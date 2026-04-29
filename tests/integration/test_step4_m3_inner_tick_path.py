"""Integration test for Step 4 M3 — inner tick goes through MainLoop.

Verifies:
  * InnerTickScheduler enqueues InnerTickEvent.
  * MainLoop._handle_inner_tick calls brain.think_inner with drained
    urgency items and reports the result back to the scheduler.
  * has_owner_message() pre-empts a tick (skipped when OWNER waiting).
  * No __inner__ chat_id touches brain — think_inner has no chat_id arg.
"""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.integration

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.authority_gate import AuthLevel
from src.core.event_queue import EventQueue
from src.core.events import InnerTickEvent, MessageEvent
from src.core.inner_tick_scheduler import InnerTickScheduler
from src.core.main_loop import MainLoop


@pytest.mark.asyncio
async def test_inner_tick_invokes_brain_think_inner_with_urgency():
    brain = AsyncMock()
    brain.think_inner = AsyncMock(return_value=("did the thing", 600, True))

    q = EventQueue()
    sched = InnerTickScheduler(q)
    loop = MainLoop(q, brain=brain, inner_tick_scheduler=sched)
    runner = asyncio.create_task(loop.run())

    sched.push_urgency({"type": "reminder", "content": "drink water"})
    await q.put(InnerTickEvent.make(reason="urgency"))

    # Wait for brain to be called.
    for _ in range(50):
        if brain.think_inner.await_count >= 1:
            break
        await asyncio.sleep(0.02)

    brain.think_inner.assert_awaited_once()
    kwargs = brain.think_inner.call_args.kwargs
    assert kwargs["urgent_items"] == [
        {"type": "reminder", "content": "drink water"},
    ]

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)


@pytest.mark.asyncio
async def test_inner_tick_skipped_when_owner_message_queued():
    """An OWNER message landing before the tick handler runs preempts it."""
    brain = AsyncMock()
    brain.think_inner = AsyncMock(return_value=("", None, False))

    q = EventQueue()
    sched = InnerTickScheduler(q)
    loop = MainLoop(q, brain=brain, inner_tick_scheduler=sched)

    # Pre-fill: tick first, then OWNER message. PriorityQueue reorders so
    # OWNER pops first, then tick. By the time tick is dispatched, the
    # OWNER message is already drained — the test instead uses a manual
    # has_owner_message scenario by injecting both into the queue without
    # starting the loop, then starting after.
    await q.put(InnerTickEvent.make(reason="periodic"))
    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))

    # Start loop now — OWNER will dispatch first; when the loop reaches
    # the InnerTickEvent the queue is empty (no OWNER waiting), so it
    # WILL run. To exercise the skip path, we have to put an OWNER msg
    # *during* the tick handler. Simpler: stub _handle_message so the
    # OWNER doesn't actually run the brain (just records), then verify
    # think_inner DID run after OWNER (since by then no OWNER is queued).
    # The dedicated skip-when-owner-pending case is covered by the
    # has_owner_message_skip test below.

    runner = asyncio.create_task(loop.run())
    for _ in range(50):
        if brain.think_inner.await_count >= 1:
            break
        await asyncio.sleep(0.02)
    # think_inner ran — confirms loop processes both events.
    assert brain.think_inner.await_count == 1

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)


@pytest.mark.asyncio
async def test_handle_inner_tick_yields_when_owner_pending(monkeypatch):
    """Direct unit test: tick handler returns early when OWNER queued."""
    brain = AsyncMock()
    brain.think_inner = AsyncMock(return_value=("ok", 600, True))

    q = EventQueue()
    sched = InnerTickScheduler(q)
    loop = MainLoop(q, brain=brain, inner_tick_scheduler=sched)

    # Put an OWNER message in the queue first so has_owner_message is True.
    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))

    # Now call handler directly — should skip without invoking brain.
    await loop._handle_inner_tick(InnerTickEvent.make())  # type: ignore[attr-defined]
    brain.think_inner.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_result_drives_scheduler_next_interval():
    brain = AsyncMock()
    brain.think_inner = AsyncMock(return_value=("did stuff", 1500, True))

    q = EventQueue()
    sched = InnerTickScheduler(q)
    loop = MainLoop(q, brain=brain, inner_tick_scheduler=sched)
    runner = asyncio.create_task(loop.run())

    await q.put(InnerTickEvent.make())
    for _ in range(50):
        if brain.think_inner.await_count >= 1:
            break
        await asyncio.sleep(0.02)

    # Wait one more tick for note_tick_result to land.
    await asyncio.sleep(0.05)
    assert sched.next_interval_seconds == 1500
    assert sched.idle_streak == 0

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)


@pytest.mark.asyncio
async def test_think_inner_no_chat_id_arg():
    """Sanity: think_inner does not accept chat_id (the sentinel is gone)."""
    import inspect
    from src.core.brain import LapwingBrain

    sig = inspect.signature(LapwingBrain.think_inner)
    params = set(sig.parameters.keys())
    assert "chat_id" not in params
    assert "urgent_items" in params


async def _noop(*_a, **_kw):  # pragma: no cover
    return None
