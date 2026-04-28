from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.intent_router import IntentRouter, RouteDecision


# ── T1: RouteDecision dataclass shape ─────────────────────────────────


def test_route_decision_dataclass_defaults():
    d = RouteDecision(profile_name="chat_extended")
    assert d.profile_name == "chat_extended"
    assert d.requires_current_info is False
    assert d.current_info_domain is None
    assert d.required_tool_names == ()


def test_route_decision_with_current_info():
    d = RouteDecision(
        profile_name="chat_extended",
        requires_current_info=True,
        current_info_domain="sports",
        required_tool_names=("get_sports_score", "research"),
    )
    assert d.requires_current_info is True
    assert "get_sports_score" in d.required_tool_names


# ── T2: IntentRouter returns RouteDecision (existing tests adapted) ───


@pytest.mark.asyncio
async def test_route_chat_minimal():
    router = AsyncMock()
    router.complete.return_value = "chat none"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "今天累死了")
    assert decision.profile_name == "chat_minimal"
    assert decision.requires_current_info is False


@pytest.mark.asyncio
async def test_route_extended():
    router = AsyncMock()
    router.complete.return_value = "chat_extended none"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "明天天气怎么样")
    assert decision.profile_name == "chat_extended"


@pytest.mark.asyncio
async def test_route_task_execution():
    router = AsyncMock()
    router.complete.return_value = "task none"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "帮我跑一下 pytest")
    assert decision.profile_name == "task_execution"


@pytest.mark.asyncio
async def test_fallback_on_uncertainty():
    router = AsyncMock()
    router.complete.return_value = "huh???"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "...")
    assert decision.profile_name == "chat_extended"
    assert decision.requires_current_info is False


@pytest.mark.asyncio
async def test_fallback_on_llm_failure():
    router = AsyncMock()
    router.complete.side_effect = Exception("LLM down")
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "anything")
    assert decision.profile_name == "chat_extended"
    assert decision.requires_current_info is False


# ── T3: current-info domain detection ─────────────────────────────────


@pytest.mark.asyncio
async def test_route_sports_detection():
    router = AsyncMock()
    router.complete.return_value = "chat_extended sports"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "道奇今天比赛怎么样")
    assert decision.profile_name == "chat_extended"
    assert decision.requires_current_info is True
    assert decision.current_info_domain == "sports"
    assert "get_sports_score" in decision.required_tool_names
    assert "research" in decision.required_tool_names


@pytest.mark.asyncio
async def test_route_weather_detection():
    router = AsyncMock()
    router.complete.return_value = "chat_extended weather"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "明天会下雨吗")
    assert decision.requires_current_info is True
    assert decision.current_info_domain == "weather"
    assert "research" in decision.required_tool_names


@pytest.mark.asyncio
async def test_route_news_detection():
    router = AsyncMock()
    router.complete.return_value = "chat_extended news"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "最新消息")
    assert decision.requires_current_info is True
    assert decision.current_info_domain == "news"


@pytest.mark.asyncio
async def test_route_price_detection():
    router = AsyncMock()
    router.complete.return_value = "chat_extended price"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "比特币现在多少钱")
    assert decision.requires_current_info is True
    assert decision.current_info_domain == "price"


@pytest.mark.asyncio
async def test_route_no_current_info():
    router = AsyncMock()
    router.complete.return_value = "chat none"
    intent = IntentRouter(router)
    decision = await intent.route("chat_1", "今天心情不好")
    assert decision.requires_current_info is False
    assert decision.required_tool_names == ()


# ── T4: session stickiness with RouteDecision ─────────────────────────


@pytest.mark.asyncio
async def test_session_stickiness():
    router = AsyncMock()
    router.complete.return_value = "chat_extended none"
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "查个天气")
    d2 = await intent.route("chat_1", "再问一下")
    assert d1.profile_name == d2.profile_name == "chat_extended"
    assert router.complete.call_count == 1


@pytest.mark.asyncio
async def test_session_stickiness_with_current_info():
    router = AsyncMock()
    router.complete.return_value = "chat_extended sports"
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "道奇今天比赛")
    assert d1.requires_current_info is True

    d2 = await intent.route("chat_1", "几点开始")
    assert d2.profile_name == d1.profile_name
    # Cached domain carries over too — caller can re-use the requirement
    # for follow-up questions in the same conversation context.
    assert d2.requires_current_info is True
    assert router.complete.call_count == 1


@pytest.mark.asyncio
async def test_current_info_breaks_chat_cache():
    """A cached chat decision (no current_info) must not silently swallow
    a follow-up real-time question. The keyword sniff in route() should
    force a re-classification when the new message looks like sports/
    weather/news/price even though the cache says plain chat."""
    router = AsyncMock()
    router.complete.side_effect = ["chat none", "chat_extended sports"]
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "你好")
    assert d1.requires_current_info is False

    d2 = await intent.route("chat_1", "道奇今天比赛怎么样")
    assert d2.requires_current_info is True
    assert d2.current_info_domain == "sports"
    assert router.complete.call_count == 2


@pytest.mark.asyncio
async def test_obvious_task_breaks_session_stickiness():
    router = AsyncMock()
    router.complete.side_effect = ["chat none", "task none"]
    intent = IntentRouter(router)

    d1 = await intent.route("chat_1", "你好")
    assert d1.profile_name == "chat_minimal"

    d2 = await intent.route("chat_1", "帮我跑 git status")
    assert d2.profile_name == "task_execution"


# ── T9: _parse_decision edge cases ────────────────────────────────────


def test_parse_decision_unknown_domain():
    router = IntentRouter(llm_router=AsyncMock())
    d = router._parse_decision("chat_extended unknown_thing")
    assert d.profile_name == "chat_extended"
    assert d.requires_current_info is False


def test_parse_decision_single_word():
    router = IntentRouter(llm_router=AsyncMock())
    d = router._parse_decision("chat")
    assert d.profile_name == "chat_minimal"
    assert d.requires_current_info is False


def test_parse_decision_garbage():
    router = IntentRouter(llm_router=AsyncMock())
    d = router._parse_decision("???")
    assert d.profile_name == "chat_extended"
    assert d.requires_current_info is False


def test_parse_decision_empty():
    router = IntentRouter(llm_router=AsyncMock())
    d = router._parse_decision("")
    assert d.profile_name == "chat_extended"
    assert d.requires_current_info is False


def test_domain_forces_profile_upgrade():
    """If the LLM picks chat_minimal but the domain needs a real tool, the
    profile must be upgraded to chat_extended — chat_minimal exposes
    nothing that can satisfy the gate, so leaving it would guarantee the
    fallback fires every time."""
    router = IntentRouter(llm_router=AsyncMock())
    d = router._parse_decision("chat sports")
    assert d.profile_name == "chat_extended"
    assert d.requires_current_info is True
    assert d.current_info_domain == "sports"
