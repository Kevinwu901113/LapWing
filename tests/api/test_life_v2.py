"""/api/v2/life/* endpoint tests (Phase 5 — Life v2)."""

from __future__ import annotations

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

    # Trajectory store — async list method returns [] by default
    trajectory = MagicMock()
    trajectory.list_for_timeline = AsyncMock(return_value=[])
    trajectory.recent = AsyncMock(return_value=[])
    brain.trajectory_store = trajectory

    # SoulManager — snapshot dir points somewhere that does not exist by default
    soul = MagicMock()
    soul.SNAPSHOT_DIR = MagicMock()
    soul.SNAPSHOT_DIR.exists = MagicMock(return_value=False)
    soul.SNAPSHOT_DIR.iterdir = MagicMock(return_value=[])
    brain._soul_manager_ref = soul

    # DurableScheduler — list fired reminders returns []
    scheduler = MagicMock()
    scheduler.list_fired = AsyncMock(return_value=[])
    brain._durable_scheduler_ref = scheduler

    # LLM router — unused in scaffold smoke test
    brain.router = MagicMock()

    return brain


@pytest.fixture
def client(mock_brain, tmp_path, monkeypatch):
    # Empty summaries dir so the timeline source is empty
    summaries = tmp_path / "summaries"
    summaries.mkdir()
    monkeypatch.setattr(
        "src.api.routes.life_v2._summaries_dir_override",
        summaries,
        raising=False,
    )

    app = create_app(mock_brain, DesktopEventBus())
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
class TestLifeV2Scaffold:
    async def test_router_mounted(self, client):
        async with client:
            resp = await client.get("/api/v2/life/ping")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
