"""Unit tests for src/core/events.py."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.authority_gate import AuthLevel
from src.core.events import (
    PRIORITY_INNER_TICK,
    PRIORITY_OWNER_MESSAGE,
    PRIORITY_SYSTEM,
    PRIORITY_USER_MESSAGE,
    Event,
    InnerTickEvent,
    MessageEvent,
    SystemEvent,
)


# ── Ordering ─────────────────────────────────────────────────────────


def test_owner_message_sorts_before_user_message():
    owner = MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="qq", send_fn=_noop_send, auth_level=int(AuthLevel.OWNER),
    )
    guest = MessageEvent.from_message(
        chat_id="grp", user_id="bob", text="hi",
        adapter="qq", send_fn=_noop_send, auth_level=int(AuthLevel.GUEST),
    )
    # Sort puts lower priority first; owner is priority 0.
    assert owner < guest


def test_inner_tick_sorts_after_user_message():
    user = MessageEvent.from_message(
        chat_id="x", user_id="x", text="x",
        adapter="qq", send_fn=_noop_send, auth_level=int(AuthLevel.GUEST),
    )
    tick = InnerTickEvent.make()
    assert user < tick


def test_system_sorts_last_by_default():
    tick = InnerTickEvent.make()
    sysev = SystemEvent.make(action="shutdown")
    assert tick < sysev


def test_same_priority_sorts_by_timestamp_fifo():
    a = InnerTickEvent.make(reason="periodic")
    # Force a later monotonic stamp without sleeping.
    b = InnerTickEvent(
        priority=PRIORITY_INNER_TICK,
        kind="inner_tick",
        timestamp=a.timestamp + 0.001,
        scheduled_at=time.monotonic(),
        reason="periodic",
    )
    assert a < b


def test_event_subclasses_are_frozen():
    ev = InnerTickEvent.make()
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        ev.priority = 99  # type: ignore[misc]


# ── Construction helpers ─────────────────────────────────────────────


def test_message_event_from_owner_assigns_owner_priority_and_kind():
    ev = MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="desktop", send_fn=_noop_send, auth_level=int(AuthLevel.OWNER),
    )
    assert ev.priority == PRIORITY_OWNER_MESSAGE
    assert ev.kind == "owner_message"


def test_message_event_from_trusted_uses_user_priority():
    ev = MessageEvent.from_message(
        chat_id="x", user_id="y", text="hi",
        adapter="qq", send_fn=_noop_send, auth_level=int(AuthLevel.TRUSTED),
    )
    assert ev.priority == PRIORITY_USER_MESSAGE
    assert ev.kind == "user_message"


def test_inner_tick_default_reason_periodic():
    ev = InnerTickEvent.make()
    assert ev.priority == PRIORITY_INNER_TICK
    assert ev.kind == "inner_tick"
    assert ev.reason == "periodic"


def test_system_event_carries_payload():
    ev = SystemEvent.make(action="reload_persona", payload={"path": "x"})
    assert ev.priority == PRIORITY_SYSTEM
    assert ev.kind == "system"
    assert ev.action == "reload_persona"
    assert ev.payload == {"path": "x"}


# ── PriorityQueue integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_priority_queue_orders_events_correctly():
    q: asyncio.PriorityQueue[Event] = asyncio.PriorityQueue()
    tick = InnerTickEvent.make()
    sysev = SystemEvent.make(action="shutdown")
    user = MessageEvent.from_message(
        chat_id="x", user_id="y", text="hi",
        adapter="qq", send_fn=_noop_send, auth_level=int(AuthLevel.GUEST),
    )
    owner = MessageEvent.from_message(
        chat_id="k", user_id="k", text="hi",
        adapter="qq", send_fn=_noop_send, auth_level=int(AuthLevel.OWNER),
    )
    # Put in scrambled order; expect owner→user→tick→system.
    for ev in (sysev, tick, user, owner):
        await q.put(ev)
    got = [await q.get() for _ in range(4)]
    assert got == [owner, user, tick, sysev]


# ── helpers ──────────────────────────────────────────────────────────


async def _noop_send(*_args, **_kwargs):  # pragma: no cover - test stub
    return None
