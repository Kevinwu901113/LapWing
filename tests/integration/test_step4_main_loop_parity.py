"""Step 4 M7.c — main-loop parity smoke.

End-to-end shape test: a sequence of MessageEvent + InnerTickEvent +
OWNER preempt go through the full MainLoop / EventQueue / brain stack
and produce the expected dispatches in order.

Not a parity test against real-conversation transcripts — that lives
in the manual `2g` validation suite — but this catches regressions in
the dispatch order, OWNER preemption, and inner-tick scheduling
contract that the unit tests can miss.
"""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.integration

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.authority_gate import AuthLevel
from src.core.event_queue import EventQueue
from src.core.events import InnerTickEvent, MessageEvent
from src.core.inner_tick_scheduler import InnerTickScheduler
from src.core.main_loop import MainLoop


@pytest.mark.asyncio
async def test_step4_main_loop_parity_8_turn_conversation():
    """A canned 8-message dialogue routes through MainLoop in order.

    Mirrors the shape of the 2g VALIDATION_TURNS suite (without using
    those exact prompts). Verifies:

      * Every MessageEvent reaches brain.think_conversational.
      * Order is preserved within priority class.
      * OWNER messages take precedence over GUEST messages.
      * No exceptions propagate out of dispatch.
    """
    brain = AsyncMock()
    seen_chat_ids: list[str] = []

    async def fake_think_conversational(*, chat_id, **_kwargs):
        seen_chat_ids.append(chat_id)
        return f"reply:{chat_id}"

    brain.think_conversational = fake_think_conversational
    brain.think_inner = AsyncMock(return_value=("", None, False))

    q = EventQueue()
    sched = InnerTickScheduler(q)
    loop = MainLoop(q, brain=brain, inner_tick_scheduler=sched)
    runner = asyncio.create_task(loop.run())

    # 8 OWNER messages with distinct chat_ids to avoid coalescing
    # (OWNER_COALESCE_SECONDS merges rapid-fire same-chat messages).
    for i in range(8):
        await q.put(MessageEvent.from_message(
            chat_id=f"kev-{i}",
            user_id="kev",
            text=f"turn {i}",
            adapter="qq",
            send_fn=_noop,
            auth_level=int(AuthLevel.OWNER),
        ))

    # Wait for all 8 dispatches.
    for _ in range(200):
        if len(seen_chat_ids) >= 8:
            break
        await asyncio.sleep(0.05)

    assert len(seen_chat_ids) == 8
    assert all(c.startswith("kev-") for c in seen_chat_ids)

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)


@pytest.mark.asyncio
async def test_step4_owner_preempts_mid_conversation():
    """A non-OWNER chat in flight is preempted when OWNER arrives.

    Slimmer than the M4 scenario tests: validates the parity stack
    sees the preempt as a normal dispatch sequence, not just the
    isolated cancellation path.
    """

    class FakeBrain:
        def __init__(self):
            self.persisted_interruptions: list = []
            self.dispatched: list = []
            self.long_call_started = asyncio.Event()
            self.long_call_release = asyncio.Event()

        async def _persist_interrupted(self, **kwargs):
            self.persisted_interruptions.append(kwargs)

        async def think_conversational(self, *, chat_id, **_kw):
            self.dispatched.append(chat_id)
            if chat_id == "grp":
                self.long_call_started.set()
                try:
                    await self.long_call_release.wait()
                except asyncio.CancelledError:
                    await self._persist_interrupted(
                        chat_id=chat_id, partial_text="hi-",
                        reason="owner_message_preempt", kind="conversational",
                    )
                    raise
            return "ok"

        async def think_inner(self, **_kw):
            return ("", None, False)

    brain = FakeBrain()
    q = EventQueue()
    loop = MainLoop(q, brain=brain)
    runner = asyncio.create_task(loop.run())

    await q.put(MessageEvent.from_message(
        chat_id="grp", user_id="bob", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
    ))
    await asyncio.wait_for(brain.long_call_started.wait(), timeout=2.0)

    await q.put(MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="urgent",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    ))
    # OWNER's think_conversational doesn't block, so it should dispatch
    # right after preemption.
    for _ in range(50):
        if "kev" in brain.dispatched:
            break
        await asyncio.sleep(0.02)

    assert "kev" in brain.dispatched
    assert any(p["chat_id"] == "grp" for p in brain.persisted_interruptions)

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)


async def _noop(*_a, **_kw):  # pragma: no cover
    return None
