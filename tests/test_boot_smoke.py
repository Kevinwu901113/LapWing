"""Boot and ingress smoke tests for runtime import regressions."""

from __future__ import annotations

import logging
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_main_imports():
    import main  # noqa: F401


def test_main_does_not_import_get_settings_from_compat_layer():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "from config.settings import get_settings" not in source


def test_desktop_ingress_does_not_use_fire_and_forget_tasks():
    source = Path("src/api/routes/chat_ws.py").read_text(encoding="utf-8")
    assert "asyncio.create_task" not in source


@pytest.mark.asyncio
async def test_qq_private_ingress_enqueues_without_import_error(caplog):
    from main import handle_qq_message_for_container
    from src.adapters.base import ChannelType
    from src.adapters.qq_adapter import QQAdapter
    from src.core.chat_activity import ChatActivityTracker
    from src.core.channel_manager import ChannelManager
    from src.core.event_queue import EventQueue
    from src.core.inbound import (
        BusySessionController,
        CommandInterceptLayer,
        InboundMessageGate,
    )

    queue = EventQueue()
    adapter = QQAdapter(config={"self_id": "100", "kevin_id": "200"})
    adapter._mark_as_read = AsyncMock()
    manager = ChannelManager()
    manager.register(ChannelType.QQ, adapter)
    container = SimpleNamespace(
        brain=SimpleNamespace(),
        channel_manager=manager,
        inbound_gate=InboundMessageGate(allow_untrusted=True),
        command_intercept_layer=CommandInterceptLayer(),
        busy_session_controller=BusySessionController(),
        chat_activity_tracker=ChatActivityTracker(),
        event_queue=queue,
        main_loop=None,
        steering_store=None,
    )
    raw_event = {
        "post_type": "message",
        "message_type": "private",
        "user_id": "200",
        "message_id": "ping-1",
        "message": "诊断 ping，收到请回 1",
    }

    caplog.set_level(logging.INFO, logger="lapwing")

    async def on_message(**kwargs):
        await handle_qq_message_for_container(container, **kwargs)

    adapter.on_message = on_message

    await adapter._handle_message_event(raw_event)
    for _ in range(20):
        if queue.qsize():
            break
        await asyncio.sleep(0)

    event = queue.get_nowait()
    assert event is not None
    assert event.chat_id == "200"
    assert event.source_message_id == "ping-1"
    assert "[qq/inbound] accepted chat_id=200" in caplog.text
    assert "[qq/inbound] enqueued chat_id=200" in caplog.text
    assert "ImportError" not in caplog.text
    assert "qq_adapter_task_exception" not in caplog.text
