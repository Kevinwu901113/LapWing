"""AppContainer 测试。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.event_bus import DesktopEventBus
from src.app.container import AppContainer
from src.app.task_view import TaskViewStore
from src.core.browser_guard import BrowserGuard
from src.core.proactive_message_gate import ProactiveMessageGate


@pytest.mark.asyncio
async def test_start_and_shutdown_calls_lifecycle_components(tmp_path):
    brain = MagicMock()
    brain.init_db = AsyncMock()
    api_server = SimpleNamespace(start=AsyncMock(), shutdown=AsyncMock(), _app=None)
    event_bus = DesktopEventBus()
    task_view_store = TaskViewStore()

    container = AppContainer(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
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


@pytest.mark.asyncio
async def test_event_bus_listener_updates_task_view_store():
    brain = MagicMock()
    brain.init_db = AsyncMock()
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


def test_proactive_message_gate_constructed_in_container():
    """AppContainer must build a ProactiveMessageGate from settings so the
    send_message executor can consult it on proactive sends in production."""
    brain = MagicMock()
    container = AppContainer(
        db_path=Path("test.db"),
        data_dir=Path("data"),
        brain=brain,
        event_bus=DesktopEventBus(),
        task_view_store=TaskViewStore(),
        api_server=SimpleNamespace(start=AsyncMock(), shutdown=AsyncMock(), _app=None),  # type: ignore[arg-type]
    )
    assert isinstance(container.proactive_message_gate, ProactiveMessageGate)
    # Brain must hold a ref so _complete_chat can stamp it into services.
    assert brain._proactive_message_gate_ref is container.proactive_message_gate


def test_brain_services_dict_includes_proactive_message_gate():
    """LapwingBrain._build_services() must emit
    ``services["proactive_message_gate"]`` once the AppContainer wires the
    ref. send_message reads from this dict in production."""
    from src.core.brain import LapwingBrain

    brain = LapwingBrain.__new__(LapwingBrain)
    # Minimal stubs — all _build_services reads are getattr() guarded.
    brain.trajectory_store = None
    brain.focus_manager = None
    brain.reminder_scheduler = None
    brain.channel_manager = None
    brain.tool_registry = None
    brain.event_bus = None
    brain.router = MagicMock()
    brain.browser_manager = None
    gate = ProactiveMessageGate(enabled=True)
    brain._proactive_message_gate_ref = gate

    services = brain._build_services()
    assert services["proactive_message_gate"] is gate


@pytest.mark.asyncio
async def test_browser_guard_built_when_browser_enabled():
    """BrowserGuard must be installed when browser.enabled=true. With the
    guard absent, TaskRuntime refuses every browser_* call (fail-safe)."""
    brain = MagicMock()
    brain.task_runtime = MagicMock()
    api_server = SimpleNamespace(start=AsyncMock(), shutdown=AsyncMock(), _app=None)
    container = AppContainer(
        db_path=Path("test.db"),
        data_dir=Path("data"),
        brain=brain,
        event_bus=DesktopEventBus(),
        task_view_store=TaskViewStore(),
        api_server=api_server,  # type: ignore[arg-type]
    )

    # Force BROWSER_ENABLED=True for this test by monkeypatching the symbol
    # the container reads at prepare() time.
    fake_bm = MagicMock()
    fake_bm.start = AsyncMock()
    fake_bm.set_proxy_router = MagicMock()
    fake_bm.set_router = MagicMock()
    fake_bm.set_event_bus = MagicMock()
    fake_bm.set_browser_guard = MagicMock()
    with patch("src.app.container.BROWSER_ENABLED", True), \
         patch("src.app.container.PHASE0_MODE", ""), \
         patch("src.core.browser_manager.BrowserManager", return_value=fake_bm):
        # Stub the rest of prepare() so we only run _init_browser.
        await container._init_browser()

    assert isinstance(container._browser_guard, BrowserGuard)
    # The same guard must be passed into BrowserManager + TaskRuntime so
    # both layers share one budget counter / blacklist.
    fake_bm.set_browser_guard.assert_called_with(container._browser_guard)
    brain.task_runtime.set_browser_guard.assert_called_with(container._browser_guard)
