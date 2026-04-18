"""Tests for /api/v2/system/* endpoints.

v2.0 Step 1: /api/v2/system/events now reads from StateMutationLog
(mutation_log.db) rather than events_v2.db. Response field names stay
backward-compatible for the desktop frontend.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.event_bus import DesktopEventBus
from src.api.server import create_app
from src.logging.state_mutation_log import MutationType, StateMutationLog


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
    brain._mutation_log_ref = None
    return brain


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(tmp_path / "ml.db", logs_dir=tmp_path / "logs")
    await log.init()
    yield log
    await log.close()


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

    async def test_events_query_from_mutation_log(self, mock_brain, mutation_log):
        await mutation_log.record(
            MutationType.SYSTEM_STARTED,
            {"pid": 1, "reason": "normal_start"},
        )
        mock_brain._mutation_log_ref = mutation_log

        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v2/system/events",
                params={"event_type": "system.started"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        ev = data["events"][0]
        assert ev["event_type"] == "system.started"
        assert ev["payload"] == {"pid": 1, "reason": "normal_start"}
        # task_id not in payload → None
        assert ev["task_id"] is None

    async def test_events_no_mutation_log(self, mock_brain):
        mock_brain._mutation_log_ref = None
        app = create_app(mock_brain, DesktopEventBus())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/system/events")
        assert resp.status_code == 200
        assert resp.json()["events"] == []
