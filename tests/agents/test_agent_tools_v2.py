"""Tests for shared delegate helper + delegate_to_agent with freshness_hint.

Covers:
  - _execute_delegation shared helper validation
  - delegate_to_agent freshness_hint propagation
  - Shim ⇔ delegate_to_agent equivalence (same _run_agent args)
  - Backward compat: legacy `request` param, missing optional fields
  - Schema: delegate_to_agent includes freshness_hint
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.types import AgentMessage, AgentResult
from src.tools.agent_tools import (
    _execute_delegation,
    delegate_to_agent_executor,
    delegate_to_coder_executor,
    delegate_to_researcher_executor,
    list_agents_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult


def _make_ctx(*, agent_name: str = "test_agent"):
    registry = MagicMock()
    fake_agent = MagicMock()
    fake_agent.spec = MagicMock()
    fake_agent.spec.name = agent_name
    fake_agent.execute = AsyncMock(
        return_value=AgentResult(
            task_id="t1", status="done", result="ok", artifacts=[], evidence=[],
        )
    )
    registry.get_or_create_instance = AsyncMock(return_value=fake_agent)
    registry._ephemeral_agents = {}
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="test",
        user_id="test",
        auth_level=10,
        chat_id="c1",
        services={
            "agent_registry": registry,
            "dispatcher": MagicMock(),
            "tool_registry": MagicMock(),
            "llm_router": MagicMock(),
            "research_engine": MagicMock(),
            "ambient_store": MagicMock(),
        },
    )


# ── _execute_delegation shared helper ─────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_delegation_rejects_empty_task():
    ctx = _make_ctx()
    result = await _execute_delegation(
        agent_name="researcher", task="", ctx=ctx,
    )
    assert not result.success
    assert "task" in result.reason


@pytest.mark.asyncio
async def test_execute_delegation_rejects_whitespace_task():
    ctx = _make_ctx()
    result = await _execute_delegation(
        agent_name="researcher", task="   ", ctx=ctx,
    )
    assert not result.success


@pytest.mark.asyncio
async def test_execute_delegation_passes_all_params_to_run_agent():
    ctx = _make_ctx()
    with patch("src.tools.agent_tools._run_agent", new=AsyncMock(
        return_value=ToolExecutionResult(success=True, payload={}, reason="ok")
    )) as mock_run:
        await _execute_delegation(
            agent_name="researcher",
            task="find foo",
            ctx=ctx,
            context="some context",
            expected_output="json",
            freshness_hint="realtime",
            parent_task_id="parent_1",
        )
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["agent_name"] == "researcher"
        assert kwargs["request"] == "find foo"
        assert kwargs["context_digest"] == "some context"
        assert kwargs["expected_output"] == "json"
        assert kwargs["freshness_hint"] == "realtime"
        assert kwargs["parent_task_id"] == "parent_1"


# ── delegate_to_agent freshness_hint ──────────────────────────────────────

@pytest.mark.asyncio
async def test_delegate_to_agent_propagates_freshness_hint():
    ctx = _make_ctx(agent_name="researcher")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={
                "agent_name": "researcher",
                "task": "现在比分",
                "freshness_hint": "realtime",
            },
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert isinstance(msg, AgentMessage)
    assert msg.freshness_hint == "realtime"


@pytest.mark.asyncio
async def test_delegate_to_agent_omitted_freshness_hint_is_none():
    ctx = _make_ctx(agent_name="researcher")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent_name": "researcher", "task": "解释 RAG"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.freshness_hint is None


@pytest.mark.asyncio
async def test_delegate_to_agent_empty_freshness_hint_is_none():
    ctx = _make_ctx(agent_name="researcher")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={
                "agent_name": "researcher",
                "task": "x",
                "freshness_hint": "",
            },
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.freshness_hint is None


# ── Shim ⇔ delegate_to_agent equivalence (same _run_agent args) ───────────

@pytest.mark.asyncio
async def test_researcher_shim_and_delegate_to_agent_produce_same_run_agent_args():
    """researcher shim → _run_agent 与 delegate_to_agent → _run_agent 参数等价."""
    with patch("src.tools.agent_tools._run_agent", new=AsyncMock(
        return_value=ToolExecutionResult(success=True, payload={}, reason="ok")
    )) as mock_run:
        ctx = _make_ctx(agent_name="researcher")

        # Via shim
        await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={
                    "task": "find foo",
                    "context_digest": "some bg",
                    "freshness_hint": "realtime",
                },
            ),
            ctx,
        )
        shim_kwargs = mock_run.call_args.kwargs
        mock_run.reset_mock()

        # Via delegate_to_agent
        await delegate_to_agent_executor(
            ToolExecutionRequest(
                name="delegate_to_agent",
                arguments={
                    "agent_name": "researcher",
                    "task": "find foo",
                    "context": "some bg",
                    "freshness_hint": "realtime",
                },
            ),
            ctx,
        )
        agent_kwargs = mock_run.call_args.kwargs

        assert shim_kwargs["agent_name"] == agent_kwargs["agent_name"] == "researcher"
        assert shim_kwargs["request"] == agent_kwargs["request"] == "find foo"
        assert shim_kwargs["context_digest"] == agent_kwargs["context_digest"] == "some bg"
        assert shim_kwargs["freshness_hint"] == agent_kwargs["freshness_hint"] == "realtime"


@pytest.mark.asyncio
async def test_coder_shim_and_delegate_to_agent_produce_same_run_agent_args():
    """coder shim → _run_agent 与 delegate_to_agent → _run_agent 参数等价."""
    with patch("src.tools.agent_tools._run_agent", new=AsyncMock(
        return_value=ToolExecutionResult(success=True, payload={}, reason="ok")
    )) as mock_run:
        ctx = _make_ctx(agent_name="coder")

        await delegate_to_coder_executor(
            ToolExecutionRequest(
                name="delegate_to_coder",
                arguments={"task": "write hello.py", "context_digest": "project needs x"},
            ),
            ctx,
        )
        shim_kwargs = mock_run.call_args.kwargs
        mock_run.reset_mock()

        await delegate_to_agent_executor(
            ToolExecutionRequest(
                name="delegate_to_agent",
                arguments={"agent_name": "coder", "task": "write hello.py", "context": "project needs x"},
            ),
            ctx,
        )
        agent_kwargs = mock_run.call_args.kwargs

        assert shim_kwargs["agent_name"] == agent_kwargs["agent_name"] == "coder"
        assert shim_kwargs["request"] == agent_kwargs["request"] == "write hello.py"
        assert shim_kwargs["context_digest"] == agent_kwargs["context_digest"] == "project needs x"


# ── Backward compat: legacy `request` param ───────────────────────────────

@pytest.mark.asyncio
async def test_researcher_shim_accepts_legacy_request_param():
    ctx = _make_ctx(agent_name="researcher")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "old style task"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.content == "old style task"


@pytest.mark.asyncio
async def test_coder_shim_accepts_legacy_request_param():
    ctx = _make_ctx(agent_name="coder")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    await delegate_to_coder_executor(
        ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"request": "old style code task"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.content == "old style code task"


# ── Backward compat: missing optional fields ──────────────────────────────

@pytest.mark.asyncio
async def test_researcher_shim_missing_context_digest_still_works():
    ctx = _make_ctx(agent_name="researcher")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    result = await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"task": "find foo"},
        ),
        ctx,
    )
    assert result.success
    msg = fake_agent.execute.call_args.args[0]
    assert msg.content == "find foo"


@pytest.mark.asyncio
async def test_coder_shim_missing_context_digest_still_works():
    ctx = _make_ctx(agent_name="coder")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    result = await delegate_to_coder_executor(
        ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"task": "write foo.py"},
        ),
        ctx,
    )
    assert result.success
    msg = fake_agent.execute.call_args.args[0]
    assert msg.content == "write foo.py"


# ── Schema tests ──────────────────────────────────────────────────────────

def test_delegate_to_agent_schema_includes_freshness_hint():
    from src.tools.registry import ToolRegistry
    from src.tools.agent_tools import register_agent_tools
    registry = ToolRegistry()
    register_agent_tools(registry)
    spec = registry.get("delegate_to_agent")
    assert spec is not None
    schema = spec.json_schema
    assert "freshness_hint" in schema["properties"]
    assert schema["properties"]["freshness_hint"]["enum"] == ["realtime", "recent", "anytime"]
    assert "freshness_hint" not in schema.get("required", [])


def test_delegate_to_agent_schema_does_not_require_freshness_hint():
    from src.tools.registry import ToolRegistry
    from src.tools.agent_tools import register_agent_tools
    registry = ToolRegistry()
    register_agent_tools(registry)
    spec = registry.get("delegate_to_agent")
    assert spec.json_schema["required"] == ["agent_name", "task"]


# ── list_agents tool tests ─────────────────────────────────────────────────


def _make_list_agents_ctx(*, registry=None):
    """Build a ToolExecutionContext with an optional agent_registry."""
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="test",
        user_id="test",
        auth_level=10,
        chat_id="c1",
        services={"agent_registry": registry} if registry else {},
    )


@pytest.mark.asyncio
async def test_list_agents_registry_missing():
    """A. list_agents returns failure when agent_registry is not in services."""
    ctx = _make_list_agents_ctx(registry=None)
    result = await list_agents_executor(
        ToolExecutionRequest(name="list_agents", arguments={}), ctx,
    )
    assert not result.success
    assert "unavailable" in result.reason


@pytest.mark.asyncio
async def test_list_agents_compact_returns_summaries():
    """B. compact listing returns name/kind/lifecycle/status/profile/model_slot."""
    registry = MagicMock()
    registry.list_agents = AsyncMock(return_value=[
        {"name": "researcher", "kind": "builtin", "status": "active",
         "description": "searches the web", "runtime_profile": "agent_researcher",
         "lifecycle_mode": "persistent", "model_slot": "agent_researcher"},
        {"name": "coder", "kind": "builtin", "status": "active",
         "description": "writes code", "runtime_profile": "agent_coder",
         "lifecycle_mode": "persistent", "model_slot": "agent_coder"},
    ])
    ctx = _make_list_agents_ctx(registry=registry)
    result = await list_agents_executor(
        ToolExecutionRequest(name="list_agents", arguments={}), ctx,
    )
    assert result.success
    assert result.payload["count"] == 2
    for agent in result.payload["agents"]:
        for key in ("name", "kind", "lifecycle_mode", "status", "description",
                    "runtime_profile", "model_slot"):
            assert key in agent, f"missing key '{key}'"
        assert "system_prompt" not in agent


@pytest.mark.asyncio
async def test_list_agents_full_returns_extra_metadata():
    """C. full=True returns resource_limits and lifecycle detail."""
    registry = MagicMock()
    registry.list_agents = AsyncMock(return_value=[
        {"name": "researcher", "kind": "builtin", "status": "active",
         "description": "searches", "runtime_profile": "agent_researcher",
         "lifecycle_mode": "persistent", "model_slot": "agent_researcher",
         "lifecycle": {"mode": "persistent", "ttl_seconds": None, "max_runs": None},
         "resource_limits": {"max_tool_calls": 20, "max_llm_calls": 10,
                             "max_tokens": 10000, "max_wall_time_seconds": 300},
         "created_reason": ""},
    ])
    ctx = _make_list_agents_ctx(registry=registry)
    result = await list_agents_executor(
        ToolExecutionRequest(name="list_agents", arguments={"full": True}), ctx,
    )
    assert result.success
    agent = result.payload["agents"][0]
    assert "resource_limits" in agent
    assert "lifecycle" in agent
    # system_prompt_preview is fine in full mode (truncated), but never raw system_prompt
    assert "system_prompt" not in agent


@pytest.mark.asyncio
async def test_list_agents_include_inactive():
    """D. include_inactive=True forwards to registry; default is False."""
    registry = MagicMock()
    registry.list_agents = AsyncMock(return_value=[])

    # Default: include_inactive=False
    ctx = _make_list_agents_ctx(registry=registry)
    await list_agents_executor(
        ToolExecutionRequest(name="list_agents", arguments={}), ctx,
    )
    registry.list_agents.assert_called_with(include_inactive=False, full=False)

    registry.list_agents.reset_mock()

    # include_inactive=True
    await list_agents_executor(
        ToolExecutionRequest(
            name="list_agents",
            arguments={"include_inactive": True, "full": True},
        ), ctx,
    )
    registry.list_agents.assert_called_with(include_inactive=True, full=True)


@pytest.mark.asyncio
async def test_list_agents_registry_exception_fails_closed():
    """When registry.list_agents raises, return failure (not unhandled)."""
    registry = MagicMock()
    registry.list_agents = AsyncMock(side_effect=RuntimeError("db down"))
    ctx = _make_list_agents_ctx(registry=registry)
    result = await list_agents_executor(
        ToolExecutionRequest(name="list_agents", arguments={}), ctx,
    )
    assert not result.success
    assert "list_agents failed" in result.reason


# ── Profile exposure tests ──────────────────────────────────────────────────


def test_list_agents_in_standard_profile():
    """E1. list_agents is available in STANDARD_PROFILE (main chat)."""
    from src.core.runtime_profiles import STANDARD_PROFILE
    assert "list_agents" in STANDARD_PROFILE.tool_names


def test_list_agents_in_task_execution_profile():
    """E2. list_agents is available in TASK_EXECUTION_PROFILE."""
    from src.core.runtime_profiles import TASK_EXECUTION_PROFILE
    assert "list_agents" in TASK_EXECUTION_PROFILE.tool_names


def test_list_agents_not_in_agent_researcher_profile():
    """E3. Dynamic agent (researcher profile) must NOT have list_agents."""
    from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE
    assert "list_agents" not in AGENT_RESEARCHER_PROFILE.tool_names


def test_list_agents_not_in_agent_coder_profile():
    """E4. Dynamic agent (coder profile) must NOT have list_agents."""
    from src.core.runtime_profiles import AGENT_CODER_PROFILE
    assert "list_agents" not in AGENT_CODER_PROFILE.tool_names


def test_list_agents_in_dynamic_denylist():
    """E5. list_agents is in DYNAMIC_AGENT_DENYLIST (policy gate)."""
    from src.agents.spec import DYNAMIC_AGENT_DENYLIST
    assert "list_agents" in DYNAMIC_AGENT_DENYLIST


# ── Regression: delegate_to_agent still works ───────────────────────────────


@pytest.mark.asyncio
async def test_delegate_to_agent_unaffected_by_list_agents():
    """F. delegate_to_agent shim path is unaffected by list_agents registration."""
    ctx = _make_ctx(agent_name="researcher")
    fake_agent = ctx.services["agent_registry"].get_or_create_instance.return_value

    result = await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent_name": "researcher", "task": "explain RAG"},
        ),
        ctx,
    )
    assert result.success
    msg = fake_agent.execute.call_args.args[0]
    assert isinstance(msg, AgentMessage)
    assert msg.content == "explain RAG"


# ── list_agents ToolSpec registration ───────────────────────────────────────


def test_list_agents_tool_spec_registered():
    """list_agents is registered with correct schema and executor."""
    from src.tools.registry import ToolRegistry
    from src.tools.agent_tools import register_agent_tools
    registry = ToolRegistry()
    register_agent_tools(registry)
    spec = registry.get("list_agents")
    assert spec is not None
    assert spec.executor is list_agents_executor
    schema = spec.json_schema
    assert "include_inactive" in schema["properties"]
    assert "full" in schema["properties"]
    assert schema["properties"]["include_inactive"]["type"] == "boolean"
    assert schema["properties"]["full"]["type"] == "boolean"
    assert schema["required"] == []
