"""Tests for DurableScheduler (Phase 4 persistent reminder scheduler)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.core.durable_scheduler import (
    DurableScheduler,
    Reminder,
    _TAIPEI_TZ,
    _ensure_taipei,
    _now_taipei,
    cancel_reminder_executor,
    set_reminder_executor,
    view_reminders_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _past(minutes: int = 5) -> datetime:
    """Return a Taipei datetime N minutes in the past."""
    return _now_taipei() - timedelta(minutes=minutes)


def _future(minutes: int = 60) -> datetime:
    """Return a Taipei datetime N minutes in the future."""
    return _now_taipei() + timedelta(minutes=minutes)


async def _make_scheduler(tmp_path) -> DurableScheduler:
    """Create a DurableScheduler with an initialised temp DB."""
    db_path = str(tmp_path / "test.db")
    scheduler = DurableScheduler(db_path=db_path)
    await scheduler._init_table()
    return scheduler


def _make_ctx(scheduler: DurableScheduler) -> ToolExecutionContext:
    """Build a ToolExecutionContext with the scheduler injected."""
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services={"durable_scheduler": scheduler},
    )


# ===========================================================================
# DurableScheduler core tests
# ===========================================================================


class TestDurableSchedulerCore:
    """Core scheduling operations."""

    async def test_schedule_and_list(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        rid = await scheduler.schedule(due_time=_future(30), content="buy milk")
        assert rid.startswith("rem_")

        pending = await scheduler.list_pending()
        assert len(pending) == 1
        assert pending[0].reminder_id == rid
        assert pending[0].content == "buy milk"
        assert pending[0].fired is False

    async def test_cancel(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        rid = await scheduler.schedule(due_time=_future(30), content="cancel me")
        assert await scheduler.cancel(rid) is True

        pending = await scheduler.list_pending()
        assert len(pending) == 0

    async def test_cancel_nonexistent(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        assert await scheduler.cancel("rem_nonexistent_00000000") is False

    async def test_check_and_fire(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        rid = await scheduler.schedule(due_time=_past(5), content="overdue task")

        await scheduler.check_and_fire()

        pending = await scheduler.list_pending()
        assert len(pending) == 0

    async def test_urgency_callback(self, tmp_path):
        callback = AsyncMock()
        db_path = str(tmp_path / "test.db")
        scheduler = DurableScheduler(db_path=db_path, urgency_callback=callback)
        await scheduler._init_table()

        await scheduler.schedule(due_time=_past(5), content="urgent thing")
        await scheduler.check_and_fire()

        callback.assert_awaited_once()
        fired_reminder: Reminder = callback.call_args[0][0]
        assert fired_reminder.content == "urgent thing"

    async def test_recurring_daily(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        await scheduler.schedule(
            due_time=_past(5),
            content="daily standup",
            repeat="daily",
            time_of_day="09:00",
        )

        await scheduler.check_and_fire()

        # Original should be fired; a new one should be pending
        pending = await scheduler.list_pending()
        assert len(pending) == 1
        new = pending[0]
        assert new.content == "daily standup"
        assert new.repeat == "daily"
        # Next occurrence should be in the future
        assert new.due_time > _now_taipei()

    async def test_recurring_interval(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        await scheduler.schedule(
            due_time=_past(1),
            content="interval ping",
            repeat="interval",
            interval_minutes=45,
        )

        await scheduler.check_and_fire()

        pending = await scheduler.list_pending()
        assert len(pending) == 1
        new = pending[0]
        assert new.content == "interval ping"
        assert new.repeat == "interval"
        assert new.interval_minutes == 45
        # Next due should be ~44 minutes from now (original due + 45 min)
        assert new.due_time > _now_taipei()

    async def test_get_due_soon(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        # One in the past (overdue) — should be returned
        await scheduler.schedule(due_time=_past(2), content="overdue")
        # One due in 10 minutes — within default 30 min window
        await scheduler.schedule(due_time=_future(10), content="soon")
        # One due in 120 minutes — outside the window
        await scheduler.schedule(due_time=_future(120), content="later")

        due_soon = await scheduler.get_due_soon(minutes=30)
        contents = {r.content for r in due_soon}
        assert "overdue" in contents
        assert "soon" in contents
        assert "later" not in contents

    async def test_get_due_reminders_compat(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        await scheduler.schedule(due_time=_past(2), content="compat test")
        await scheduler.schedule(due_time=_future(10), content="soon compat")

        result = await scheduler.get_due_reminders(
            chat_id="test_chat",
            now=_now_taipei(),
            grace_seconds=1800,
            limit=3,
        )

        assert isinstance(result, list)
        assert len(result) >= 1
        # Each item should be a dict with the expected keys
        for item in result:
            assert "content" in item
            assert "next_trigger_at" in item

    async def test_fire_notify_sends_message(self, tmp_path):
        send_fn = AsyncMock()
        db_path = str(tmp_path / "test.db")
        scheduler = DurableScheduler(db_path=db_path, send_fn=send_fn)
        await scheduler._init_table()

        await scheduler.schedule(due_time=_past(1), content="hello notify")
        await scheduler.check_and_fire()

        send_fn.assert_awaited_once()
        msg = send_fn.call_args[0][0]
        assert "hello notify" in msg

    async def test_multiple_reminders_ordering(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)

        await scheduler.schedule(due_time=_future(60), content="second")
        await scheduler.schedule(due_time=_future(10), content="first")
        await scheduler.schedule(due_time=_future(120), content="third")

        pending = await scheduler.list_pending()
        assert len(pending) == 3
        assert pending[0].content == "first"
        assert pending[1].content == "second"
        assert pending[2].content == "third"


# ===========================================================================
# Tool executor tests
# ===========================================================================


class TestToolExecutors:
    """Tool executor functions for set/view/cancel reminder."""

    async def test_set_reminder_executor(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        future_time = _future(60).strftime("%Y-%m-%d %H:%M")
        req = ToolExecutionRequest(
            name="set_reminder",
            arguments={"time": future_time, "content": "test reminder"},
        )

        result = await set_reminder_executor(req, ctx)
        assert result.success is True
        assert "reminder_id" in result.payload

        pending = await scheduler.list_pending()
        assert len(pending) == 1
        assert pending[0].content == "test reminder"

    async def test_view_reminders_executor(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        await scheduler.schedule(due_time=_future(30), content="view me")

        req = ToolExecutionRequest(name="view_reminders", arguments={})
        result = await view_reminders_executor(req, ctx)
        assert result.success is True
        assert "view me" in result.payload["output"]

    async def test_view_reminders_executor_empty(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        req = ToolExecutionRequest(name="view_reminders", arguments={})
        result = await view_reminders_executor(req, ctx)
        assert result.success is True
        assert "没有" in result.payload["output"]

    async def test_cancel_reminder_executor(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        rid = await scheduler.schedule(due_time=_future(30), content="cancel this")

        req = ToolExecutionRequest(
            name="cancel_reminder",
            arguments={"reminder_id": rid},
        )
        result = await cancel_reminder_executor(req, ctx)
        assert result.success is True

        pending = await scheduler.list_pending()
        assert len(pending) == 0

    async def test_cancel_reminder_executor_not_found(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        req = ToolExecutionRequest(
            name="cancel_reminder",
            arguments={"reminder_id": "rem_fake_00000000"},
        )
        result = await cancel_reminder_executor(req, ctx)
        assert result.success is False
        assert result.reason == "not_found"

    async def test_set_reminder_missing_time(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        req = ToolExecutionRequest(
            name="set_reminder",
            arguments={"content": "no time"},
        )
        result = await set_reminder_executor(req, ctx)
        assert result.success is False
        assert result.reason == "missing_time"

    async def test_set_reminder_missing_content(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        req = ToolExecutionRequest(
            name="set_reminder",
            arguments={"time": "2026-12-25 10:00"},
        )
        result = await set_reminder_executor(req, ctx)
        assert result.success is False
        assert result.reason == "missing_content"

    async def test_set_reminder_invalid_time(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        req = ToolExecutionRequest(
            name="set_reminder",
            arguments={"time": "not-a-time", "content": "bad time"},
        )
        result = await set_reminder_executor(req, ctx)
        assert result.success is False
        assert result.reason == "invalid_time"

    async def test_set_reminder_with_repeat(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        future_time = _future(60).strftime("%Y-%m-%d %H:%M")
        req = ToolExecutionRequest(
            name="set_reminder",
            arguments={
                "time": future_time,
                "content": "daily check",
                "repeat": "daily",
                "time_of_day": "09:00",
            },
        )
        result = await set_reminder_executor(req, ctx)
        assert result.success is True

        pending = await scheduler.list_pending()
        assert len(pending) == 1
        assert pending[0].repeat == "daily"

    async def test_set_reminder_interval_missing_minutes(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        future_time = _future(60).strftime("%Y-%m-%d %H:%M")
        req = ToolExecutionRequest(
            name="set_reminder",
            arguments={
                "time": future_time,
                "content": "interval no minutes",
                "repeat": "interval",
            },
        )
        result = await set_reminder_executor(req, ctx)
        assert result.success is False
        assert result.reason == "invalid_interval_minutes"

    async def test_scheduler_unavailable(self, tmp_path):
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={},  # no scheduler
        )

        req = ToolExecutionRequest(
            name="set_reminder",
            arguments={"time": "2026-12-25 10:00", "content": "test"},
        )
        result = await set_reminder_executor(req, ctx)
        assert result.success is False
        assert result.reason == "scheduler_unavailable"

    async def test_cancel_missing_reminder_id(self, tmp_path):
        scheduler = await _make_scheduler(tmp_path)
        ctx = _make_ctx(scheduler)

        req = ToolExecutionRequest(
            name="cancel_reminder",
            arguments={},
        )
        result = await cancel_reminder_executor(req, ctx)
        assert result.success is False
        assert result.reason == "missing_reminder_id"


# ===========================================================================
# list_fired tests
# ===========================================================================


import aiosqlite  # noqa: E402


@pytest.mark.asyncio
async def test_list_fired_returns_only_fired(tmp_path):
    db = tmp_path / "rem.db"
    scheduler = DurableScheduler(db_path=db)
    await scheduler._init_table()
    # Schedule one and manually mark it fired + one unfired for contrast.
    from datetime import timezone
    rid1 = await scheduler.schedule(
        due_time=datetime.now(tz=timezone.utc),
        content="done",
    )
    rid2 = await scheduler.schedule(
        due_time=datetime.now(tz=timezone.utc),
        content="pending",
    )
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE reminders_v2 SET fired = 1 WHERE reminder_id = ?", (rid1,))
        await conn.commit()

    rows = await scheduler.list_fired(limit=10)

    assert len(rows) == 1
    assert rows[0]["reminder_id"] == rid1
    assert rows[0]["content"] == "done"


@pytest.mark.asyncio
async def test_list_fired_orders_newest_first(tmp_path):
    db = tmp_path / "rem.db"
    scheduler = DurableScheduler(db_path=db)
    await scheduler._init_table()
    from datetime import datetime, timezone
    # Three reminders with distinct due_times
    r_old = await scheduler.schedule(
        due_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        content="old",
    )
    r_mid = await scheduler.schedule(
        due_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        content="mid",
    )
    r_new = await scheduler.schedule(
        due_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        content="new",
    )
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE reminders_v2 SET fired = 1")
        await conn.commit()

    rows = await scheduler.list_fired(limit=10)

    assert [r["reminder_id"] for r in rows] == [r_new, r_mid, r_old]


@pytest.mark.asyncio
async def test_list_fired_before_ts_cutoff_is_correct(tmp_path):
    """Regression: over-fetch limit*2 used to silently under-return
    when all nearest rows were above the cutoff. Cutoff now applied in SQL."""
    db = tmp_path / "rem.db"
    scheduler = DurableScheduler(db_path=db)
    await scheduler._init_table()
    from datetime import datetime, timezone
    # Five reminders above cutoff, five below. Cutoff sits between Feb and Mar.
    above_ids = []
    for day in (10, 11, 12, 13, 14):
        rid = await scheduler.schedule(
            due_time=datetime(2026, 3, day, tzinfo=timezone.utc),
            content=f"above_{day}",
        )
        above_ids.append(rid)
    below_ids = []
    for day in (1, 2, 3, 4, 5):
        rid = await scheduler.schedule(
            due_time=datetime(2026, 2, day, tzinfo=timezone.utc),
            content=f"below_{day}",
        )
        below_ids.append(rid)
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE reminders_v2 SET fired = 1")
        await conn.commit()

    cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
    rows = await scheduler.list_fired(before_ts=cutoff, limit=3)

    # Must get 3 rows — all from below the cutoff. The bug was returning 0
    # because limit*2 (=6) of the top-newest rows were all >= cutoff.
    assert len(rows) == 3
    returned_ids = [r["reminder_id"] for r in rows]
    assert all(rid in below_ids for rid in returned_ids), (
        f"Returned ids should all be below-cutoff: {returned_ids}"
    )


@pytest.mark.asyncio
async def test_list_fired_limit_enforced(tmp_path):
    db = tmp_path / "rem.db"
    scheduler = DurableScheduler(db_path=db)
    await scheduler._init_table()
    from datetime import datetime, timezone
    for i in range(10):
        await scheduler.schedule(
            due_time=datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
            content=f"r{i}",
        )
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE reminders_v2 SET fired = 1")
        await conn.commit()

    rows = await scheduler.list_fired(limit=3)

    assert len(rows) == 3
