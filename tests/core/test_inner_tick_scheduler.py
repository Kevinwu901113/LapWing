"""Unit tests for InnerTickScheduler."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.event_queue import EventQueue
from src.core.events import InnerTickEvent, PRIORITY_INNER_TICK
from src.core.inner_tick_scheduler import (
    InnerTickScheduler,
    parse_next_interval,
)


# ── parse_next_interval ──────────────────────────────────────────────


def test_parse_next_interval_minutes():
    text, secs = parse_next_interval("free thought [NEXT: 15m]")
    assert text == "free thought"
    assert secs == 15 * 60


def test_parse_next_interval_seconds():
    text, secs = parse_next_interval("[TNEXT: 30s] go faster")
    assert "30s" not in text
    assert secs == 30


def test_parse_next_interval_hours():
    _, secs = parse_next_interval("rest [NEXT: 2h]")
    assert secs == 2 * 3600


def test_parse_next_interval_absent():
    text, secs = parse_next_interval("nothing to do")
    assert text == "nothing to do"
    assert secs is None


# ── note_tick_result ─────────────────────────────────────────────────


def test_llm_next_interval_overrides_backoff():
    sched = InnerTickScheduler(EventQueue())
    sched.note_tick_result(did_something=False, llm_next_interval=120)
    assert sched.next_interval_seconds == 120


def test_idle_backoff_grows_streak():
    sched = InnerTickScheduler(EventQueue())
    initial = sched.next_interval_seconds
    sched.note_tick_result(did_something=False, llm_next_interval=None)
    sched.note_tick_result(did_something=False, llm_next_interval=None)
    sched.note_tick_result(did_something=False, llm_next_interval=None)
    assert sched.idle_streak == 3
    assert sched.next_interval_seconds > initial


def test_did_something_resets_streak():
    sched = InnerTickScheduler(EventQueue())
    sched.note_tick_result(did_something=False, llm_next_interval=None)
    sched.note_tick_result(did_something=False, llm_next_interval=None)
    assert sched.idle_streak == 2
    sched.note_tick_result(did_something=True, llm_next_interval=None)
    assert sched.idle_streak == 0


def test_tick_failed_doubles_interval_with_min_clamp():
    sched = InnerTickScheduler(EventQueue())
    before = sched.next_interval_seconds
    sched.note_tick_failed()
    assert sched.next_interval_seconds >= before


def test_llm_interval_clamped_to_min_max():
    sched = InnerTickScheduler(EventQueue())
    sched.note_tick_result(did_something=False, llm_next_interval=1)  # below min
    assert sched.next_interval_seconds >= sched.MIN_INTERVAL
    sched.note_tick_result(did_something=False, llm_next_interval=10**9)  # above max
    assert sched.next_interval_seconds <= sched.MAX_INTERVAL


# ── urgency queue ────────────────────────────────────────────────────


def test_drain_urgency_returns_all_pushed_items():
    sched = InnerTickScheduler(EventQueue())
    sched.push_urgency({"type": "reminder", "content": "a"})
    sched.push_urgency({"type": "agent_done", "content": "b"})
    items = sched.drain_urgency()
    assert [i["type"] for i in items] == ["reminder", "agent_done"]
    # Subsequent drain returns empty
    assert sched.drain_urgency() == []


# ── conversation pause ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_conversation_pause_blocks_ticks():
    """While in conversation, the scheduler should not produce ticks."""
    q = EventQueue()
    sched = InnerTickScheduler(q)
    sched._next_interval = 0  # fire as fast as possible  # type: ignore[attr-defined]
    sched.note_conversation_start()
    await sched.start()

    await asyncio.sleep(0.05)
    # Queue should be empty — we paused.
    assert q.qsize() == 0

    sched.note_conversation_end()
    await asyncio.sleep(0.1)
    # After conversation end, at least one tick should land.
    assert q.qsize() >= 1

    await sched.stop()


# ── lifecycle ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_urgency_wakes_immediately():
    q = EventQueue()
    sched = InnerTickScheduler(q)
    sched._next_interval = 60  # long interval; would never fire in test  # type: ignore[attr-defined]
    await sched.start()

    sched.push_urgency({"type": "system", "content": "wake!"})
    # Wait briefly for the tick to land.
    for _ in range(20):
        if q.qsize() >= 1:
            break
        await asyncio.sleep(0.02)
    assert q.qsize() >= 1
    ev = await q.get()
    assert isinstance(ev, InnerTickEvent)
    assert ev.priority == PRIORITY_INNER_TICK
    assert ev.reason == "urgency"

    await sched.stop()


@pytest.mark.asyncio
async def test_stop_idempotent():
    sched = InnerTickScheduler(EventQueue())
    await sched.start()
    await sched.stop()
    await sched.stop()  # second stop is a no-op
