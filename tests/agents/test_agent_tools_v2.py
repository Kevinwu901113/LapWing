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
        services={"agent_registry": registry},
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
