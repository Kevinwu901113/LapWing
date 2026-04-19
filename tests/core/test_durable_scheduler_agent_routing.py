"""DurableScheduler._fire_agent routing — verifies agent-mode reminder
fires push a MessageEvent through the shared EventQueue (so MainLoop's
preempt rules apply) rather than calling brain.think_conversational
directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.durable_scheduler import DurableScheduler, Reminder
from src.core.event_queue import EventQueue
from src.core.events import MessageEvent, PRIORITY_USER_MESSAGE


def _reminder(mode: str = "agent") -> Reminder:
    from datetime import datetime, timezone
    return Reminder(
        reminder_id="rem1",
        content="喝水",
        due_time=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        repeat=None,
        fired=False,
        interval_minutes=None,
        time_of_day=None,
        execution_mode=mode,
    )


@pytest.mark.asyncio
async def test_fire_agent_pushes_message_event_into_queue(tmp_path):
    """agent-mode fire should enqueue a MessageEvent, not invoke brain directly."""
    send_fn = AsyncMock()
    queue = EventQueue()

    scheduler = DurableScheduler(
        db_path=tmp_path / "test.db",
        send_fn=send_fn,
        event_queue=queue,
    )

    # Consume the event off the queue and resolve its done_future so
    # _fire_agent can proceed. Simulates what MainLoop's
    # _handle_message would do.
    async def _consume_and_resolve():
        event = await queue.get()
        assert isinstance(event, MessageEvent)
        assert event.adapter == "system"
        assert event.chat_id == "__scheduler__"
        assert event.text == "[定时任务] 喝水"
        assert event.priority == PRIORITY_USER_MESSAGE  # TRUSTED, not OWNER
        assert event.done_future is not None
        event.done_future.set_result("搞定，已经提醒你喝水了")

    consumer = asyncio.create_task(_consume_and_resolve())
    await scheduler._fire_agent(_reminder())
    await consumer

    # The final reply should have been system_send-routed via send_fn.
    send_fn.assert_awaited_once()
    text, *_ = send_fn.call_args.args
    assert "搞定" in text


@pytest.mark.asyncio
async def test_fire_agent_falls_back_to_direct_brain_when_no_queue(tmp_path):
    """Without event_queue wired, _fire_agent falls back to direct brain call.
    Parity for unit tests and phase-0 contexts."""
    send_fn = AsyncMock()
    brain = MagicMock()
    brain.think_conversational = AsyncMock(return_value="直接路径也能工作")

    scheduler = DurableScheduler(
        db_path=tmp_path / "test.db",
        send_fn=send_fn,
        brain=brain,
        event_queue=None,
    )

    await scheduler._fire_agent(_reminder())

    brain.think_conversational.assert_awaited_once()
    send_fn.assert_awaited_once()
    text, *_ = send_fn.call_args.args
    assert "直接路径" in text


@pytest.mark.asyncio
async def test_fire_agent_without_queue_or_brain_falls_back_to_notify(tmp_path):
    """When neither event_queue nor brain is wired, degrade to a simple notify."""
    send_fn = AsyncMock()

    scheduler = DurableScheduler(
        db_path=tmp_path / "test.db",
        send_fn=send_fn,
        brain=None,
        event_queue=None,
    )

    await scheduler._fire_agent(_reminder())

    send_fn.assert_awaited_once()
    text, *_ = send_fn.call_args.args
    # notify mode prefix — scheduler downgraded
    assert text.startswith("⏰")


@pytest.mark.asyncio
async def test_fire_agent_event_queue_failure_falls_back_to_notify(tmp_path):
    """If MainLoop raises in done_future, scheduler surfaces a fallback notify."""
    send_fn = AsyncMock()
    queue = EventQueue()

    scheduler = DurableScheduler(
        db_path=tmp_path / "test.db",
        send_fn=send_fn,
        event_queue=queue,
    )

    async def _consume_and_fail():
        event = await queue.get()
        assert event.done_future is not None
        event.done_future.set_exception(RuntimeError("brain exploded"))

    consumer = asyncio.create_task(_consume_and_fail())
    await scheduler._fire_agent(_reminder())
    await consumer

    # Fallback notify should have been sent
    send_fn.assert_awaited_once()
    text, *_ = send_fn.call_args.args
    assert "自动执行失败" in text
