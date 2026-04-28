"""Regression: chat_minimal turns without a current-info requirement must
travel a zero-tool fast path (skip the OpenAI tool-call protocol entirely).

When IntentRouter returns ``profile_name="chat_minimal"`` with
``requires_current_info=False``, ``_complete_chat`` must hand
``tools=[]`` to ``TaskRuntime.complete_chat``. TaskRuntime's existing
``if not tools`` branch then dispatches directly to
``router.complete(slot="main_conversation")``, which means no tool
schemas occupy the model's attention and no tool-call decision step
runs.

Other branches must remain on the tool-call path:
- chat_minimal that was upgraded to current-info → tools populated
- chat_extended (any decision) → tools populated
- profile_override (e.g. inner_tick) → tools populated
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def brain(tmp_path):
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.build_default_tool_registry"), \
         patch("src.core.brain.TaskRuntime"):
        from src.core.brain import LapwingBrain
        b = LapwingBrain(db_path=tmp_path / "x.db")
    return b


def _wire_brain_with_router_decision(brain, decision):
    """Attach a stub IntentRouter that returns ``decision`` and a spying
    task_runtime that captures the tools argument passed to complete_chat."""

    class _RouterStub:
        async def route(self, chat_id, message):
            return decision

    brain.intent_router = _RouterStub()
    brain.task_runtime = AsyncMock()
    brain.task_runtime.tools_for_profile = lambda name: [
        {"type": "function", "function": {"name": f"stub_for_{name}"}}
    ]
    brain.task_runtime.record_pending_confirmation = lambda *a, **k: ""
    brain.event_bus = None
    brain.router = AsyncMock()

    captured: dict = {}

    async def spy_complete_chat(**kwargs):
        captured["tools"] = kwargs.get("tools")
        captured["profile"] = kwargs.get("profile")
        return "ok"

    brain.task_runtime.complete_chat = spy_complete_chat
    return captured


async def test_chat_minimal_without_current_info_uses_zero_tools(brain):
    """chat_minimal + requires_current_info=False → tools=[] (fast path)."""
    from src.core.brain import LapwingBrain
    from src.core.intent_router import RouteDecision

    decision = RouteDecision(
        profile_name="chat_minimal",
        requires_current_info=False,
    )
    captured = _wire_brain_with_router_decision(brain, decision)

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        reply = await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "在干嘛"}],
            user_message="在干嘛",
        )

    assert reply == "ok"
    assert captured["profile"] == "chat_minimal"
    assert captured["tools"] == [], (
        "chat_minimal without current-info must hit the zero-tool fast path "
        f"(tools should be []), got {captured['tools']!r}"
    )


async def test_chat_minimal_with_current_info_keeps_tools(brain):
    """If IntentRouter sets requires_current_info=True, the fast path must
    NOT engage even when category=chat_minimal — the model needs the tools
    to satisfy the required-tool gate."""
    from src.core.brain import LapwingBrain
    from src.core.intent_router import RouteDecision

    # Mirror the upgrade IntentRouter actually performs (chat_minimal +
    # current-info domain → it overrides profile to chat_extended).
    # We test the contract one level up: even if the profile somehow
    # remained chat_minimal AND requires_current_info=True, tools must
    # not be cleared.
    decision = RouteDecision(
        profile_name="chat_minimal",
        requires_current_info=True,
        current_info_domain="sports",
        required_tool_names=("get_sports_score",),
    )
    captured = _wire_brain_with_router_decision(brain, decision)

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "道奇今天比分"}],
            user_message="道奇今天比分",
        )

    assert captured["tools"] != [], (
        "current-info turns must keep tools populated even on chat_minimal — "
        f"got {captured['tools']!r}"
    )


async def test_chat_extended_keeps_tools(brain):
    """chat_extended must always travel the tool-call path."""
    from src.core.brain import LapwingBrain
    from src.core.intent_router import RouteDecision

    decision = RouteDecision(
        profile_name="chat_extended",
        requires_current_info=False,
    )
    captured = _wire_brain_with_router_decision(brain, decision)

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "帮我记一下..."}],
            user_message="帮我记一下...",
        )

    assert captured["tools"] != [], (
        f"chat_extended must keep tools populated, got {captured['tools']!r}"
    )


async def test_profile_override_bypasses_zero_tools_path(brain):
    """profile_override (e.g. inner_tick) must keep its tools regardless of
    profile name — overrides are explicit caller contracts and the fast
    path's IntentRouter-based heuristic must not interfere."""
    from src.core.brain import LapwingBrain

    # No IntentRouter call expected (profile_override short-circuits it).
    captured = _wire_brain_with_router_decision(
        brain, decision=None  # not used; intent_router won't be called
    )

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="_inner_tick",
            messages=[{"role": "user", "content": "[Heartbeat]"}],
            user_message="[Heartbeat]",
            profile_override="chat_minimal",  # contrived: override to chat_minimal
        )

    assert captured["tools"] != [], (
        "profile_override must always carry the profile's tool surface — "
        "fast path is only for IntentRouter-decided chat_minimal turns. "
        f"Got {captured['tools']!r}"
    )
