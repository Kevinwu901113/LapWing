"""AppContainer 测试。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.event_bus import DesktopEventBus
from src.app.container import AppContainer
from src.app.task_view import TaskViewStore


@pytest.mark.asyncio
async def test_start_and_shutdown_calls_lifecycle_components():
    brain = MagicMock()
    brain.init_db = AsyncMock()
    brain.fact_extractor = SimpleNamespace(shutdown=AsyncMock())
    brain.memory = MagicMock()
    brain.memory.close = AsyncMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=[])
    brain.interest_tracker = SimpleNamespace(shutdown=AsyncMock())
    api_server = SimpleNamespace(start=AsyncMock(), shutdown=AsyncMock(), _app=None)
    event_bus = DesktopEventBus()
    task_view_store = TaskViewStore()

    container = AppContainer(
        db_path=Path("test.db"),
        data_dir=Path("data"),
        brain=brain,
        event_bus=event_bus,
        task_view_store=task_view_store,
        api_server=api_server,  # type: ignore[arg-type]
    )

    heartbeat = SimpleNamespace(start=MagicMock(), shutdown=AsyncMock())
    with patch.object(container, "_configure_brain_dependencies", new=AsyncMock()), \
         patch.object(container, "_build_heartbeat", return_value=heartbeat), \
         patch("config.settings.CONSCIOUSNESS_ENABLED", False), \
         patch("config.settings.HEARTBEAT_ENABLED", True):
        await container.start(send_fn=AsyncMock())
        assert container.reminder_scheduler is not None
        assert brain.reminder_scheduler is container.reminder_scheduler
        await container.shutdown()
        assert container.reminder_scheduler is None

    brain.init_db.assert_awaited_once()
    api_server.start.assert_awaited_once()
    heartbeat.start.assert_called_once()
    heartbeat.shutdown.assert_awaited_once()
    api_server.shutdown.assert_awaited_once()
    brain.interest_tracker.shutdown.assert_awaited_once()
    brain.fact_extractor.shutdown.assert_awaited_once()
    brain.memory.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_bus_listener_updates_task_view_store():
    brain = MagicMock()
    brain.init_db = AsyncMock()
    brain.fact_extractor = SimpleNamespace(shutdown=AsyncMock())
    brain.memory = SimpleNamespace(close=AsyncMock())
    brain.interest_tracker = None
    api_server = SimpleNamespace(start=AsyncMock(), shutdown=AsyncMock())

    event_bus = DesktopEventBus()
    task_view_store = TaskViewStore()
    container = AppContainer(
        db_path=Path("test.db"),
        data_dir=Path("data"),
        brain=brain,
        event_bus=event_bus,
        task_view_store=task_view_store,
        api_server=api_server,  # type: ignore[arg-type]
    )

    await event_bus.publish(
        "task.started",
        {
            "task_id": "task_1",
            "chat_id": "c1",
            "phase": "started",
            "text": "start",
        },
    )

    task = await container.task_view_store.get_task("task_1")
    assert task is not None
    assert task["status"] == "started"
