"""Regression: zero-tool fast path for pure-chat turns.

When IntentRouter returns ``profile_name="zero_tools"``,
``_complete_chat`` must hand ``tools=[]`` to ``TaskRuntime.complete_chat``.
TaskRuntime's existing ``if not tools`` branch then dispatches
directly to ``router.complete(slot="main_conversation")``, which means
no tool schemas occupy the model's attention and no tool-call
decision step runs.

Other branches must remain on the tool-call path:
- standard → tools populated
- profile_override (e.g. inner_tick) → tools populated regardless of
  the override name
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from src.logging.state_mutation_log import MutationType


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


async def test_zero_tools_decision_uses_zero_tools(brain):
    """zero_tools → tools=[] (fast path)."""
    from src.core.brain import LapwingBrain
    from src.core.intent_router import RouteDecision

    decision = RouteDecision(profile_name="zero_tools")
    captured = _wire_brain_with_router_decision(brain, decision)

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        reply = await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "在干嘛"}],
            user_message="在干嘛",
        )

    assert reply == "ok"
    assert captured["profile"] == "zero_tools"
    assert captured["tools"] == [], (
        "zero_tools must hit the zero-tool fast path (tools should be []), "
        f"got {captured['tools']!r}"
    )


async def test_standard_keeps_tools(brain):
    """standard must always travel the tool-call path."""
    from src.core.brain import LapwingBrain
    from src.core.intent_router import RouteDecision

    decision = RouteDecision(profile_name="standard")
    captured = _wire_brain_with_router_decision(brain, decision)

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "帮我记一下..."}],
            user_message="帮我记一下...",
        )

    assert captured["tools"] != [], (
        f"standard must keep tools populated, got {captured['tools']!r}"
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
            profile_override="zero_tools",  # contrived: override to zero_tools
        )

    assert captured["tools"] != [], (
        "profile_override must always carry the profile's tool surface — "
        "fast path is only for IntentRouter-decided turns. "
        f"Got {captured['tools']!r}"
    )


@pytest.mark.parametrize("profile_name", ["local_execution", "task_execution"])
async def test_intent_router_return_operator_execution_profiles_are_downgraded_to_standard(brain, profile_name):
    from src.core.brain import LapwingBrain
    from src.core.intent_router import RouteDecision

    decision = RouteDecision(profile_name=profile_name)
    captured = _wire_brain_with_router_decision(brain, decision)
    brain._mutation_log_ref = AsyncMock()

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "请执行本机命令"}],
            user_message="请执行本机命令",
            adapter="qq",
            user_id="guest-user",
        )

    assert captured["profile"] == "standard"


@pytest.mark.parametrize("profile_name", ["local_execution", "task_execution"])
async def test_operator_execution_profiles_require_explicit_override_and_owner_or_agent(brain, profile_name):
    from src.core.brain import LapwingBrain

    captured = _wire_brain_with_router_decision(brain, decision=None)
    brain._mutation_log_ref = AsyncMock()

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "危险操作"}],
            user_message="危险操作",
            profile_override=profile_name,
            adapter="qq",
            user_id="guest-user",
        )

    assert captured["profile"] == "standard"
    brain._mutation_log_ref.record.assert_awaited()
    args = brain._mutation_log_ref.record.await_args.args
    assert args[0] == MutationType.TOOL_DENIED


@pytest.mark.parametrize("profile_name", ["local_execution", "task_execution"])
async def test_operator_execution_profiles_explicit_owner_override_emits_escalation_audit(brain, profile_name):
    from src.core.brain import LapwingBrain

    captured = _wire_brain_with_router_decision(brain, decision=None)
    brain._mutation_log_ref = AsyncMock()

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "危险操作"}],
            user_message="危险操作",
            profile_override=profile_name,
            adapter="agent",
            user_id="agent:coder",
        )

    assert captured["profile"] == profile_name
    calls = brain._mutation_log_ref.record.await_args_list
    assert any(call.args and call.args[0] == MutationType.PROFILE_ESCALATED for call in calls)


@pytest.mark.parametrize("profile_name", ["agent_admin_operator", "identity_operator", "browser_operator", "skill_operator"])
async def test_operator_profiles_require_explicit_override_and_owner_or_agent(brain, profile_name):
    from src.core.brain import LapwingBrain
    from src.core.intent_router import RouteDecision

    decision = RouteDecision(profile_name=profile_name)
    captured = _wire_brain_with_router_decision(brain, decision)
    brain._mutation_log_ref = AsyncMock()

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "危险操作"}],
            user_message="危险操作",
            adapter="qq",
            user_id="guest-user",
        )

    assert captured["profile"] == "standard"
    args = brain._mutation_log_ref.record.await_args.args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["reason"] == f"{profile_name}_requires_explicit_owner_or_agent"


@pytest.mark.parametrize("profile_name", ["agent_admin_operator", "identity_operator", "browser_operator", "skill_operator"])
async def test_operator_profiles_explicit_owner_override_emits_escalation_audit(brain, profile_name):
    from src.core.brain import LapwingBrain

    captured = _wire_brain_with_router_decision(brain, decision=None)
    brain._mutation_log_ref = AsyncMock()

    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        await LapwingBrain._complete_chat(
            brain,
            chat_id="kevin",
            messages=[{"role": "user", "content": "危险操作"}],
            user_message="危险操作",
            profile_override=profile_name,
            adapter="agent",
            user_id="agent:coder",
        )

    assert captured["profile"] == profile_name
    calls = brain._mutation_log_ref.record.await_args_list
    audit_payloads = [call.args[1] for call in calls if call.args and call.args[0] == MutationType.PROFILE_ESCALATED]
    assert audit_payloads, "expected PROFILE_ESCALATED audit"
    assert any(p.get("reason") == f"explicit_{profile_name}_override" for p in audit_payloads)
