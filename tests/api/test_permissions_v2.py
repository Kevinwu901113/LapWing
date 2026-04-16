"""Phase 5: /api/v2/permissions/* 端点测试。"""

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
    brain.auth_manager = mock_auth
    brain.memory = MagicMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=[])
    brain.memory.get_last_interaction = AsyncMock(return_value=None)
    brain._note_store = None
    brain._memory_vector_store = None
    return brain


@pytest.mark.asyncio
class TestPermissionsV2:
    async def test_list_permissions(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert "defaults" in data
        assert "operation_auth" in data

    async def test_set_and_remove_user(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 设置权限
            resp = await client.put(
                "/api/v2/permissions/user123",
                json={"level": 2, "name": "Test User", "note": "trusted friend"},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True
            assert resp.json()["level"] == 2

            # 验证出现在列表中
            resp = await client.get("/api/v2/permissions")
            assert "user123" in resp.json()["users"]

            # 删除
            resp = await client.delete("/api/v2/permissions/user123")
            assert resp.status_code == 200

    async def test_remove_nonexistent(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v2/permissions/nonexistent")
        assert resp.status_code == 404

    async def test_invalid_level(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v2/permissions/user123",
                json={"level": 5, "name": "Bad"},
            )
        assert resp.status_code == 400

    async def test_defaults(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/permissions/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert "default_auth" in data
        assert "operation_auth" in data
