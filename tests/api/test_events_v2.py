"""Tests for /api/v2/events SSE endpoint + Dispatcher pub/sub.

v2.0 Step 1: EventLogger + events_v2.db have been removed. Dispatcher is
pure in-memory pub/sub now; the Last-Event-ID replay branch on the SSE
route has been deleted (flagged in cleanup_report_step1.md as Step 2 debt).
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app
from src.core.dispatcher import Dispatcher, Event


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    mock_auth = MagicMock()
    mock_auth.api_sessions.cookie_name = "lapwing_session"
    mock_auth.validate_api_session = MagicMock(return_value=True)
    brain.auth_manager = mock_auth
    brain.memory = MagicMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=[])
    brain.memory.get_last_interaction = AsyncMock(return_value=None)
    brain._note_store = None
    brain._memory_vector_store = None
    return brain


@pytest.mark.asyncio
class TestDispatcherSubscribeAll:
    """Dispatcher subscribe_all 单元测试。"""

    async def test_subscribe_receives_events(self):
        disp = Dispatcher()
        queue = asyncio.Queue()
        disp.subscribe_all(queue)
        await disp.submit("test.event", {"key": "value"})
        assert not queue.empty()
        event = queue.get_nowait()
        assert event.event_type == "test.event"
        assert event.payload == {"key": "value"}

    async def test_unsubscribe_stops_events(self):
        disp = Dispatcher()
        queue = asyncio.Queue()
        disp.subscribe_all(queue)
        disp.unsubscribe_all(queue)
        await disp.submit("test.event", {"key": "value"})
        assert queue.empty()

    async def test_multiple_subscribers(self):
        disp = Dispatcher()
        q1 = asyncio.Queue()
        q2 = asyncio.Queue()
        disp.subscribe_all(q1)
        disp.subscribe_all(q2)
        await disp.submit("test.event", {"multi": True})
        assert not q1.empty()
        assert not q2.empty()
        assert q1.get_nowait().event_type == "test.event"
        assert q2.get_nowait().event_type == "test.event"

    async def test_subscribe_all_plus_typed(self):
        """全局订阅和按类型订阅同时生效。"""
        disp = Dispatcher()
        queue = asyncio.Queue()
        disp.subscribe_all(queue)
        typed_events = []
        disp.subscribe("test.typed", lambda e: typed_events.append(e))
        await disp.submit("test.typed", {"typed": True})
        assert not queue.empty()
        assert len(typed_events) == 1


@pytest.mark.asyncio
class TestSSEFormat:
    """SSE 格式化单元测试。"""

    async def test_format_sse(self):
        from src.api.routes.events_v2 import _format_sse

        event = Event(
            event_id="e1",
            timestamp=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
            event_type="note.written",
            actor="lapwing",
            task_id=None,
            source="",
            trust_level="",
            correlation_id="e1",
            payload={"note_id": "n1"},
        )
        formatted = _format_sse(event)
        assert formatted.startswith("id: e1\n")
        assert "event: note.written\n" in formatted
        assert "data: " in formatted
        assert formatted.endswith("\n\n")


@pytest.mark.asyncio
class TestSSEEndpoint:
    """SSE 端点基本注册检查。"""

    async def test_sse_endpoint_exists(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus(), dispatcher=Dispatcher())
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v2/events" in routes
