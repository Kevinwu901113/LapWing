"""tests/core/test_dispatcher.py — Dispatcher 串行提交、事件通知测试。"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from src.core.event_logger_v2 import EventLogger
from src.core.dispatcher import Dispatcher


@pytest.fixture
async def event_logger(tmp_path):
    logger = EventLogger(tmp_path / "test_events.db")
    await logger.init()
    yield logger
    await logger.close()


@pytest.fixture
async def dispatcher(event_logger):
    return Dispatcher(event_logger)


class TestDispatcher:
    async def test_submit_returns_event_id(self, dispatcher):
        event_id = await dispatcher.submit("test_event", {"key": "value"})
        assert isinstance(event_id, str)
        assert len(event_id) > 0

    async def test_submit_persists_event(self, dispatcher, event_logger):
        await dispatcher.submit("test_event", {"key": "value"}, actor="test")
        events = await event_logger.query(event_type="test_event")
        assert len(events) == 1
        assert events[0].actor == "test"
        assert events[0].payload == {"key": "value"}

    async def test_serial_execution(self, dispatcher):
        """Verify events are processed serially (no interleaving)."""
        order = []

        async def slow_handler(event):
            order.append(f"start_{event.payload['n']}")
            await asyncio.sleep(0.01)
            order.append(f"end_{event.payload['n']}")

        dispatcher.subscribe("ordered", slow_handler)

        await dispatcher.submit("ordered", {"n": 1})
        await dispatcher.submit("ordered", {"n": 2})

        assert order == ["start_1", "end_1", "start_2", "end_2"]

    async def test_subscribe_notifies(self, dispatcher):
        received = []
        dispatcher.subscribe("notify_test", lambda e: received.append(e.event_type))
        await dispatcher.submit("notify_test", {})
        assert received == ["notify_test"]
