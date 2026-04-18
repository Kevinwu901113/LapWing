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
