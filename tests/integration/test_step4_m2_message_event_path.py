"""Integration test for Step 4 M2 — message path goes through MainLoop.

The producer (an adapter or an HTTP route) puts a MessageEvent on the
queue; MainLoop dequeues, dispatches, and drives a fake brain. The
fake records the call so we can assert kwarg shape; the producer
awaits done_future and gets the brain's reply.

This is the contract M2 establishes:
  producer → EventQueue → MainLoop → brain.think_conversational
"""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.integration

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.authority_gate import AuthLevel
from src.core.event_queue import EventQueue
from src.core.events import MessageEvent
from src.core.main_loop import MainLoop


@pytest.mark.asyncio
async def test_message_event_routed_to_brain_with_correct_kwargs():
    """Producer enqueues; MainLoop calls brain.think_conversational with
    the kwargs unpacked from the event."""
    brain = AsyncMock()
    brain.think_conversational = AsyncMock(return_value="hello back")

    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())

    sent = []

    async def send_fn(text):
        sent.append(text)

    done = asyncio.get_running_loop().create_future()
    event = MessageEvent.from_message(
        chat_id="kev",
        user_id="kev",
        text="hi",
        adapter="qq",
        send_fn=send_fn,
        auth_level=int(AuthLevel.OWNER),
        done_future=done,
    )
    await q.put(event)

    reply = await asyncio.wait_for(done, timeout=2.0)
    assert reply == "hello back"

    brain.think_conversational.assert_awaited_once()
    kwargs = brain.think_conversational.call_args.kwargs
    assert kwargs["chat_id"] == "kev"
    assert kwargs["user_message"] == "hi"
    assert kwargs["adapter"] == "qq"
    assert kwargs["user_id"] == "kev"
    assert kwargs["send_fn"] is not None
    assert kwargs["send_fn"] is not send_fn
    await kwargs["send_fn"]("wrapped hello")
    assert sent == ["wrapped hello"]

    await loop.stop()
    # Unblock loop's queue.get so run() exits.
    await q.put(MessageEvent.from_message(
        chat_id="x", user_id="x", text="x",
        adapter="x", send_fn=send_fn, auth_level=int(AuthLevel.GUEST),
    ))
    await asyncio.wait_for(runner, timeout=1.0)


@pytest.mark.asyncio
async def test_brain_exception_propagates_to_done_future():
    brain = AsyncMock()
    brain.think_conversational = AsyncMock(side_effect=RuntimeError("brain went boom"))

    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())

    done = asyncio.get_running_loop().create_future()
    await q.put(MessageEvent.from_message(
        chat_id="x", user_id="x", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
        done_future=done,
    ))

    reply = await asyncio.wait_for(done, timeout=2.0)
    assert reply == MainLoop.FOREGROUND_EXCEPTION_REPLY

    await loop.stop()
    await q.put(MessageEvent.from_message(
        chat_id="z", user_id="z", text="z",
        adapter="z", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
    ))
    await asyncio.wait_for(runner, timeout=1.0)


@pytest.mark.asyncio
async def test_fire_and_forget_event_no_done_future():
    """QQ private/group producers don't await — they put and move on."""
    brain = AsyncMock()
    brain.think_conversational = AsyncMock(return_value="ok")

    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())

    await q.put(MessageEvent.from_message(
        chat_id="grp", user_id="bob", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.TRUSTED),
    ))
    # Give MainLoop a chance to dispatch.
    await asyncio.sleep(0.05)
    brain.think_conversational.assert_awaited_once()

    await loop.stop()
    await q.put(MessageEvent.from_message(
        chat_id="x", user_id="x", text="x",
        adapter="x", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
    ))
    await asyncio.wait_for(runner, timeout=1.0)


@pytest.mark.asyncio
async def test_owner_message_dispatches_before_user_message():
    """Two events queued back-to-back; OWNER-priority should run first."""
    brain = AsyncMock()
    seen: list[str] = []

    async def fake_think(*, chat_id, **_kwargs):
        seen.append(chat_id)
        return ""

    brain.think_conversational = fake_think

    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())
    # Pre-fill queue *before* loop starts dispatching.
    await q.put(MessageEvent.from_message(
        chat_id="grp", user_id="bob", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
    ))
    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="urgent",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))

    # Wait for both to be processed.
    for _ in range(50):
        if len(seen) >= 2:
            break
        await asyncio.sleep(0.02)

    assert seen[0] == "kev", f"OWNER should run first; got {seen}"

    await loop.stop()
    await q.put(MessageEvent.from_message(
        chat_id="x", user_id="x", text="x",
        adapter="x", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
    ))
    await asyncio.wait_for(runner, timeout=1.0)


async def _noop(*_a, **_kw):  # pragma: no cover
    return None
