"""Tests for /api/v2/tasks/* endpoints.

v2.0 Step 1 note: `/api/v2/tasks/{id}/messages` now returns an empty list
(EventLogger-backed agent-history lookup was removed). Step 6 will wire
it back up against StateMutationLog-derived agent events.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app
from src.app.task_view import TaskViewStore


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

    async def test_task_messages_returns_empty(self, mock_brain):
        """Step 1 placeholder — endpoint shape preserved, payload empty."""
        app = create_app(mock_brain, DesktopEventBus(), task_view_store=TaskViewStore())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tasks/t1/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert data["messages"] == []
