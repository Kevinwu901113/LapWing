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


from src.core.trajectory_store import TrajectoryEntry


def _make_entry(**overrides) -> TrajectoryEntry:
    defaults = dict(
        id=1,
        timestamp=1_776_498_000.0,
        entry_type="assistant_text",
        source_chat_id="desktop:kevin",
        actor="lapwing",
        content={"text": "等我看一下"},
        related_commitment_id=None,
        related_iteration_id="iter_abc",
        related_tool_call_id=None,
    )
    defaults.update(overrides)
    return TrajectoryEntry(**defaults)


@pytest.mark.asyncio
class TestLifeV2Trajectory:
    async def test_returns_items_with_metadata(self, client, mock_brain):
        mock_brain.trajectory_store.list_for_timeline = AsyncMock(return_value=[
            _make_entry(id=2, timestamp=2000.0, entry_type="inner_thought", content={"text": "想 Kevin"}),
            _make_entry(id=1, timestamp=1000.0, entry_type="user_message", content={"text": "hi"}, actor="user"),
        ])

        async with client:
            resp = await client.get("/api/v2/life/trajectory")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["kind"] == "inner_thought"
        assert data["items"][0]["id"] == "traj_2"
        assert data["items"][0]["timestamp"] == 2000.0
        assert data["items"][0]["content"] == "想 Kevin"
        assert data["items"][0]["metadata"]["actor"] == "lapwing"
        assert data["items"][0]["metadata"]["source_chat_id"] == "desktop:kevin"
        assert data["items"][0]["metadata"]["related_iteration_id"] == "iter_abc"

    async def test_limit_forwarded(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            await client.get("/api/v2/life/trajectory", params={"limit": 25})

        kwargs = spy.call_args.kwargs
        assert kwargs["limit"] == 25

    async def test_limit_capped(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            resp = await client.get("/api/v2/life/trajectory", params={"limit": 9999})

        assert resp.status_code == 422  # pydantic le=500

    async def test_entry_types_filter_parsed(self, client, mock_brain):
        spy = AsyncMock(return_value=[])
        mock_brain.trajectory_store.list_for_timeline = spy

        async with client:
            await client.get(
                "/api/v2/life/trajectory",
                params={"entry_types": "assistant_text,user_message"},
            )

        types = spy.call_args.kwargs["entry_types"]
        assert [t.value for t in types] == ["assistant_text", "user_message"]

    async def test_bad_entry_type_returns_400(self, client, mock_brain):
        async with client:
            resp = await client.get(
                "/api/v2/life/trajectory",
                params={"entry_types": "not_a_real_type"},
            )
        assert resp.status_code == 400

    async def test_store_unavailable_returns_empty(self, client, mock_brain):
        mock_brain.trajectory_store = None
        # Re-init the route module to pick up the None store.
        from src.api.routes import life_v2
        life_v2.init(trajectory_store=None)

        async with client:
            resp = await client.get("/api/v2/life/trajectory")

        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_before_ts": None}
