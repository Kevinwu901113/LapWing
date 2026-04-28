"""IntentRouter tests — agents-as-tools refactor (2026-04-29).

Two-class classifier: every user turn is either pure chitchat
(``zero_tools``) or "use tools" (``standard``). No domain detection,
no current-info gate. The model picks delegate_to_researcher itself
based on the tool description when external info is needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.intent_router import IntentRouter, RouteDecision


# ── RouteDecision dataclass ───────────────────────────────────────────


def test_route_decision_dataclass_defaults():
    d = RouteDecision(profile_name="standard")
    assert d.profile_name == "standard"
    # Deprecated current-info fields default to neutral values.
    assert d.requires_current_info is False
    assert d.current_info_domain is None
    assert d.required_tool_names == ()


# ── Two-class routing ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_pure_chat_returns_zero_tools():
    """LLM says 'chat' → zero_tools (no tool calls, pure text reply)."""
    router = AsyncMock()
    router.complete.return_value = "chat"
    intent = IntentRouter(router)
    d = await intent.route("chat_1", "今天累死了")
    assert d.profile_name == "zero_tools"
    assert d.requires_current_info is False


@pytest.mark.asyncio
async def test_route_tools_returns_standard():
    router = AsyncMock()
    router.complete.return_value = "tools"
    intent = IntentRouter(router)
    d = await intent.route("chat_1", "需要查个东西")
    assert d.profile_name == "standard"


@pytest.mark.asyncio
async def test_unknown_llm_output_falls_back_to_standard():
    """When the LLM emits something we can't parse, default to
    ``standard``. Conservative — better to over-equip than to leave the
    model unable to act on a real request.
    """
    router = AsyncMock()
    router.complete.return_value = "??? whatever"
    intent = IntentRouter(router)
    d = await intent.route("chat_1", "嗯嗯")
    assert d.profile_name == "standard"


@pytest.mark.asyncio
async def test_route_falls_back_to_standard_on_llm_failure():
    router = AsyncMock()
    router.complete.side_effect = RuntimeError("boom")
    intent = IntentRouter(router)
    d = await intent.route("chat_1", "你好")
    assert d.profile_name == "standard"


# ── Obvious-task short-circuit ───────────────────────────────────────


@pytest.mark.asyncio
async def test_obvious_engineering_task_skips_llm():
    router = AsyncMock()
    intent = IntentRouter(router)
    d = await intent.route("chat_1", "帮我跑 git status")
    assert d.profile_name == "standard"
    # Short-circuit: no LLM call needed for obvious task messages.
    router.complete.assert_not_called()


@pytest.mark.asyncio
async def test_obvious_external_info_query_skips_llm():
    router = AsyncMock()
    intent = IntentRouter(router)
    d = await intent.route("chat_1", "明天天气怎么样")
    assert d.profile_name == "standard"
    router.complete.assert_not_called()


@pytest.mark.asyncio
async def test_obvious_reminder_request_skips_llm():
    router = AsyncMock()
    intent = IntentRouter(router)
    d = await intent.route("chat_1", "明天提醒我开会")
    assert d.profile_name == "standard"
    router.complete.assert_not_called()


# ── Cache behaviour ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_decision_is_cached_and_reused():
    """Plain chat decisions are cached — re-classifying every turn
    when the conversation is casual chitchat is wasteful.
    """
    router = AsyncMock()
    router.complete.return_value = "chat"
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "你好")
    d2 = await intent.route("chat_1", "嗯嗯")
    d3 = await intent.route("chat_1", "好的")

    assert all(d.profile_name == "zero_tools" for d in (d1, d2, d3))
    assert router.complete.call_count == 1


@pytest.mark.asyncio
async def test_obvious_task_breaks_zero_tools_cache():
    """When the cache says zero_tools but the new message clearly
    needs tools, escalate to standard rather than serving the stale
    zero-tools verdict.
    """
    router = AsyncMock()
    router.complete.return_value = "chat"
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "你好")
    assert d1.profile_name == "zero_tools"

    d2 = await intent.route("chat_1", "帮我跑 git status")
    assert d2.profile_name == "standard"


@pytest.mark.asyncio
async def test_external_info_query_breaks_zero_tools_cache():
    """A cached pure-chat decision must not swallow a follow-up
    external-info question. The obvious-task sniff catches the pivot.
    """
    router = AsyncMock()
    router.complete.return_value = "chat"
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "你好")
    assert d1.profile_name == "zero_tools"

    d2 = await intent.route("chat_1", "道奇今天比赛怎么样")
    assert d2.profile_name == "standard"


@pytest.mark.asyncio
async def test_standard_decision_is_cached_too():
    """Standard decisions cache as well — re-classifying mid-task
    flips between profiles unnecessarily.
    """
    router = AsyncMock()
    router.complete.return_value = "tools"
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "需要查个东西")
    d2 = await intent.route("chat_1", "再来一个")
    assert d1.profile_name == d2.profile_name == "standard"
    assert router.complete.call_count == 1


# ── Cache scoping ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_keyed_by_chat_id():
    router = AsyncMock()
    router.complete.side_effect = ["chat", "tools"]
    intent = IntentRouter(router)

    d1 = await intent.route("chat_a", "你好")
    d2 = await intent.route("chat_b", "查个东西")
    assert d1.profile_name == "zero_tools"
    assert d2.profile_name == "standard"
    assert router.complete.call_count == 2
