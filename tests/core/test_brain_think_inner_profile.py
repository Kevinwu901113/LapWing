"""Regression: think_inner must use the dedicated inner_tick RuntimeProfile.

Before this commit, ``think_inner`` reached ``_complete_chat`` which then
asked the IntentRouter to pick a profile from the inner prompt. That made
inner ticks share their tool surface with whatever the router happened to
classify "[Heartbeat]" as — typically ``chat_extended`` — exposing
``create_skill`` and other tools that an autonomous tick must not have.

The fix: ``think_inner`` passes ``profile_override="inner_tick"`` into
``_complete_chat``, which short-circuits IntentRouter and feeds the
companion-aligned ``INNER_TICK_PROFILE`` straight to TaskRuntime.
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


async def test_think_inner_pins_inner_tick_profile(brain):
    """think_inner must hand profile_override='inner_tick' to _complete_chat."""
    brain.trajectory_store = AsyncMock()
    brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])

    captured: dict = {}

    async def fake_render(chat_id, recent, *, inner=False, **kwargs):
        return [{"role": "system", "content": "<sys>"}] + list(recent)

    async def fake_complete(chat_id, messages, user_msg, **kwargs):
        captured["kwargs"] = kwargs
        return "ok [NEXT: 30m]"

    brain._render_messages = fake_render  # type: ignore[method-assign]
    brain._complete_chat = fake_complete  # type: ignore[method-assign]

    await brain.think_inner()

    assert captured["kwargs"].get("profile_override") == "inner_tick", (
        "think_inner must pin the inner_tick profile so IntentRouter does "
        "not silently widen the tool surface for autonomous ticks"
    )


async def test_complete_chat_profile_override_skips_intent_router(brain):
    """_complete_chat with profile_override must not call IntentRouter."""
    from src.core.brain import LapwingBrain
    from src.core.runtime_profiles import INNER_TICK_PROFILE

    routed_calls: list = []

    class _RouterSpy:
        async def route(self, chat_id, message):
            routed_calls.append((chat_id, message))
            return "chat_extended"

    brain.intent_router = _RouterSpy()
    brain.task_runtime = AsyncMock()
    brain.task_runtime.tools_for_profile = lambda name: [{"profile": name}]
    brain.task_runtime.complete_chat = AsyncMock(return_value="done")
    brain.task_runtime.record_pending_confirmation = lambda *a, **k: ""
    brain.event_bus = None
    brain.router = AsyncMock()

    captured: dict = {}

    async def spy_complete_chat(**kwargs):
        captured["profile"] = kwargs.get("profile")
        captured["tools"] = kwargs.get("tools")
        return "done"

    brain.task_runtime.complete_chat = spy_complete_chat

    # Use INTENT_ROUTER_ENABLED=True to make the test meaningful — without
    # an override the router would normally fire.
    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        reply = await LapwingBrain._complete_chat(
            brain,
            chat_id="_inner_tick",
            messages=[{"role": "user", "content": "[Heartbeat]"}],
            user_message="[Heartbeat]",
            profile_override="inner_tick",
        )

    assert reply == "done"
    assert routed_calls == [], (
        "IntentRouter must be skipped when profile_override is set — got "
        f"{routed_calls!r}"
    )
    assert captured["profile"] == "inner_tick"
    assert captured["tools"] == [{"profile": "inner_tick"}]
    # Confirm the override actually selected the dedicated profile
    assert INNER_TICK_PROFILE.name == "inner_tick"


async def test_complete_chat_without_override_still_uses_router(brain):
    """Default path (no override) must still consult IntentRouter."""
    from src.core.brain import LapwingBrain

    routed_calls: list = []

    class _RouterSpy:
        async def route(self, chat_id, message):
            routed_calls.append((chat_id, message))
            return "chat_extended"

    brain.intent_router = _RouterSpy()
    brain.task_runtime = AsyncMock()
    brain.task_runtime.tools_for_profile = lambda name: [{"profile": name}]

    async def spy_complete_chat(**kwargs):
        return "done"

    brain.task_runtime.complete_chat = spy_complete_chat
    brain.task_runtime.record_pending_confirmation = lambda *a, **k: ""
    brain.event_bus = None
    brain.router = AsyncMock()

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin-real",
            messages=[{"role": "user", "content": "你好"}],
            user_message="你好",
        )

    assert len(routed_calls) == 1, (
        "Default _complete_chat must still call IntentRouter — got "
        f"{routed_calls!r}"
    )
