import pytest
pytestmark = pytest.mark.e2e
"""T-02: legacy delegate_to_researcher / delegate_to_coder shims must produce
the same ToolExecutionResult shape as delegate_to_agent_executor.

Blueprint §7.4: shims forward to delegate_to_agent_executor and don't
duplicate the logic. The persisted-plan / older-client compatibility is
preserved.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.types import AgentResult
from src.tools.agent_tools import (
    delegate_to_agent_executor,
    delegate_to_researcher_executor,
    delegate_to_coder_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(*, agent_name: str):
    """Build a context with a fake registry that returns a successful agent."""
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
    services = {
        "agent_registry": registry,
        "dispatcher": MagicMock(),
        "tool_dispatcher": MagicMock(),
        "tool_registry": MagicMock(),
        "llm_router": MagicMock(),
        "research_engine": MagicMock(),
        "ambient_store": MagicMock(),
    }
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="test",
        user_id="test",
        auth_level=10,
        chat_id="c1",
        services=services,
    ), fake_agent


@pytest.mark.asyncio
async def test_t02_researcher_shim_matches_new_path():
    """Same task via shim and new path → equivalent result shapes."""
    new_ctx, _ = _make_ctx(agent_name="researcher")
    new_res = await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent_name": "researcher", "task": "find foo"},
        ),
        new_ctx,
    )

    shim_ctx, _ = _make_ctx(agent_name="researcher")
    shim_res = await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "find foo"},
        ),
        shim_ctx,
    )

    assert new_res.success == shim_res.success is True
    # Both payloads must have the same top-level keys for the LLM/UI consumer
    assert set(new_res.payload.keys()) == set(shim_res.payload.keys())
    # Both carry the agent's result text
    assert new_res.payload["result"] == shim_res.payload["result"] == "ok"


@pytest.mark.asyncio
async def test_t02_coder_shim_matches_new_path():
    new_ctx, _ = _make_ctx(agent_name="coder")
    new_res = await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent_name": "coder", "task": "write hello.py"},
        ),
        new_ctx,
    )

    shim_ctx, _ = _make_ctx(agent_name="coder")
    shim_res = await delegate_to_coder_executor(
        ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"request": "write hello.py"},
        ),
        shim_ctx,
    )

    assert new_res.success == shim_res.success is True
    assert set(new_res.payload.keys()) == set(shim_res.payload.keys())


@pytest.mark.asyncio
async def test_shim_preserves_context_digest_field():
    """Old `context_digest` arg is forwarded to the new `context` field."""
    ctx, fake_agent = _make_ctx(agent_name="researcher")
    await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={
                "request": "find foo",
                "context_digest": "user wants Y",
            },
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.context_digest == "user wants Y"


# ── New-style schema (task / freshness_hint) ──────────────────────────


@pytest.mark.asyncio
async def test_researcher_accepts_task_param():
    """Primary parameter is now ``task`` (matches the new schema)."""
    ctx, fake_agent = _make_ctx(agent_name="researcher")
    await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"task": "今天 LA 天气"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.content == "今天 LA 天气"


@pytest.mark.asyncio
async def test_researcher_propagates_freshness_hint():
    ctx, fake_agent = _make_ctx(agent_name="researcher")
    await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"task": "现在比分", "freshness_hint": "realtime"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.freshness_hint == "realtime"


@pytest.mark.asyncio
async def test_researcher_freshness_hint_omitted_means_none():
    ctx, fake_agent = _make_ctx(agent_name="researcher")
    await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"task": "解释 RAG"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.freshness_hint is None


@pytest.mark.asyncio
async def test_coder_accepts_task_param():
    ctx, fake_agent = _make_ctx(agent_name="coder")
    await delegate_to_coder_executor(
        ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"task": "写个 hello.py"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.content == "写个 hello.py"


@pytest.mark.asyncio
async def test_researcher_rejects_empty_task():
    ctx, _ = _make_ctx(agent_name="researcher")
    res = await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={},
        ),
        ctx,
    )
    assert res.success is False
    assert "task" in (res.reason or "")


@pytest.mark.asyncio
async def test_researcher_task_param_takes_precedence_over_request():
    """When both legacy and new args appear, ``task`` wins."""
    ctx, fake_agent = _make_ctx(agent_name="researcher")
    await delegate_to_researcher_executor(
        ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"task": "new", "request": "old"},
        ),
        ctx,
    )
    msg = fake_agent.execute.call_args.args[0]
    assert msg.content == "new"


def test_researcher_schema_advertises_new_shape():
    """Schema must require ``task`` (not ``request``) and offer the
    freshness_hint enum, so the LLM sees the new contract.
    """
    from src.tools.registry import ToolRegistry
    from src.tools.agent_tools import register_agent_tools
    registry = ToolRegistry()
    register_agent_tools(registry)
    spec = registry.get("delegate_to_researcher")
    assert spec is not None
    schema = spec.json_schema
    assert schema["required"] == ["task"]
    assert "task" in schema["properties"]
    assert "freshness_hint" in schema["properties"]
    assert schema["properties"]["freshness_hint"]["enum"] == [
        "realtime", "recent", "anytime",
    ]


def test_coder_schema_advertises_task_param():
    from src.tools.registry import ToolRegistry
    from src.tools.agent_tools import register_agent_tools
    registry = ToolRegistry()
    register_agent_tools(registry)
    spec = registry.get("delegate_to_coder")
    assert spec is not None
    assert spec.json_schema["required"] == ["task"]
