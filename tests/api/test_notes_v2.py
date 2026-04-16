"""Phase 5: /api/v2/notes/* 端点测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    mock_auth = MagicMock()
    mock_auth.api_sessions.cookie_name = "lapwing_session"
    mock_auth.validate_api_session = MagicMock(return_value=True)
    mock_auth.bootstrap_token = MagicMock(return_value="test-token")
    brain.auth_manager = mock_auth
    brain.memory = MagicMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=[])
    brain.memory.get_last_interaction = AsyncMock(return_value=None)

    # NoteStore mock
    note_store = MagicMock()
    note_store.list_notes.return_value = [
        {"name": "people", "type": "dir", "note_id": None},
        {"name": "observation_20260415_1000_abcd.md", "type": "file", "note_id": "note_20260415_1000_abcd"},
    ]
    note_store.read.return_value = {
        "meta": {"id": "note_20260415_1000_abcd", "note_type": "observation"},
        "content": "Kevin 喜欢咖啡。",
        "file_path": "/data/memory/notes/observation_20260415_1000_abcd.md",
    }
    note_store.search_keyword.return_value = [
        {"note_id": "note_20260415_1000_abcd", "file_path": "/data/memory/notes/test.md", "snippet": "...咖啡..."},
    ]
    brain._note_store = note_store
    brain._memory_vector_store = None
    return brain


@pytest.fixture
def client(mock_brain):
    app = create_app(mock_brain, DesktopEventBus())
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
class TestNotesV2:
    async def test_tree(self, client):
        async with client:
            resp = await client.get("/api/v2/notes/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == ""
        assert len(data["entries"]) == 2
        assert data["entries"][0]["type"] == "dir"

    async def test_tree_with_path(self, client):
        async with client:
            resp = await client.get("/api/v2/notes/tree", params={"path": "people"})
        assert resp.status_code == 200

    async def test_content_by_note_id(self, client):
        async with client:
            resp = await client.get("/api/v2/notes/content", params={"note_id": "note_20260415_1000_abcd"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Kevin 喜欢咖啡。"
        assert data["meta"]["id"] == "note_20260415_1000_abcd"

    async def test_content_missing_params(self, client):
        async with client:
            resp = await client.get("/api/v2/notes/content")
        assert resp.status_code == 400

    async def test_content_not_found(self, client, mock_brain):
        mock_brain._note_store.read.return_value = None
        async with client:
            resp = await client.get("/api/v2/notes/content", params={"note_id": "missing"})
        assert resp.status_code == 404

    async def test_search(self, client):
        async with client:
            resp = await client.get("/api/v2/notes/search", params={"q": "咖啡"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "咖啡"
        assert len(data["results"]) == 1

    async def test_recall_no_vector_store(self, client):
        async with client:
            resp = await client.get("/api/v2/notes/recall", params={"q": "test"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []
