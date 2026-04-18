"""Verify AppContainer records SYSTEM_STARTED + SYSTEM_STOPPED.

Step 1e of Blueprint v2.0.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.event_bus import DesktopEventBus
from src.app.container import AppContainer
from src.app.task_view import TaskViewStore
from src.logging.state_mutation_log import MutationType, StateMutationLog


async def _open(path: Path) -> StateMutationLog:
    log = StateMutationLog(path)
    await log.init()
    return log


@pytest.mark.asyncio
async def test_container_records_system_started_and_stopped(tmp_path):
    brain = MagicMock()
    brain.init_db = AsyncMock()
    brain.memory = MagicMock()
    brain.memory.close = AsyncMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=[])
    api_server = SimpleNamespace(start=AsyncMock(), shutdown=AsyncMock(), _app=None)
    event_bus = DesktopEventBus()
    task_view_store = TaskViewStore()

    container = AppContainer(
        db_path=tmp_path / "lapwing.db",
        data_dir=tmp_path,
        brain=brain,
        event_bus=event_bus,
        task_view_store=task_view_store,
        api_server=api_server,  # type: ignore[arg-type]
    )

    with patch.object(container, "_configure_brain_dependencies", new=AsyncMock()), \
         patch("config.settings.CONSCIOUSNESS_ENABLED", False):
        await container.start(send_fn=AsyncMock())
        await container.shutdown()

    # mutation_log.db was closed at shutdown — reopen read-only via a fresh instance
    mutation_db = tmp_path / "mutation_log.db"
    assert mutation_db.exists(), "mutation_log.db not created during container lifecycle"

    log = await _open(mutation_db)
    try:
        started = await log.query_by_type(MutationType.SYSTEM_STARTED)
        stopped = await log.query_by_type(MutationType.SYSTEM_STOPPED)
        assert len(started) == 1
        assert len(stopped) == 1
        assert "pid" in started[0].payload
        assert started[0].payload["reason"] == "normal_start"
        assert stopped[0].payload["reason"] == "normal_shutdown"
        # STARTED precedes STOPPED
        assert started[0].timestamp <= stopped[0].timestamp
    finally:
        await log.close()
