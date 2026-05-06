import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.factory import AgentFactory
from src.agents.registry import AgentRegistry
from src.agents.spec import AgentSpec
from src.agents.base import BaseAgent
from src.agents.dynamic import DynamicAgent
from src.tools.agent_tools import _resolve_agent, delegate_to_coder_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

@pytest.fixture
def mock_dispatcher():
    dispatcher = AsyncMock()
    dispatcher.dispatch.return_value = ToolExecutionResult(
        success=True,
        payload={"stdout": "success", "return_code": 0},
        reason=""
    )
    return dispatcher

@pytest.fixture
def mock_services(mock_dispatcher):
    return {
        "dispatcher": mock_dispatcher,
        "mutation_log": AsyncMock()
    }

@pytest.mark.asyncio
async def test_base_agent_routes_through_dispatcher(mock_services, mock_dispatcher):
    spec = MagicMock()
    spec.name = "test_agent"
    spec.runtime_profile = "test_profile"
    
    agent = BaseAgent(
        spec=spec,
        tool_registry=MagicMock(),
        llm_router=MagicMock(),
        mutation_log=MagicMock(),
        services=mock_services
    )
    
    tool_call = MagicMock()
    tool_call.name = "run_python_code"
    tool_call.arguments = {"code": "print(1)"}
    
    message = MagicMock()
    message.task_id = "task-123"
    
    await agent._execute_tool(tool_call, message)
    
    mock_dispatcher.dispatch.assert_called_once()
    kwargs = mock_dispatcher.dispatch.call_args.kwargs
    
    assert kwargs["request"].name == "run_python_code"
    assert kwargs["profile"] == "test_profile"
    assert kwargs["adapter"] == "agent"
    assert kwargs["user_id"] == "agent:test_agent"
    assert kwargs["agent_spec"] == agent.spec

@pytest.mark.asyncio
async def test_dynamic_agent_routes_through_dispatcher(mock_services, mock_dispatcher):
    spec = MagicMock()
    spec.name = "dynamic_test"
    spec.kind = "dynamic"
    spec.runtime_profile = "test_profile"
    
    agent = DynamicAgent(
        spec=spec,
        profile=MagicMock(),
        tool_registry=MagicMock(),
        llm_router=MagicMock(),
        mutation_log=MagicMock(),
        services=mock_services
    )
    
    tool_call = MagicMock()
    tool_call.name = "execute_shell"
    tool_call.arguments = {"command": "ls"}
    
    message = MagicMock()
    message.task_id = "task-123"
    
    await agent._execute_tool(tool_call, message)
    
    mock_dispatcher.dispatch.assert_called_once()
    kwargs = mock_dispatcher.dispatch.call_args.kwargs
    
    assert kwargs["request"].name == "execute_shell"
    assert kwargs["agent_spec"] == agent.dynamic_spec


@pytest.mark.asyncio
async def test_base_agent_missing_dispatcher_fails_closed():
    spec = MagicMock()
    spec.name = "test_agent"
    spec.runtime_profile = "test_profile"

    registry = MagicMock()
    registry.execute = AsyncMock()

    mutation_log = AsyncMock()
    agent = BaseAgent(
        spec=spec,
        tool_registry=registry,
        llm_router=MagicMock(),
        mutation_log=MagicMock(),
        services={"mutation_log": mutation_log},  # no dispatcher
    )

    tool_call = MagicMock()
    tool_call.name = "run_python_code"
    tool_call.arguments = {"code": "print(1)"}
    message = MagicMock()
    message.task_id = "task-123"

    output = await agent._execute_tool(tool_call, message)

    assert "missing_dispatcher" in output
    registry.execute.assert_not_called()
    mutation_log.record.assert_awaited_once()
    payload = mutation_log.record.await_args.args[1]
    assert payload["guard"] == "dispatcher_missing"


@pytest.mark.asyncio
async def test_resolve_agent_passes_turn_services_override():
    agent = MagicMock()
    registry = MagicMock()
    registry.get_or_create_instance = AsyncMock(return_value=agent)

    services = {"dispatcher": object(), "agent_policy": object()}
    resolved = await _resolve_agent(registry, "coder", services_override=services)

    assert resolved is agent
    registry.get_or_create_instance.assert_awaited_once_with(
        "coder",
        services_override=services,
    )


@pytest.mark.asyncio
async def test_agent_registry_factory_path_injects_services_override():
    spec = AgentSpec(name="coder", kind="builtin", runtime_profile="agent_coder")

    catalog = MagicMock()
    catalog.get_by_name = AsyncMock(return_value=spec)
    factory = MagicMock()
    factory.create = MagicMock(return_value=MagicMock())

    registry = AgentRegistry(catalog=catalog, factory=factory, policy=None)

    services = {"dispatcher": object(), "agent_policy": object(), "budget_ledger": object()}
    await registry.get_or_create_instance("coder", services_override=services)

    factory.create.assert_called_once()
    _, kwargs = factory.create.call_args
    assert kwargs["services_override"] is services


def test_agent_factory_injects_services_for_builtin():
    factory = AgentFactory(
        llm_router=MagicMock(),
        tool_registry=MagicMock(),
        mutation_log=MagicMock(),
    )
    spec = AgentSpec(name="coder", kind="builtin", runtime_profile="agent_coder")
    services = {"dispatcher": object(), "agent_policy": object()}

    agent = factory.create(spec, services_override=services)

    assert getattr(agent, "_services", {}).get("dispatcher") is services["dispatcher"]
    assert getattr(agent, "_services", {}).get("agent_policy") is services["agent_policy"]


@pytest.mark.asyncio
async def test_delegate_to_coder_passes_turn_services_into_registry():
    agent = MagicMock()
    agent.execute = AsyncMock(return_value=MagicMock(
        status="done",
        result="ok",
        artifacts=[],
        evidence=[],
        execution_trace=[],
        structured_result=None,
        reason="",
        error_detail="",
    ))
    registry = MagicMock()
    registry.get_or_create_instance = AsyncMock(return_value=agent)

    services = {
        "agent_registry": registry,
        "dispatcher": object(),
        "agent_policy": object(),
        "tool_registry": object(),
        "llm_router": object(),
        "budget_ledger": None,
    }
    ctx = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="desktop",
        user_id="owner",
        auth_level=3,
        chat_id="chat-1",
        services=services,
    )
    req = ToolExecutionRequest(name="delegate_to_coder", arguments={"task": "print hello"})

    await delegate_to_coder_executor(req, ctx)

    registry.get_or_create_instance.assert_awaited_once_with(
        "coder",
        services_override=services,
    )
