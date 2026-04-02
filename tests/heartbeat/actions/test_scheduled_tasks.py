"""tests/heartbeat/actions/test_scheduled_tasks.py — ScheduledTasksAction 测试。"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.heartbeat import SenseContext


def _make_ctx() -> SenseContext:
    now = datetime.now(timezone.utc)
    return SenseContext(
        beat_type="minute",
        now=now,
        last_interaction=now,
        silence_hours=0,
        user_facts_summary="",
        recent_memory_summary="",
        chat_id="test_chat",
    )


def _make_brain():
    brain = MagicMock()
    brain.think = AsyncMock(return_value="已完成定时任务")
    return brain


@pytest.fixture
def tasks_path(tmp_path):
    p = tmp_path / "scheduled_tasks.json"
    with patch("src.heartbeat.actions.scheduled_tasks.SCHEDULED_TASKS_PATH", p):
        yield p


# ─── _should_run ──────────────────────────────────────────────────

class TestShouldRun:
    def test_daily_in_window(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 23, 0, 0)
        task = {
            "schedule_parsed": {"type": "daily", "time": "23:00"},
            "last_run": None,
            "enabled": True,
        }
        assert _should_run(task, now) is True

    def test_daily_outside_window(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 14, 0, 0)
        task = {
            "schedule_parsed": {"type": "daily", "time": "23:00"},
            "last_run": None,
            "enabled": True,
        }
        assert _should_run(task, now) is False

    def test_daily_already_ran_today(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 23, 0, 0)
        task = {
            "schedule_parsed": {"type": "daily", "time": "23:00"},
            "last_run": "2026-04-01T22:59:00",
            "enabled": True,
        }
        assert _should_run(task, now) is False

    def test_interval_first_run(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 12, 0, 0)
        task = {
            "schedule_parsed": {"type": "interval", "hours": 2},
            "last_run": None,
            "enabled": True,
        }
        assert _should_run(task, now) is True

    def test_interval_not_yet(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 12, 0, 0)
        task = {
            "schedule_parsed": {"type": "interval", "hours": 2},
            "last_run": "2026-04-01T11:30:00",  # 30 分钟前
            "enabled": True,
        }
        assert _should_run(task, now) is False

    def test_interval_ready(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 13, 31, 0)
        task = {
            "schedule_parsed": {"type": "interval", "hours": 2},
            "last_run": "2026-04-01T11:30:00",
        }
        assert _should_run(task, now) is True

    def test_once_in_window(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 8, 1, 0)
        task = {
            "schedule_parsed": {"type": "once", "datetime": "2026-04-01 08:00"},
            "last_run": None,
            "enabled": True,
        }
        assert _should_run(task, now) is True

    def test_once_already_ran(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 8, 1, 0)
        task = {
            "schedule_parsed": {"type": "once", "datetime": "2026-04-01 08:00"},
            "last_run": "2026-04-01T08:00:00",
            "enabled": True,
        }
        assert _should_run(task, now) is False

    def test_once_past_target_fires_even_hours_later(self):
        # once 任务没有窗口限制：只要过了目标时间且未运行，就触发（系统宕机恢复后补跑）
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 10, 0, 0)
        task = {
            "schedule_parsed": {"type": "once", "datetime": "2026-04-01 08:00"},
            "last_run": None,
        }
        assert _should_run(task, now) is True

    def test_interval_too_short(self):
        from src.heartbeat.actions.scheduled_tasks import _should_run
        now = datetime(2026, 4, 1, 12, 0, 0)
        task = {
            "schedule_parsed": {"type": "interval", "minutes": 0},
            "last_run": None,
        }
        assert _should_run(task, now) is False


# ─── ScheduledTasksAction ─────────────────────────────────────────

class TestScheduledTasksAction:
    async def test_no_file(self, tasks_path):
        from src.heartbeat.actions.scheduled_tasks import ScheduledTasksAction
        action = ScheduledTasksAction()
        # 文件不存在时不应报错
        await action.execute(_make_ctx(), _make_brain(), AsyncMock())

    async def test_executes_due_task(self, tasks_path):
        from src.heartbeat.actions.scheduled_tasks import ScheduledTasksAction
        now = datetime.now()
        task = {
            "id": "sched_test1",
            "schedule_raw": "每隔1分钟",
            "schedule_parsed": {"type": "interval", "minutes": 1},
            "task": "测试任务",
            "repeat": True,
            "last_run": (now - timedelta(minutes=2)).isoformat(),
            "enabled": True,
        }
        tasks_path.write_text(json.dumps([task]), encoding="utf-8")

        action = ScheduledTasksAction()
        brain = _make_brain()
        send_fn = AsyncMock()
        await action.execute(_make_ctx(), brain, send_fn)
        brain.think.assert_called_once()
        send_fn.assert_called_once()

    async def test_disables_single_task_after_run(self, tasks_path):
        from src.heartbeat.actions.scheduled_tasks import ScheduledTasksAction
        now = datetime.now()
        task = {
            "id": "sched_once1",
            "schedule_raw": "2026-04-01 08:00",
            "schedule_parsed": {"type": "once", "datetime": now.strftime("%Y-%m-%d %H:%M")},
            "task": "单次任务",
            "repeat": False,
            "last_run": None,
            "enabled": True,
        }
        tasks_path.write_text(json.dumps([task]), encoding="utf-8")

        action = ScheduledTasksAction()
        await action.execute(_make_ctx(), _make_brain(), AsyncMock())

        # 单次任务执行后应从文件中移除
        remaining = json.loads(tasks_path.read_text(encoding="utf-8"))
        assert remaining == []

    async def test_skips_disabled_task(self, tasks_path):
        from src.heartbeat.actions.scheduled_tasks import ScheduledTasksAction
        now = datetime.now()
        task = {
            "id": "sched_disabled",
            "schedule_parsed": {"type": "interval", "minutes": 1},
            "task": "禁用的任务",
            "repeat": True,
            "last_run": (now - timedelta(minutes=5)).isoformat(),
            "enabled": False,
        }
        tasks_path.write_text(json.dumps([task]), encoding="utf-8")

        action = ScheduledTasksAction()
        brain = _make_brain()
        await action.execute(_make_ctx(), brain, AsyncMock())
        brain.think.assert_not_called()
