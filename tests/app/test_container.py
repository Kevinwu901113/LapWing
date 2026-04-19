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
    brain.memory = MagicMock()
    brain.memory.close = AsyncMock()
    brain.memory.get_all_chat_ids = AsyncMock(return_value=[])
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

    with patch.object(container, "_configure_brain_dependencies", new=AsyncMock()):
        await container.start(send_fn=AsyncMock())
        await container.shutdown()

    brain.init_db.assert_awaited_once()
    api_server.start.assert_awaited_once()
    api_server.shutdown.assert_awaited_once()
    brain.memory.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_bus_listener_updates_task_view_store():
    brain = MagicMock()
    brain.init_db = AsyncMock()
    brain.memory = SimpleNamespace(close=AsyncMock())
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
