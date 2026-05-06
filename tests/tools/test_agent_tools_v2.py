"""Tests for the 5 new agent tools (Blueprint §7)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.budget import BudgetLedger
from src.agents.spec import AgentSpec, AgentLifecyclePolicy
from src.agents.types import AgentResult
from src.tools.agent_tools import (
    delegate_to_agent_executor,
    create_agent_executor,
    destroy_agent_executor,
    save_agent_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(*, registry=None, ledger=None, mutation_log=None):
    services = {
        "dispatcher": MagicMock(),
        "tool_registry": MagicMock(),
        "llm_router": MagicMock(),
        "research_engine": MagicMock(),
        "ambient_store": MagicMock(),
    }
    if registry is not None:
        services["agent_registry"] = registry
    if ledger is not None:
        services["budget_ledger"] = ledger
    if mutation_log is not None:
        services["mutation_log"] = mutation_log
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="test",
        user_id="test",
        auth_level=10,
        chat_id="c1",
        services=services,
    )


def _make_registry_with_agent(agent_name="researcher", *, lifecycle="persistent",
                              builtin=True, run_result=None):
    """Build a fake AgentRegistry whose get_or_create_instance returns a fake agent."""
    registry = MagicMock()
    spec = AgentSpec(
        name=agent_name,
        kind="builtin" if builtin else "dynamic",
        runtime_profile="agent_researcher",
        lifecycle=AgentLifecyclePolicy(mode=lifecycle, max_runs=1),
    )
    fake_agent = MagicMock()
    fake_agent.spec = MagicMock(name=agent_name)
    fake_agent.spec.name = agent_name
    fake_agent.execute = AsyncMock(
        return_value=run_result or AgentResult(
            task_id="t", status="done", result="ok",
        )
    )
    registry.get_or_create_instance = AsyncMock(return_value=fake_agent)
    registry.destroy_agent = AsyncMock(return_value=True)
    registry._ephemeral_agents = {}
    registry._session_agents = {}
    if not builtin and lifecycle == "ephemeral":
        registry._ephemeral_agents[agent_name] = spec
    return registry, fake_agent, spec


# ── delegate_to_agent ──

async def test_delegate_to_agent_success():
    registry, _, _ = _make_registry_with_agent("researcher")
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": "researcher", "task": "find foo"},
    )
    res = await delegate_to_agent_executor(req, ctx)
    assert res.success is True
    assert res.payload["result"] == "ok"


async def test_delegate_to_agent_unknown_name():
    registry = MagicMock()
    registry.get_or_create_instance = AsyncMock(return_value=None)
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": "nonexistent", "task": "x"},
    )
    res = await delegate_to_agent_executor(req, ctx)
    assert res.success is False
    assert "not" in res.reason.lower() or "不可用" in res.reason or "不存在" in res.reason


async def test_delegate_to_agent_charges_delegation_depth():
    """Ledger with max_delegation_depth=1; nested delegation should fail.
    The first delegation enters depth=1 (OK); a hypothetical second would fail,
    but we just check that enter_delegation was called."""
    ledger = BudgetLedger(max_delegation_depth=1)
    registry, _, _ = _make_registry_with_agent("researcher")
    ctx = _make_ctx(registry=registry, ledger=ledger)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": "researcher", "task": "x"},
    )
    res = await delegate_to_agent_executor(req, ctx)
    assert res.success is True
    # After delegation, depth should be back to 0
    assert ledger.snapshot().delegation_depth == 0


async def test_delegate_to_agent_depth_exhausted():
    """If max_delegation_depth=0, even the first call fails (nested delegation)."""
    ledger = BudgetLedger(max_delegation_depth=0)
    registry, _, _ = _make_registry_with_agent("researcher")
    ctx = _make_ctx(registry=registry, ledger=ledger)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": "researcher", "task": "x"},
    )
    res = await delegate_to_agent_executor(req, ctx)
    assert res.success is False


async def test_delegate_passes_context_and_expected_output():
    registry, fake_agent, _ = _make_registry_with_agent("researcher")
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={
            "agent_name": "researcher",
            "task": "find X",
            "context": "user is interested in Y",
            "expected_output": "markdown bullet list",
        },
    )
    await delegate_to_agent_executor(req, ctx)
    # Examine the AgentMessage passed to agent.execute
    call_args = fake_agent.execute.call_args
    msg = call_args.args[0]
    assert msg.context_digest == "user is interested in Y"
    assert "markdown bullet list" in msg.content


# Note: list_agents tool was removed in the agents-as-tools refactor.
# AgentRegistry.list_agents() the *method* is still used by the API
# routes; the LLM-facing tool was removed as it had no real use case.


# ── create_agent ──

async def test_create_agent_success():
    registry = MagicMock()
    spec = AgentSpec(
        name="translator", kind="dynamic",
        runtime_profile="agent_researcher",
    )
    registry.create_agent = AsyncMock(return_value=spec)
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="create_agent",
        arguments={
            "name_hint": "translator",
            "purpose": "translate things",
            "instructions": "you translate text",
            "profile": "agent_researcher",
        },
    )
    res = await create_agent_executor(req, ctx)
    assert res.success is True
    assert res.payload["name"] == "translator"


async def test_create_agent_policy_violation():
    from src.agents.policy import AgentPolicyViolation
    registry = MagicMock()
    registry.create_agent = AsyncMock(
        side_effect=AgentPolicyViolation("unknown_profile",
                                          {"allowed": ["agent_researcher"]})
    )
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="create_agent",
        arguments={
            "name_hint": "x", "purpose": "p", "instructions": "i",
            "profile": "bad_profile",
        },
    )
    res = await create_agent_executor(req, ctx)
    assert res.success is False
    assert "unknown_profile" in res.reason or "policy" in res.reason.lower()


# ── destroy_agent ──

async def test_destroy_agent_success():
    registry = MagicMock()
    registry.destroy_agent = AsyncMock(return_value=True)
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="destroy_agent", arguments={"agent_name": "translator"}
    )
    res = await destroy_agent_executor(req, ctx)
    assert res.success is True


async def test_destroy_agent_builtin_blocked():
    """destroy_agent('researcher') → registry returns False → success=False."""
    registry = MagicMock()
    registry.destroy_agent = AsyncMock(return_value=False)
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="destroy_agent", arguments={"agent_name": "researcher"}
    )
    res = await destroy_agent_executor(req, ctx)
    assert res.success is False


# ── save_agent ──

async def test_save_agent_success():
    registry = MagicMock()
    registry.save_agent = AsyncMock()
    ctx = _make_ctx(registry=registry)
    # First, "delegate" so the helper marks completion
    from src.tools.agent_tools import _completed_delegations
    _completed_delegations["myagent"] = 1
    req = ToolExecutionRequest(
        name="save_agent",
        arguments={"agent_name": "myagent", "reason": "useful"},
    )
    res = await save_agent_executor(req, ctx)
    assert res.success is True
    registry.save_agent.assert_awaited()
    # cleanup
    _completed_delegations.pop("myagent", None)


async def test_save_agent_no_history():
    """save_agent on an agent that never ran → policy rejects."""
    from src.agents.policy import AgentPolicyViolation
    registry = MagicMock()
    registry.save_agent = AsyncMock(
        side_effect=AgentPolicyViolation("save_requires_run_history")
    )
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="save_agent",
        arguments={"agent_name": "ghost", "reason": "x"},
    )
    res = await save_agent_executor(req, ctx)
    assert res.success is False


# ── ephemeral auto-destroy after max_runs ──

async def test_ephemeral_auto_destroy_after_max_runs():
    """An ephemeral agent with max_runs=1 should be destroyed after one delegation."""
    registry, fake_agent, spec = _make_registry_with_agent(
        "ephem", builtin=False, lifecycle="ephemeral",
    )
    spec.lifecycle = AgentLifecyclePolicy(mode="ephemeral", max_runs=1)
    registry._ephemeral_agents["ephem"] = spec
    ctx = _make_ctx(registry=registry)
    req = ToolExecutionRequest(
        name="delegate_to_agent",
        arguments={"agent_name": "ephem", "task": "go"},
    )
    res = await delegate_to_agent_executor(req, ctx)
    assert res.success is True
    registry.destroy_agent.assert_awaited_with("ephem")
