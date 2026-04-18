"""Unit tests for src/core/event_queue.py."""

from __future__ import annotations

import asyncio

import pytest

from src.core.authority_gate import AuthLevel
from src.core.event_queue import EventQueue
from src.core.events import (
    PRIORITY_INNER_TICK,
    PRIORITY_OWNER_MESSAGE,
    PRIORITY_USER_MESSAGE,
    InnerTickEvent,
    MessageEvent,
)


@pytest.mark.asyncio
async def test_empty_queue_state():
    q = EventQueue()
    assert q.empty()
    assert q.qsize() == 0
    assert q.peek_priority() is None
    assert q.has_owner_message() is False


@pytest.mark.asyncio
async def test_put_and_get_returns_highest_priority_first():
    q = EventQueue()
    tick = InnerTickEvent.make()
    owner = MessageEvent.from_message(
        chat_id="k", user_id="k", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    )
    user = MessageEvent.from_message(
        chat_id="x", user_id="y", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.GUEST),
    )
    await q.put(tick)
    await q.put(user)
    await q.put(owner)
    assert q.qsize() == 3
    assert (await q.get()).priority == PRIORITY_OWNER_MESSAGE
    assert (await q.get()).priority == PRIORITY_USER_MESSAGE
    assert (await q.get()).priority == PRIORITY_INNER_TICK


@pytest.mark.asyncio
async def test_peek_priority_does_not_dequeue():
    q = EventQueue()
    await q.put(InnerTickEvent.make())
    assert q.peek_priority() == PRIORITY_INNER_TICK
    assert q.qsize() == 1


@pytest.mark.asyncio
async def test_has_owner_message_true_when_owner_queued():
    q = EventQueue()
    await q.put(InnerTickEvent.make())
    await q.put(
        MessageEvent.from_message(
            chat_id="k", user_id="k", text="hi",
            adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
        )
    )
    assert q.has_owner_message() is True


@pytest.mark.asyncio
async def test_has_owner_message_false_when_no_owner():
    q = EventQueue()
    await q.put(InnerTickEvent.make())
    await q.put(
        MessageEvent.from_message(
            chat_id="x", user_id="y", text="hi",
            adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.TRUSTED),
        )
    )
    assert q.has_owner_message() is False


@pytest.mark.asyncio
async def test_get_blocks_until_event_arrives():
    q = EventQueue()

    async def consumer():
        return await q.get()

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.01)
    assert not task.done()
    tick = InnerTickEvent.make()
    await q.put(tick)
    got = await asyncio.wait_for(task, timeout=1.0)
    assert got is tick


@pytest.mark.asyncio
async def test_concurrent_producers_and_consumer():
    q = EventQueue()
    seen: list[str] = []

    async def producer(reason: str, n: int):
        for _ in range(n):
            await q.put(InnerTickEvent.make(reason=reason))

    async def consumer(n: int):
        for _ in range(n):
            ev = await q.get()
            seen.append(ev.kind)

    await asyncio.gather(
        producer("a", 5),
        producer("b", 5),
        consumer(10),
    )
    assert len(seen) == 10
    assert all(k == "inner_tick" for k in seen)


async def _noop(*_args, **_kwargs):  # pragma: no cover
    return None
