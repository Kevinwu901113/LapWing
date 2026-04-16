"""Phase 5: /api/v2/system/* 端点测试。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app
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
    logger.query = AsyncMock(return_value=[
        Event(
            event_id="ev1",
            timestamp=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
            event_type="system.heartbeat_tick",
            actor="system",
            task_id=None,
            source="",
            trust_level="",
            correlation_id="ev1",
            payload={"tick": 42},
        ),
    ])
    return logger


@pytest.mark.asyncio
class TestSystemV2:
    async def test_info(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/system/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "uptime_seconds" in data
        assert "cpu_percent" in data
        assert "memory" in data
        assert "disk" in data
        assert "channels" in data
        assert data["channels"]["desktop"] == "via_websocket"

    async def test_events_query(self, mock_brain, mock_event_logger):
        app = create_app(mock_brain, DesktopEventBus(), event_logger_v2=mock_event_logger)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/system/events", params={"event_type": "system.heartbeat_tick"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["event_type"] == "system.heartbeat_tick"

    async def test_events_no_logger(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/system/events")
        assert resp.status_code == 200
        assert resp.json()["events"] == []
