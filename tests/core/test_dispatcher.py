"""tests/core/test_dispatcher.py — Dispatcher 串行提交、事件通知测试。"""

import asyncio
import pytest

from src.core.dispatcher import Dispatcher


@pytest.fixture
def dispatcher():
    return Dispatcher()


class TestDispatcher:
    async def test_submit_returns_event_id(self, dispatcher):
        event_id = await dispatcher.submit("test_event", {"key": "value"})
        assert isinstance(event_id, str)
        assert len(event_id) > 0

    async def test_submit_delivers_to_type_subscriber(self, dispatcher):
        received = []
        dispatcher.subscribe("notify_test", lambda e: received.append(e.event_type))
        await dispatcher.submit("notify_test", {"k": "v"})
        assert received == ["notify_test"]

    async def test_submit_delivers_to_global_queue(self, dispatcher):
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        dispatcher.subscribe_all(queue)
        await dispatcher.submit("broadcast", {"n": 1}, actor="lapwing")
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event.event_type == "broadcast"
        assert event.actor == "lapwing"
        assert event.payload == {"n": 1}

    async def test_serial_execution(self, dispatcher):
        """Events are processed serially (no interleaving)."""
        order = []

        async def slow_handler(event):
            order.append(f"start_{event.payload['n']}")
            await asyncio.sleep(0.01)
            order.append(f"end_{event.payload['n']}")

        dispatcher.subscribe("ordered", slow_handler)
        await dispatcher.submit("ordered", {"n": 1})
        await dispatcher.submit("ordered", {"n": 2})
        assert order == ["start_1", "end_1", "start_2", "end_2"]

    async def test_unsubscribe_all_is_idempotent(self, dispatcher):
        queue: asyncio.Queue = asyncio.Queue()
        dispatcher.subscribe_all(queue)
        dispatcher.unsubscribe_all(queue)
        dispatcher.unsubscribe_all(queue)  # should not raise
