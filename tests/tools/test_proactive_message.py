"""send_proactive_message 工具处理器测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.handlers import send_proactive_message
from src.tools.types import ToolExecutionRequest


@pytest.mark.asyncio
async def test_sends_message_via_channel_manager():
    req = ToolExecutionRequest(name="send_proactive_message", arguments={"message": "hello"})
    channel_manager = AsyncMock()
    ctx = MagicMock()
    ctx.services = {"channel_manager": channel_manager}

    result = await send_proactive_message(req, ctx)

    assert result.success is True
    channel_manager.send_to_owner.assert_awaited_once_with("hello")


@pytest.mark.asyncio
async def test_fails_on_empty_message():
    req = ToolExecutionRequest(name="send_proactive_message", arguments={"message": ""})
    ctx = MagicMock()
    ctx.services = {}

    result = await send_proactive_message(req, ctx)

    assert result.success is False
    assert "空" in result.reason


@pytest.mark.asyncio
async def test_fails_without_channel_manager():
    req = ToolExecutionRequest(name="send_proactive_message", arguments={"message": "hi"})
    ctx = MagicMock()
    ctx.services = {}

    result = await send_proactive_message(req, ctx)

    assert result.success is False
    assert "通道" in result.reason
