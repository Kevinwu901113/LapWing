"""tests/tools/test_schedule_task.py — 自主调度工具测试。"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock

from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.shell_executor import ShellResult


def _make_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(return_value=ShellResult(stdout="", stderr="", return_code=0)),
        shell_default_cwd="/tmp",
    )


def _make_request(name: str, **kwargs) -> ToolExecutionRequest:
    return ToolExecutionRequest(name=name, arguments=kwargs)


@pytest.fixture
def tasks_path(tmp_path):
    p = tmp_path / "scheduled_tasks.json"
    with patch("src.tools.schedule_task.SCHEDULED_TASKS_PATH", p):
        yield p


# ─── _parse_schedule ──────────────────────────────────────────────

class TestParseSchedule:
    def test_daily(self):
        from src.tools.schedule_task import _parse_schedule
        result = _parse_schedule("每天23:00")
        assert result == {"type": "daily", "time": "23:00"}

    def test_daily_single_digit_hour(self):
        from src.tools.schedule_task import _parse_schedule
        result = _parse_schedule("每天8:30")
        assert result == {"type": "daily", "time": "08:30"}

    def test_interval_hours(self):
        from src.tools.schedule_task import _parse_schedule
        result = _parse_schedule("每隔2小时")
        assert result == {"type": "interval", "hours": 2}

    def test_interval_minutes(self):
        from src.tools.schedule_task import _parse_schedule
        result = _parse_schedule("每隔30分钟")
        assert result == {"type": "interval", "minutes": 30}

    def test_once_datetime(self):
        from src.tools.schedule_task import _parse_schedule
        result = _parse_schedule("2026-04-01 08:00")
        assert result == {"type": "once", "datetime": "2026-04-01 08:00"}

    def test_tomorrow(self):
        from src.tools.schedule_task import _parse_schedule
        result = _parse_schedule("明天09:00")
        assert result is not None
        assert result["type"] == "once"
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert result["datetime"].startswith(tomorrow)

    def test_day_after_tomorrow(self):
        from src.tools.schedule_task import _parse_schedule
        result = _parse_schedule("后天10:00")
        assert result is not None
        assert result["type"] == "once"
        day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        assert result["datetime"].startswith(day_after)

    def test_invalid_returns_none(self):
        from src.tools.schedule_task import _parse_schedule
        assert _parse_schedule("random text") is None
        assert _parse_schedule("") is None
        assert _parse_schedule("在某个时候执行") is None


# ─── _load_tasks / _save_tasks ────────────────────────────────────

class TestTaskFileIO:
    def test_load_empty_when_missing(self, tasks_path):
        from src.tools.schedule_task import _load_tasks
        assert _load_tasks() == []

    def test_save_and_load(self, tasks_path):
        from src.tools.schedule_task import _load_tasks, _save_tasks
        tasks = [{"id": "sched_abc", "task": "test", "enabled": True}]
        _save_tasks(tasks)
        loaded = _load_tasks()
        assert loaded == tasks

    def test_load_corrupt_file_returns_empty(self, tasks_path):
        from src.tools.schedule_task import _load_tasks
        tasks_path.write_text("not json", encoding="utf-8")
        assert _load_tasks() == []


# ─── schedule_task executor ───────────────────────────────────────

class TestScheduleTaskExecutor:
    async def test_creates_task(self, tasks_path):
        from src.tools.schedule_task import _execute_schedule_task, _load_tasks
        ctx = _make_context()
        req = _make_request("schedule_task", schedule="每天23:00", task_description="每晚自省")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is True
        assert "已安排" in result.payload["output"]
        tasks = _load_tasks()
        assert len(tasks) == 1
        assert tasks[0]["task"] == "每晚自省"

    async def test_invalid_schedule(self, tasks_path):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", schedule="invalid time", task_description="test")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False
        assert "无法解析" in result.reason

    async def test_missing_schedule(self, tasks_path):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", task_description="test")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_missing_task_description(self, tasks_path):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", schedule="每天10:00")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_repeat_flag(self, tasks_path):
        from src.tools.schedule_task import _execute_schedule_task, _load_tasks
        ctx = _make_context()
        req = _make_request("schedule_task", schedule="每天10:00", task_description="test", repeat=False)
        await _execute_schedule_task(req, ctx)
        tasks = _load_tasks()
        assert tasks[0]["repeat"] is False


# ─── list_scheduled_tasks executor ───────────────────────────────

class TestListScheduledTasksExecutor:
    async def test_empty(self, tasks_path):
        from src.tools.schedule_task import _execute_list_scheduled_tasks
        ctx = _make_context()
        req = _make_request("list_scheduled_tasks")
        result = await _execute_list_scheduled_tasks(req, ctx)
        assert result.success is True
        assert "没有" in result.payload["output"]

    async def test_lists_tasks(self, tasks_path):
        from src.tools.schedule_task import _execute_schedule_task, _execute_list_scheduled_tasks
        ctx = _make_context()
        await _execute_schedule_task(
            _make_request("schedule_task", schedule="每天08:00", task_description="早间报告"), ctx
        )
        result = await _execute_list_scheduled_tasks(_make_request("list_scheduled_tasks"), ctx)
        assert result.success is True
        assert "早间报告" in result.payload["output"]


# ─── cancel_scheduled_task executor ──────────────────────────────

class TestCancelScheduledTaskExecutor:
    async def test_cancel_existing(self, tasks_path):
        from src.tools.schedule_task import _execute_schedule_task, _execute_cancel_scheduled_task, _load_tasks
        ctx = _make_context()
        create_result = await _execute_schedule_task(
            _make_request("schedule_task", schedule="每天10:00", task_description="要取消的任务"), ctx
        )
        task_id = create_result.payload["task_id"]
        cancel_result = await _execute_cancel_scheduled_task(
            _make_request("cancel_scheduled_task", task_id=task_id), ctx
        )
        assert cancel_result.success is True
        assert _load_tasks() == []

    async def test_cancel_nonexistent(self, tasks_path):
        from src.tools.schedule_task import _execute_cancel_scheduled_task
        ctx = _make_context()
        req = _make_request("cancel_scheduled_task", task_id="sched_nonexistent")
        result = await _execute_cancel_scheduled_task(req, ctx)
        assert result.success is False
        assert "未找到" in result.reason

    async def test_missing_task_id(self, tasks_path):
        from src.tools.schedule_task import _execute_cancel_scheduled_task
        ctx = _make_context()
        req = _make_request("cancel_scheduled_task")
        result = await _execute_cancel_scheduled_task(req, ctx)
        assert result.success is False
