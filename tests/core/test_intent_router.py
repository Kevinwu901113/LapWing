from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.intent_router import IntentRouter


@pytest.mark.asyncio
async def test_route_chat_minimal():
    router = AsyncMock()
    router.complete.return_value = "chat"
    intent = IntentRouter(router)
    assert await intent.route("chat_1", "今天累死了") == "chat_minimal"


@pytest.mark.asyncio
async def test_route_extended():
    router = AsyncMock()
    router.complete.return_value = "chat_extended"
    intent = IntentRouter(router)
    assert await intent.route("chat_1", "明天天气怎么样") == "chat_extended"


@pytest.mark.asyncio
async def test_route_task_execution():
    router = AsyncMock()
    router.complete.return_value = "task"
    intent = IntentRouter(router)
    assert await intent.route("chat_1", "帮我跑一下 pytest") == "task_execution"


@pytest.mark.asyncio
async def test_obvious_task_breaks_session_stickiness():
    router = AsyncMock()
    router.complete.side_effect = ["chat", "task"]
    intent = IntentRouter(router)

    assert await intent.route("chat_1", "你好") == "chat_minimal"
    assert await intent.route("chat_1", "帮我跑 git status") == "task_execution"


@pytest.mark.asyncio
async def test_fallback_on_uncertainty():
    router = AsyncMock()
    router.complete.return_value = "huh???"
    intent = IntentRouter(router)
    assert await intent.route("chat_1", "...") == "chat_extended"


@pytest.mark.asyncio
async def test_fallback_on_llm_failure():
    router = AsyncMock()
    router.complete.side_effect = Exception("LLM down")
    intent = IntentRouter(router)
    assert await intent.route("chat_1", "anything") == "chat_extended"


@pytest.mark.asyncio
async def test_session_stickiness():
    router = AsyncMock()
    router.complete.return_value = "chat_extended"
    intent = IntentRouter(router)

    assert await intent.route("chat_1", "查个天气") == "chat_extended"
    assert await intent.route("chat_1", "再问一下") == "chat_extended"
    assert router.complete.call_count == 1
