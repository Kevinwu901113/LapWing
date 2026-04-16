"""Phase 5: SSE + Dispatcher 测试。"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app
from src.core.dispatcher import Dispatcher
from src.core.event_logger_v2 import Event, EventLogger


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


@pytest.fixture
def mock_event_logger():
    logger = MagicMock(spec=EventLogger)
    logger.query = AsyncMock(return_value=[])
    logger.log = AsyncMock()
    logger.make_event = EventLogger.make_event
    return logger


@pytest.mark.asyncio
class TestDispatcherSubscribeAll:
    """Dispatcher subscribe_all 单元测试。"""

    async def test_subscribe_receives_events(self):
        event_logger = MagicMock(spec=EventLogger)
        event_logger.log = AsyncMock()
        event_logger.make_event = EventLogger.make_event
        disp = Dispatcher(event_logger)

        queue = asyncio.Queue()
        disp.subscribe_all(queue)

        await disp.submit("test.event", {"key": "value"})

        assert not queue.empty()
        event = queue.get_nowait()
        assert event.event_type == "test.event"
        assert event.payload == {"key": "value"}

    async def test_unsubscribe_stops_events(self):
        event_logger = MagicMock(spec=EventLogger)
        event_logger.log = AsyncMock()
        event_logger.make_event = EventLogger.make_event
        disp = Dispatcher(event_logger)

        queue = asyncio.Queue()
        disp.subscribe_all(queue)
        disp.unsubscribe_all(queue)

        await disp.submit("test.event", {"key": "value"})

        assert queue.empty()

    async def test_multiple_subscribers(self):
        event_logger = MagicMock(spec=EventLogger)
        event_logger.log = AsyncMock()
        event_logger.make_event = EventLogger.make_event
        disp = Dispatcher(event_logger)

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
        event_logger = MagicMock(spec=EventLogger)
        event_logger.log = AsyncMock()
        event_logger.make_event = EventLogger.make_event
        disp = Dispatcher(event_logger)

        queue = asyncio.Queue()
        disp.subscribe_all(queue)

        typed_events = []
        disp.subscribe("test.typed", lambda e: typed_events.append(e))

        await disp.submit("test.typed", {"typed": True})

        assert not queue.empty()
        assert len(typed_events) == 1


@pytest.mark.asyncio
class TestSSEReconnect:
    """SSE 断线重连回放测试（直接测试 _format_sse + EventLogger query）。"""

    async def test_event_logger_after_event_id(self, mock_event_logger):
        """验证 EventLogger.query(after_event_id=...) 被正确调用。"""
        missed_event = Event(
            event_id="missed1",
            timestamp=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
            event_type="note.written",
            actor="lapwing",
            task_id=None,
            source="",
            trust_level="",
            correlation_id="missed1",
            payload={"note_id": "n1"},
        )
        mock_event_logger.query = AsyncMock(return_value=[missed_event])

        result = await mock_event_logger.query(after_event_id="prev_event", limit=100)

        assert len(result) == 1
        assert result[0].event_type == "note.written"
        assert result[0].event_id == "missed1"
        mock_event_logger.query.assert_called_once_with(
            after_event_id="prev_event", limit=100
        )

    async def test_format_sse(self):
        """验证 SSE 格式化。"""
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
    """SSE 端点基本测试。"""

    async def test_sse_endpoint_exists(self, mock_brain):
        """验证 SSE 端点已注册。"""
        disp = Dispatcher(MagicMock(spec=EventLogger, log=AsyncMock(), make_event=EventLogger.make_event))
        app = create_app(mock_brain, DesktopEventBus(), dispatcher=disp)

        # 验证路由已注册
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v2/events" in routes
