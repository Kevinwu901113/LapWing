"""Phase 5: /api/v2/tasks/* 端点测试。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app
from src.app.task_view import TaskViewStore
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
            event_type="agent.message_sent",
            actor="researcher",
            task_id="t1",
            source="desktop",
            trust_level="",
            correlation_id="ev1",
            payload={"content": "searching..."},
        ),
    ])
    return logger


@pytest.mark.asyncio
class TestTasksV2:
    async def test_list_tasks_empty(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus(), task_view_store=TaskViewStore())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["count"] == 0

    async def test_get_task_not_found(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus(), task_view_store=TaskViewStore())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tasks/nonexistent")
        assert resp.status_code == 404

    async def test_task_messages(self, mock_brain, mock_event_logger):
        app = create_app(
            mock_brain, DesktopEventBus(),
            task_view_store=TaskViewStore(),
            event_logger_v2=mock_event_logger,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tasks/t1/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["event_type"] == "agent.message_sent"
        assert data["messages"][0]["actor"] == "researcher"

    async def test_task_messages_no_logger(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus(), task_view_store=TaskViewStore())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tasks/t1/messages")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []
