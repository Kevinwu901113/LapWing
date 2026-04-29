"""DynamicAgent dispatch contract tests (ToolDispatcher as primary gate)."""

import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.dynamic import DynamicAgent
from src.agents.spec import AgentSpec
from src.agents.types import AgentMessage
from src.core.tool_dispatcher import ToolDispatcher
from src.core.task_runtime import TaskRuntime
from src.tools.types import ToolExecutionResult


@dataclass
class _FakeToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    id: str = "call_1"


def _build_dynamic_spec() -> AgentSpec:
    return AgentSpec(
        name="probe",
        kind="dynamic",
        runtime_profile="agent_researcher",
        model_slot="agent_researcher",
        system_prompt="probe prompt",
        tool_denylist=[],
    )


def _build_dynamic_agent_with_dispatcher(dispatcher, *, policy=None):
    spec = _build_dynamic_spec()
    profile = MagicMock()
    profile.name = "agent_researcher"
    profile.tool_names = frozenset({"research"})
    profile.capabilities = frozenset()
    profile.include_internal = False
    profile.shell_policy_enabled = False

    services = {"dispatcher": dispatcher}
    if policy is not None:
        services["agent_policy"] = policy

    return DynamicAgent(
        spec=spec,
        profile=profile,
        llm_router=MagicMock(),
        tool_registry=MagicMock(),
        mutation_log=MagicMock(),
        services=services,
    )


@pytest.mark.asyncio
async def test_dynamic_agent_missing_dispatcher_fails_closed():
    spec = _build_dynamic_spec()
    profile = MagicMock()
    profile.name = "agent_researcher"
    profile.tool_names = frozenset({"research"})
    profile.capabilities = frozenset()
    profile.include_internal = False
    profile.shell_policy_enabled = False
    agent = DynamicAgent(
        spec=spec,
        profile=profile,
        llm_router=MagicMock(),
        tool_registry=MagicMock(),
        mutation_log=MagicMock(),
        services={},
    )

    output = await agent._execute_tool(_FakeToolCall(name="research"), AgentMessage(
        from_agent="lapwing",
        to_agent="probe",
        task_id="t1",
        content="do x",
        message_type="request",
    ))

    payload = json.loads(output)
    assert payload.get("reason") == "missing_dispatcher"


@pytest.mark.asyncio
async def test_dynamic_agent_dispatch_uses_dynamic_spec():
    dispatcher = AsyncMock()
    dispatcher.dispatch.return_value = ToolExecutionResult(
        success=True,
        payload={"ok": True},
        reason="",
    )
    agent = _build_dynamic_agent_with_dispatcher(dispatcher)

    await agent._execute_tool(_FakeToolCall(name="research"), AgentMessage(
        from_agent="lapwing",
        to_agent="probe",
        task_id="t1",
        content="do x",
        message_type="request",
    ))

    kwargs = dispatcher.dispatch.call_args.kwargs
    assert kwargs["agent_spec"] is agent.dynamic_spec


@pytest.mark.asyncio
async def test_dynamic_agent_policy_missing_fail_closed_via_dispatcher():
    runtime = MagicMock(spec=TaskRuntime)
    runtime._resolve_profile.return_value = MagicMock(
        name="agent_researcher",
        include_internal=False,
        shell_policy_enabled=False,
    )
    runtime._tool_registry = MagicMock()
    runtime._tool_registry.get.return_value = MagicMock(capability="general")
    runtime._tool_names_for_profile.return_value = {"research"}
    runtime._memory_index = None
    dispatcher = ToolDispatcher(runtime)

    agent = _build_dynamic_agent_with_dispatcher(dispatcher)
    output = await agent._execute_tool(_FakeToolCall(name="research"), AgentMessage(
        from_agent="lapwing",
        to_agent="probe",
        task_id="t1",
        content="do x",
        message_type="request",
    ))
    payload = json.loads(output)
    assert payload.get("reason") == "missing_agent_policy"


@pytest.mark.asyncio
async def test_dynamic_agent_policy_denied_never_executes_registry():
    runtime = MagicMock(spec=TaskRuntime)
    runtime._resolve_profile.return_value = MagicMock(
        name="agent_researcher",
        include_internal=False,
        shell_policy_enabled=False,
    )
    runtime._tool_registry = MagicMock()
    runtime._tool_registry.get.return_value = MagicMock(capability="general")
    runtime._tool_names_for_profile.return_value = {"research"}
    runtime._memory_index = None
    runtime._tool_registry.execute = AsyncMock()
    dispatcher = ToolDispatcher(runtime)

    policy = MagicMock()
    policy.validate_tool_access.return_value = False
    agent = _build_dynamic_agent_with_dispatcher(dispatcher, policy=policy)

    output = await agent._execute_tool(_FakeToolCall(name="research"), AgentMessage(
        from_agent="lapwing",
        to_agent="probe",
        task_id="t1",
        content="do x",
        message_type="request",
    ))
    payload = json.loads(output)
    assert payload.get("reason") == "policy_denied_tool"
    runtime._tool_registry.execute.assert_not_called()
