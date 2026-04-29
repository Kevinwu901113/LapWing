"""Integration test for Step 4 M4 — OWNER instant interrupt.

Spec covers four scenarios:

  1. Inner tick running, OWNER message arrives → tick cancelled, OWNER
     handled, INTERRUPTED trajectory entry persisted.
  2. Non-OWNER conversation running, OWNER arrives → cancelled, OWNER
     handled, INTERRUPTED entry persisted.
  3. No in-flight task, OWNER arrives → handled normally (no-op interrupt).
  4. Inner tick handler self-yields when has_owner_message() is True
     (covered in M3 integration tests; replicated here for the M4 picture).

We use a fake brain that exposes synchronous-feeling cancellation
points: a long-running coroutine that awaits an asyncio.Event we
control. That lets the test deterministically place a cancellation
during the brain call.
"""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.integration

import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest

from src.core.authority_gate import AuthLevel
from src.core.event_queue import EventQueue
from src.core.events import InnerTickEvent, MessageEvent
from src.core.main_loop import MainLoop


class _FakeBrainBase:
    def __init__(self):
        self.persisted_interruptions: list[dict] = []
        self.think_started = asyncio.Event()
        self.think_release = asyncio.Event()

    async def _persist_interrupted(self, *, chat_id, partial_text, reason, adapter="", kind="conversational"):
        self.persisted_interruptions.append({
            "chat_id": chat_id,
            "partial_text": partial_text,
            "reason": reason,
            "kind": kind,
        })


class _FakeBrainConversational(_FakeBrainBase):
    async def think_conversational(
        self, *, chat_id, user_message, send_fn, typing_fn, status_callback,
        adapter, user_id, images,
    ):
        self.think_started.set()
        try:
            await self.think_release.wait()
            return f"replied to {chat_id}"
        except asyncio.CancelledError:
            await self._persist_interrupted(
                chat_id=chat_id, partial_text="streamed-so-far",
                reason="owner_message_preempt", adapter=adapter, kind="conversational",
            )
            raise


class _FakeBrainInner(_FakeBrainBase):
    async def think_conversational(
        self, *, chat_id, user_message, send_fn, typing_fn, status_callback,
        adapter, user_id, images,
    ):
        self.think_started.set()
        return f"replied to {chat_id}"

    async def think_inner(self, *, urgent_items=None, timeout_seconds=120):
        self.think_started.set()
        try:
            await self.think_release.wait()
            return ("inner thought", 600, True)
        except asyncio.CancelledError:
            await self._persist_interrupted(
                chat_id="_inner_tick", partial_text="",
                reason="owner_message_preempt", kind="inner",
            )
            raise


# ── Scenario 1: inner tick running, OWNER preempts ───────────────────


@pytest.mark.asyncio
async def test_scenario1_inner_tick_preempted_by_owner():
    brain = _FakeBrainInner()
    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())

    await q.put(InnerTickEvent.make(reason="periodic"))
    # Wait for inner tick handler to start.
    await asyncio.wait_for(brain.think_started.wait(), timeout=2.0)
    brain.think_started.clear()

    # OWNER message arrives mid-tick.
    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="urgent",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))

    # Wait for OWNER's think_conversational to start.
    await asyncio.wait_for(brain.think_started.wait(), timeout=2.0)
    brain.think_release.set()

    # Wait for OWNER to finish.
    await asyncio.sleep(0.05)

    assert len(brain.persisted_interruptions) == 1
    interrupt = brain.persisted_interruptions[0]
    assert interrupt["kind"] == "inner"
    assert interrupt["reason"] == "owner_message_preempt"

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)


# ── Scenario 2: non-OWNER conversation preempted by OWNER ────────────


@pytest.mark.asyncio
async def test_scenario2_user_conversation_preempted_by_owner():
    brain = _FakeBrainConversational()
    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())

    # Non-OWNER message arrives first.
    await q.put(MessageEvent.from_message(
        chat_id="grp", user_id="bob", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
    ))
    await asyncio.wait_for(brain.think_started.wait(), timeout=2.0)
    brain.think_started.clear()

    # OWNER arrives mid-conversation.
    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hey",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))

    await asyncio.wait_for(brain.think_started.wait(), timeout=2.0)
    brain.think_release.set()
    await asyncio.sleep(0.05)

    assert len(brain.persisted_interruptions) == 1
    interrupt = brain.persisted_interruptions[0]
    assert interrupt["chat_id"] == "grp"
    assert interrupt["kind"] == "conversational"
    assert interrupt["partial_text"] == "streamed-so-far"

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)


# ── Scenario 3: no in-flight task, OWNER handled normally ────────────


@pytest.mark.asyncio
async def test_scenario3_owner_with_no_inflight_task():
    brain = _FakeBrainConversational()
    brain.think_release.set()  # Don't block.
    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())

    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))
    await asyncio.sleep(0.05)
    # No interruption persisted; handler ran cleanly.
    assert brain.persisted_interruptions == []

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)


# ── Scenario 4: handler self-yield when OWNER pending ────────────────


@pytest.mark.asyncio
async def test_scenario4_inner_tick_self_yield_when_owner_pending():
    brain = _FakeBrainInner()
    q = EventQueue()
    loop = MainLoop(q, brain=brain)

    # Pre-fill OWNER message into queue, then call _handle_inner_tick
    # directly. has_owner_message() is True → handler exits without
    # invoking brain.think_inner.
    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))
    await loop._handle_inner_tick(InnerTickEvent.make())  # type: ignore[attr-defined]

    assert not brain.think_started.is_set()
    assert brain.persisted_interruptions == []


async def _noop(*_a, **_kw):  # pragma: no cover
    return None
