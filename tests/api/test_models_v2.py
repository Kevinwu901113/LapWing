"""Phase 5: /api/v2/models/* 端点测试。"""

from unittest.mock import MagicMock

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

    # ModelConfigManager mock
    config_manager = MagicMock()
    config_manager.get_config.return_value = {
        "providers": [{"id": "minimax", "name": "MiniMax"}],
        "slots": {"main_conversation": {"provider_id": "minimax", "model_id": "minimax-m2.7"}},
    }
    brain._model_config = config_manager
    brain._note_store = None
    brain._memory_vector_store = None
    return brain


@pytest.mark.asyncio
class TestModelsV2:
    async def test_get_routing(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/models/routing")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "slots" in data

    async def test_available(self, mock_brain):
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/models/available")
        assert resp.status_code == 200
        data = resp.json()
        assert "slots" in data
        assert "providers" in data
