"""Unit tests for src/core/main_loop.py — skeleton only (M1.c).

Handler behaviour for messages / inner-ticks lands in M2/M3; here we
just verify the runtime lifecycle: start / dispatch / stop / shutdown
event / cancellation.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.authority_gate import AuthLevel
from src.core.event_queue import EventQueue
from src.core.events import InnerTickEvent, MessageEvent, SystemEvent
from src.core.main_loop import MainLoop


@pytest.mark.asyncio
async def test_loop_starts_and_stops_via_stop():
    q = EventQueue()
    loop = MainLoop(q)
    runner = asyncio.create_task(loop.run())
    await asyncio.sleep(0.01)  # let the loop reach queue.get
    await loop.stop()
    # Unblock queue.get with any event so the run() exits.
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert runner.done()


@pytest.mark.asyncio
async def test_dispatch_routes_message_event(monkeypatch):
    q = EventQueue()
    loop = MainLoop(q)
    seen: list[str] = []

    async def fake_handle(event):
        seen.append(f"msg:{event.chat_id}")

    monkeypatch.setattr(loop, "_handle_message", fake_handle)
    runner = asyncio.create_task(loop.run())
    ev = MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    )
    await q.put(ev)
    await asyncio.sleep(0.05)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert "msg:kev" in seen


@pytest.mark.asyncio
async def test_dispatch_routes_inner_tick(monkeypatch):
    q = EventQueue()
    loop = MainLoop(q)
    seen: list[str] = []

    async def fake_handle(event):
        seen.append(f"tick:{event.reason}")

    monkeypatch.setattr(loop, "_handle_inner_tick", fake_handle)
    runner = asyncio.create_task(loop.run())
    await q.put(InnerTickEvent.make(reason="commitment_check"))
    await asyncio.sleep(0.05)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert "tick:commitment_check" in seen


@pytest.mark.asyncio
async def test_system_shutdown_event_stops_loop():
    q = EventQueue()
    loop = MainLoop(q)
    runner = asyncio.create_task(loop.run())
    await asyncio.sleep(0.01)
    await q.put(SystemEvent.make(action="shutdown"))
    await asyncio.wait_for(runner, timeout=1.0)
    assert runner.done()


@pytest.mark.asyncio
async def test_unknown_event_kind_does_not_crash_loop(monkeypatch, caplog):
    """A non-base event subclass should be logged, not raise."""
    q = EventQueue()
    loop = MainLoop(q)

    # Construct via a SystemEvent with a recognised type so we can
    # bypass isinstance branches. Patch _dispatch to feed a bare Event.
    from src.core.events import Event

    bare = Event(priority=4, kind="weird")
    runner = asyncio.create_task(loop.run())
    await asyncio.sleep(0.01)
    await q.put(bare)
    await asyncio.sleep(0.05)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert any("Unknown event kind" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_cancel_in_flight_clears_current_task():
    q = EventQueue()
    loop = MainLoop(q)

    # Simulate an in-flight handler.
    async def long_running():
        await asyncio.sleep(10)

    loop._current_task = asyncio.create_task(long_running())  # type: ignore[attr-defined]
    await loop._cancel_in_flight(reason="test")  # type: ignore[attr-defined]
    assert loop._current_task is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancel_in_flight_noop_when_no_task():
    q = EventQueue()
    loop = MainLoop(q)
    await loop._cancel_in_flight(reason="nothing-to-do")  # type: ignore[attr-defined]
    assert loop._current_task is None  # type: ignore[attr-defined]


async def _noop(*_a, **_kw):  # pragma: no cover
    return None


# ── OWNER coalesce & preemption tests ──────────────────────────────


def _make_owner_event(chat_id="kev", text="hi", done_future=None):
    return MessageEvent.from_message(
        chat_id=chat_id, user_id="kev", text=text,
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
        done_future=done_future,
    )


class FakeBrain:
    """Minimal brain substitute that records calls."""

    def __init__(self, delay: float = 0.0, reply: str = "ok"):
        self.calls: list[dict] = []
        self._delay = delay
        self._reply = reply

    async def think_conversational(self, **kw):
        self.calls.append(kw)
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._reply

    async def think_inner(self, **kw):
        if self._delay:
            await asyncio.sleep(self._delay)
        return "", None, False


@pytest.mark.asyncio
async def test_owner_messages_coalesce_in_burst():
    """Three OWNER messages queued within the coalesce window should
    merge into a single brain call with newline-joined text."""
    q = EventQueue()
    brain = FakeBrain(delay=0.0, reply="merged-reply")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.1  # speed up test

    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(text="msg1"))
    await q.put(_make_owner_event(text="msg2"))
    await q.put(_make_owner_event(text="msg3"))

    await asyncio.sleep(0.5)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)

    assert len(brain.calls) == 1
    assert brain.calls[0]["user_message"] == "msg1\nmsg2\nmsg3"


@pytest.mark.asyncio
async def test_owner_does_not_preempt_owner():
    """A second OWNER message must NOT cancel the handler for the first."""
    q = EventQueue()
    brain = FakeBrain(delay=0.5, reply="done")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.05

    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(text="first"))
    await asyncio.sleep(0.15)  # let handler start
    await q.put(_make_owner_event(text="second"))
    await asyncio.sleep(1.5)  # let both complete

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)

    assert len(brain.calls) == 2
    assert brain.calls[0]["user_message"] == "first"
    assert brain.calls[1]["user_message"] == "second"


@pytest.mark.asyncio
async def test_owner_still_preempts_inner_tick():
    """OWNER preemption of non-OWNER tasks (inner tick) must still work."""
    q = EventQueue()
    brain = FakeBrain(delay=2.0)
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.05

    runner = asyncio.create_task(loop.run())

    await q.put(InnerTickEvent.make())
    await asyncio.sleep(0.1)  # let inner tick handler start
    await q.put(_make_owner_event(text="urgent"))
    await asyncio.sleep(1.0)

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=3.0)

    owner_calls = [c for c in brain.calls if c.get("user_message") == "urgent"]
    assert len(owner_calls) == 1


@pytest.mark.asyncio
async def test_coalesce_only_same_chat_id():
    """OWNER messages for different chat_ids must NOT be merged."""
    q = EventQueue()
    brain = FakeBrain(delay=0.0, reply="ok")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.1

    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(chat_id="chatA", text="a1"))
    await q.put(_make_owner_event(chat_id="chatB", text="b1"))

    await asyncio.sleep(0.5)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)

    assert len(brain.calls) == 2
    texts = {c["user_message"] for c in brain.calls}
    assert "a1" in texts
    assert "b1" in texts


@pytest.mark.asyncio
async def test_coalesced_done_futures_all_resolved():
    """All done_futures from coalesced messages must receive the reply."""
    q = EventQueue()
    brain = FakeBrain(delay=0.0, reply="shared-reply")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.1

    runner = asyncio.create_task(loop.run())

    f1 = asyncio.get_event_loop().create_future()
    f2 = asyncio.get_event_loop().create_future()
    await q.put(_make_owner_event(text="a", done_future=f1))
    await q.put(_make_owner_event(text="b", done_future=f2))

    results = await asyncio.wait_for(
        asyncio.gather(f1, f2), timeout=2.0,
    )
    assert results == ["shared-reply", "shared-reply"]

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)
