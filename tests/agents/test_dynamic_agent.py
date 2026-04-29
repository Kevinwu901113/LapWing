"""DynamicAgent runtime denylist enforcement (Blueprint §3, §11.2; T-05, T-06, T-14)."""

import json
import pytest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.agents.dynamic import DynamicAgent
from src.agents.spec import AgentSpec, DYNAMIC_AGENT_DENYLIST
from src.agents.types import AgentMessage
from src.logging.state_mutation_log import MutationType


# ── Fakes ──

@dataclass
class _FakeMutationEvent:
    event_type: MutationType
    payload: dict


class _FakeMutationLog:
    def __init__(self):
        self.events: list[_FakeMutationEvent] = []

    async def record(self, event_type, payload, **kwargs):
        self.events.append(_FakeMutationEvent(event_type=event_type, payload=payload))


@dataclass
class _FakeToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    id: str = "call_1"


@dataclass
class _FakeLLMResponse:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    continuation_message: dict | None = None


def _make_llm_router_returning_tool_call(tool_name: str, then_done_text: str = "ok"):
    """LLM returns a tool_call on first turn, then plain text on second."""
    router = MagicMock()
    call_count = {"n": 0}

    async def complete_with_tools(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeLLMResponse(
                tool_calls=[_FakeToolCall(name=tool_name)],
                continuation_message={"role": "assistant", "content": ""},
            )
        return _FakeLLMResponse(text=then_done_text)

    router.complete_with_tools = AsyncMock(side_effect=complete_with_tools)
    router.build_tool_result_message = MagicMock(
        return_value={"role": "user", "content": "tool result"}
    )
    return router


class _RecordingToolRegistry:
    """A tool registry that records all execute() calls but never actually executes."""

    def __init__(self):
        self.executed: list[str] = []

    def function_tools(self, *, capabilities=None, tool_names=None, include_internal=False):
        # BaseAgent calls this once during _get_tools(); return empty list — the
        # fake LLM router doesn't actually consult the tool list, it just emits
        # the tool_call we configured.
        return []

    async def execute(self, req, context):
        self.executed.append(req.name)
        from src.tools.types import ToolExecutionResult
        return ToolExecutionResult(success=True, payload={"ok": True})


def _build_dynamic_agent(tool_name_to_attempt: str, *, spec_tool_denylist=None):
    """Construct a DynamicAgent that, when run, will see exactly one LLM call
    proposing tool_name_to_attempt."""
    spec = AgentSpec(
        name="probe",
        kind="dynamic",
        runtime_profile="agent_researcher",
        model_slot="agent_researcher",
        system_prompt="probe prompt",
        tool_denylist=spec_tool_denylist or [],
    )
    profile = MagicMock()
    profile.tool_names = frozenset()  # unused in this test
    profile.capabilities = frozenset()
    profile.include_internal = False
    router = _make_llm_router_returning_tool_call(tool_name_to_attempt)
    registry = _RecordingToolRegistry()
    log = _FakeMutationLog()
    
    mock_policy = MagicMock()
    mock_policy.validate_tool_access.return_value = True
    
    agent = DynamicAgent(
        spec=spec, profile=profile,
        llm_router=router, tool_registry=registry,
        mutation_log=log,
        services={
            "shell_default_cwd": "/tmp/lapwing/agents/probe",
            "agent_policy": mock_policy
        },
    )
    return agent, registry, log

# ── New Test: Agent runtime 二次 policy 校验 ──

@pytest.mark.asyncio
async def test_dynamic_agent_tool_dispatch_denies_policy_rejected_tool():
    """
    Test that if policy.validate_tool_access returns False, the tool is denied.
    This ensures that the runtime dispatch actually uses the policy as a hard gate.
    """
    agent, registry, log = _build_dynamic_agent("some_random_tool")
    
    # Mock AgentPolicy to always return False
    mock_policy = MagicMock()
    mock_policy.validate_tool_access.return_value = False
    
    # Inject policy into agent services
    agent._services = agent._services or {}
    agent._services["agent_policy"] = mock_policy
    
    msg = AgentMessage(from_agent="lapwing", to_agent="probe",
                       task_id="t1", content="do x", message_type="request")
    await agent.execute(msg)
    
    # Assert policy was called
    mock_policy.validate_tool_access.assert_called_once_with(agent.dynamic_spec, "some_random_tool")
    
    # Assert tool executor was not called
    assert "some_random_tool" not in registry.executed
    
    # Assert TOOL_DENIED event was recorded
    denied = [e for e in log.events
              if e.event_type == MutationType.TOOL_DENIED
              and e.payload.get("tool") == "some_random_tool"]
    assert denied, "expected TOOL_DENIED event for policy rejected tool"
    assert denied[0].payload.get("reason") == "blocked by AgentPolicy"


# ── T-05: dynamic agent cannot call DYNAMIC_AGENT_DENYLIST tools ──

@pytest.mark.asyncio
async def test_t05_send_message_blocked_at_runtime():
    agent, registry, log = _build_dynamic_agent("send_message")
    msg = AgentMessage(from_agent="lapwing", to_agent="probe",
                       task_id="t1", content="do x", message_type="request")
    await agent.execute(msg)
    # send_message must NEVER reach the registry
    assert "send_message" not in registry.executed
    # TOOL_DENIED with guard=dynamic_agent_denylist must be emitted
    denied = [e for e in log.events
              if e.event_type == MutationType.TOOL_DENIED
              and e.payload.get("tool") == "send_message"]
    assert denied, "expected TOOL_DENIED event for send_message"
    assert denied[0].payload.get("guard") == "dynamic_agent_denylist"
    assert denied[0].payload.get("agent_name") == "probe"


# ── T-06: blueprint denylist enforced even when spec.tool_denylist is empty ──

@pytest.mark.asyncio
async def test_t06_runtime_denylist_with_empty_spec_tool_denylist():
    agent, registry, log = _build_dynamic_agent("create_agent",
                                                  spec_tool_denylist=[])
    msg = AgentMessage(from_agent="lapwing", to_agent="probe",
                       task_id="t", content="x", message_type="request")
    await agent.execute(msg)
    assert "create_agent" not in registry.executed
    denied = [e for e in log.events if e.event_type == MutationType.TOOL_DENIED]
    assert any(e.payload.get("tool") == "create_agent"
               and e.payload.get("guard") == "dynamic_agent_denylist"
               for e in denied)


# ── T-14: every member of DYNAMIC_AGENT_DENYLIST blocked at runtime ──

@pytest.mark.parametrize("tool_name", sorted(DYNAMIC_AGENT_DENYLIST))
@pytest.mark.asyncio
async def test_t14_every_denylist_tool_blocked_at_runtime(tool_name):
    agent, registry, log = _build_dynamic_agent(tool_name)
    msg = AgentMessage(from_agent="lapwing", to_agent="probe",
                       task_id="t", content="x", message_type="request")
    await agent.execute(msg)
    assert tool_name not in registry.executed, f"{tool_name} reached registry"
    denied = [e for e in log.events
              if e.event_type == MutationType.TOOL_DENIED
              and e.payload.get("tool") == tool_name]
    assert denied, f"expected TOOL_DENIED for {tool_name}"
    assert denied[0].payload.get("guard") == "dynamic_agent_denylist"


# ── spec.tool_denylist also enforced (orthogonal to blueprint denylist) ──

@pytest.mark.asyncio
async def test_spec_tool_denylist_blocks_at_runtime():
    """If a name is in spec.tool_denylist (but NOT in DYNAMIC_AGENT_DENYLIST),
    it's still blocked. Test with 'research' which is NOT in the blueprint
    denylist but should be blockable per spec."""
    agent, registry, log = _build_dynamic_agent(
        "research", spec_tool_denylist=["research"]
    )
    msg = AgentMessage(from_agent="lapwing", to_agent="probe",
                       task_id="t", content="x", message_type="request")
    await agent.execute(msg)
    assert "research" not in registry.executed
    denied = [e for e in log.events if e.event_type == MutationType.TOOL_DENIED]
    assert any(e.payload.get("tool") == "research" for e in denied)


# ── allowed tools pass through ──

@pytest.mark.asyncio
async def test_allowed_tool_passes_through():
    """A tool not in any denylist should reach the registry."""
    agent, registry, log = _build_dynamic_agent("research")  # not in any denylist
    msg = AgentMessage(from_agent="lapwing", to_agent="probe",
                       task_id="t", content="x", message_type="request")
    await agent.execute(msg)
    assert "research" in registry.executed
    # No TOOL_DENIED for research
    denied_tools = [e.payload.get("tool") for e in log.events
                    if e.event_type == MutationType.TOOL_DENIED]
    assert "research" not in denied_tools
