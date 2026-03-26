"""本地 API 测试。"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.memory = MagicMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=["c2", "c1"])

    async def get_last_interaction(chat_id: str):
        if chat_id == "c2":
            return datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
        return datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc)

    brain.memory.get_last_interaction = AsyncMock(side_effect=get_last_interaction)
    brain.memory.get_top_interests = AsyncMock(return_value=[
        {"topic": "Python", "weight": 3.0, "last_seen": "2026-03-24T10:00:00+00:00"}
    ])
    brain.memory.get_user_facts = AsyncMock(return_value=[
        {"fact_key": "偏好_语言", "fact_value": "中文", "updated_at": "2026-03-24"},
        {"fact_key": "memory_summary_2026-03-23", "fact_value": "聊了工作。", "updated_at": "2026-03-23"},
    ])
    brain.memory.delete_user_fact = AsyncMock(return_value=True)
    brain.prompt_evolver = MagicMock()
    brain.prompt_evolver.evolve = AsyncMock(return_value={"success": True, "changes_summary": "优化了语气"})
    brain.reload_persona = MagicMock()
    return brain


@pytest.mark.asyncio
class TestLocalApi:
    async def test_status_and_chats_endpoints(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            chats_response = await client.get("/api/chats")
            status_response = await client.get("/api/status")

        assert chats_response.status_code == 200
        assert chats_response.json()[0]["chat_id"] == "c2"
        assert status_response.status_code == 200
        assert status_response.json()["online"] is True
        assert status_response.json()["chat_count"] == 2

    async def test_memory_endpoint_filters_summaries(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/memory", params={"chat_id": "c1"})

        data = response.json()
        assert response.status_code == 200
        assert data["items"] == [
            {
                "index": 1,
                "fact_key": "偏好_语言",
                "fact_value": "中文",
                "updated_at": "2026-03-24",
            }
        ]

    async def test_memory_delete_endpoint(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/memory/delete",
                json={"chat_id": "c1", "fact_key": "偏好_语言"},
            )

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_brain.memory.delete_user_fact.assert_awaited_once_with("c1", "偏好_语言")

    async def test_learnings_endpoint_returns_files(self, mock_brain, monkeypatch, tmp_path):
        learnings_dir = tmp_path / "learnings"
        learnings_dir.mkdir()
        (learnings_dir / "2026-03-24.md").write_text("# note\nhello", encoding="utf-8")
        monkeypatch.setattr("src.api.server._LEARNINGS_DIR", learnings_dir)

        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/learnings")

        assert response.status_code == 200
        assert response.json()["items"][0]["filename"] == "2026-03-24.md"

    async def test_events_stream_emits_published_event(self, mock_brain):
        event_bus = DesktopEventBus()
        app = create_app(mock_brain, event_bus)
        route = next(route for route in app.routes if getattr(route, "path", "") == "/api/events/stream")
        response = await route.endpoint()

        first_chunk_task = asyncio.create_task(response.body_iterator.__anext__())
        await asyncio.sleep(0.05)
        await event_bus.publish("proactive_message", {"chat_id": "c1", "text": "你好"})
        body = await asyncio.wait_for(first_chunk_task, timeout=1)
        if isinstance(body, bytes):
            body = body.decode("utf-8")

        assert "proactive_message" in body
        assert "你好" in body
