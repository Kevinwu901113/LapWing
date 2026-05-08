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
async def test_has_user_message_for_chat_and_pop_matching():
    q = EventQueue()
    tick = InnerTickEvent.make()
    owner = MessageEvent.from_message(
        chat_id="k", user_id="k", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    )
    other = MessageEvent.from_message(
        chat_id="other", user_id="k", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    )
    await q.put(tick)
    await q.put(owner)
    await q.put(other)

    assert q.has_user_message_for_chat("k") is True
    popped = q.pop_matching(
        lambda ev: isinstance(ev, MessageEvent) and ev.chat_id == "k"
    )
    assert popped is owner
    assert q.has_user_message_for_chat("k") is False
    assert q.has_user_message_for_chat("other") is True


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


@pytest.mark.asyncio
async def test_agent_needs_input_dequeues_before_inner_tick():
    from src.core.concurrent_bg_work.event_bus import AgentNeedsInputEvent
    from src.core.concurrent_bg_work.types import AgentNeedsInputPayload
    from src.core.events import PRIORITY_AGENT_URGENT

    q = EventQueue()
    tick = InnerTickEvent.make()
    urgent = AgentNeedsInputEvent(
        task_id="t1",
        payload=AgentNeedsInputPayload(
            question_for_lapwing="what file?",
            question_for_owner=None,
            expected_answer_shape=None,
        ),
        priority=PRIORITY_AGENT_URGENT,
    )
    await q.put(tick)       # priority 2
    await q.put(urgent)     # priority 1

    first = await q.get()
    second = await q.get()
    assert first.kind == "agent_needs_input"
    assert second.kind == "inner_tick"


@pytest.mark.asyncio
async def test_agent_failed_dequeues_before_inner_tick():
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import AgentEvent, AgentEventType, SalienceLevel
    from src.core.events import PRIORITY_AGENT_URGENT
    from datetime import datetime, timezone

    q = EventQueue()
    tick = InnerTickEvent.make()
    trigger = AgentEvent(
        event_id="ev_1", task_id="t1", chat_id="c", type=AgentEventType.AGENT_FAILED,
        occurred_at=datetime.now(timezone.utc), summary_for_lapwing="boom",
        summary_for_owner=None, raw_payload_ref=None,
        salience=SalienceLevel.HIGH, payload={}, sequence_in_task=1,
    )
    failed = AgentTaskResultEvent(
        task_id="t1", triggering_event=trigger, effective_salience=SalienceLevel.HIGH,
        priority=PRIORITY_AGENT_URGENT,
    )
    await q.put(tick)       # priority 2
    await q.put(failed)     # priority 1

    first = await q.get()
    second = await q.get()
    assert first.kind == "agent_task_result"
    assert second.kind == "inner_tick"


@pytest.mark.asyncio
async def test_owner_dequeues_before_agent_needs_input():
    from src.core.concurrent_bg_work.event_bus import AgentNeedsInputEvent
    from src.core.concurrent_bg_work.types import AgentNeedsInputPayload
    from src.core.events import PRIORITY_AGENT_URGENT

    q = EventQueue()
    urgent = AgentNeedsInputEvent(
        task_id="t1",
        payload=AgentNeedsInputPayload(
            question_for_lapwing="what file?",
            question_for_owner=None,
            expected_answer_shape=None,
        ),
        priority=PRIORITY_AGENT_URGENT,
    )
    owner = MessageEvent.from_message(
        chat_id="k", user_id="k", text="stop",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    )
    await q.put(urgent)     # priority 1
    await q.put(owner)      # priority 0

    first = await q.get()
    second = await q.get()
    assert first.priority == PRIORITY_OWNER_MESSAGE
    assert first.kind == "owner_message"
    assert second.kind == "agent_needs_input"


async def _noop(*_args, **_kwargs):  # pragma: no cover
    return None
