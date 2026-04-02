"""tests/tools/test_schedule_task.py — 定时任务工具测试（数据库版）。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.shell_executor import ShellResult


def _make_context(chat_id: str = "test_chat", reminder_id: int = 1) -> ToolExecutionContext:
    memory = AsyncMock()
    memory.add_reminder = AsyncMock(return_value=reminder_id)
    memory.list_reminders = AsyncMock(return_value=[])
    memory.cancel_reminder = AsyncMock(return_value=True)
    return ToolExecutionContext(
        execute_shell=AsyncMock(return_value=ShellResult(stdout="", stderr="", return_code=0)),
        shell_default_cwd="/tmp",
        chat_id=chat_id,
        memory=memory,
    )


def _make_request(name: str, **kwargs) -> ToolExecutionRequest:
    return ToolExecutionRequest(name=name, arguments=kwargs)


class TestScheduleTaskExecutor:
    async def test_delay_creates_reminder(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="下楼", trigger_type="delay", delay_minutes=5)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is True
        assert "已设置" in result.payload["output"]
        ctx.memory.add_reminder.assert_called_once()
        call_kwargs = ctx.memory.add_reminder.call_args
        assert call_kwargs.kwargs["recurrence_type"] == "once"
        assert call_kwargs.kwargs["content"] == "下楼"

    async def test_daily_creates_reminder(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="查邮件", trigger_type="daily", time_of_day="09:00")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is True
        ctx.memory.add_reminder.assert_called_once()
        call_kwargs = ctx.memory.add_reminder.call_args
        assert call_kwargs.kwargs["recurrence_type"] == "daily"
        assert call_kwargs.kwargs["time_of_day"] == "09:00"

    async def test_interval_creates_reminder(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="看新闻", trigger_type="interval", interval_minutes=120)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is True
        call_kwargs = ctx.memory.add_reminder.call_args
        assert call_kwargs.kwargs["recurrence_type"] == "interval"
        assert call_kwargs.kwargs["interval_minutes"] == 120

    async def test_once_creates_reminder(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="交文档", trigger_type="once", once_datetime="2026-12-01 15:00")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is True

    async def test_missing_content(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", trigger_type="delay", delay_minutes=5)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_invalid_trigger_type(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="test", trigger_type="invalid")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_delay_zero_rejected(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="test", trigger_type="delay", delay_minutes=0)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_daily_missing_time(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="test", trigger_type="daily")
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_no_memory_fails_gracefully(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
        )
        req = _make_request("schedule_task", content="test", trigger_type="delay", delay_minutes=5)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_db_failure_returns_error(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context(reminder_id=0)
        req = _make_request("schedule_task", content="test", trigger_type="delay", delay_minutes=5)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is False

    async def test_human_readable_delay(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="休息", trigger_type="delay", delay_minutes=120)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is True
        assert "2小时后" in result.payload["output"]

    async def test_human_readable_interval(self):
        from src.tools.schedule_task import _execute_schedule_task
        ctx = _make_context()
        req = _make_request("schedule_task", content="喝水", trigger_type="interval", interval_minutes=30)
        result = await _execute_schedule_task(req, ctx)
        assert result.success is True
        assert "30分钟" in result.payload["output"]


class TestListScheduledTasksExecutor:
    async def test_empty(self):
        from src.tools.schedule_task import _execute_list_scheduled_tasks
        ctx = _make_context()
        req = _make_request("list_scheduled_tasks")
        result = await _execute_list_scheduled_tasks(req, ctx)
        assert result.success is True
        assert "没有" in result.payload["output"]

    async def test_lists_reminders(self):
        from src.tools.schedule_task import _execute_list_scheduled_tasks
        ctx = _make_context()
        ctx.memory.list_reminders = AsyncMock(return_value=[
            {"id": 1, "content": "下楼", "recurrence_type": "once", "next_trigger_at": "2026-04-02T18:50:00+00:00"},
            {"id": 2, "content": "查邮件", "recurrence_type": "daily", "next_trigger_at": "2026-04-03T09:00:00+00:00"},
        ])
        req = _make_request("list_scheduled_tasks")
        result = await _execute_list_scheduled_tasks(req, ctx)
        assert result.success is True
        assert "下楼" in result.payload["output"]
        assert "查邮件" in result.payload["output"]

    async def test_no_memory_fails_gracefully(self):
        from src.tools.schedule_task import _execute_list_scheduled_tasks
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
        )
        req = _make_request("list_scheduled_tasks")
        result = await _execute_list_scheduled_tasks(req, ctx)
        assert result.success is False


class TestCancelScheduledTaskExecutor:
    async def test_cancel_existing(self):
        from src.tools.schedule_task import _execute_cancel_scheduled_task
        ctx = _make_context()
        req = _make_request("cancel_scheduled_task", reminder_id=1)
        result = await _execute_cancel_scheduled_task(req, ctx)
        assert result.success is True
        ctx.memory.cancel_reminder.assert_called_once_with("test_chat", 1)

    async def test_cancel_nonexistent(self):
        from src.tools.schedule_task import _execute_cancel_scheduled_task
        ctx = _make_context()
        ctx.memory.cancel_reminder = AsyncMock(return_value=False)
        req = _make_request("cancel_scheduled_task", reminder_id=999)
        result = await _execute_cancel_scheduled_task(req, ctx)
        assert result.success is False

    async def test_missing_id(self):
        from src.tools.schedule_task import _execute_cancel_scheduled_task
        ctx = _make_context()
        req = _make_request("cancel_scheduled_task")
        result = await _execute_cancel_scheduled_task(req, ctx)
        assert result.success is False

    async def test_invalid_id_type(self):
        from src.tools.schedule_task import _execute_cancel_scheduled_task
        ctx = _make_context()
        req = _make_request("cancel_scheduled_task", reminder_id="not-an-int")
        result = await _execute_cancel_scheduled_task(req, ctx)
        assert result.success is False

    async def test_no_memory_fails_gracefully(self):
        from src.tools.schedule_task import _execute_cancel_scheduled_task
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
        )
        req = _make_request("cancel_scheduled_task", reminder_id=1)
        result = await _execute_cancel_scheduled_task(req, ctx)
        assert result.success is False
