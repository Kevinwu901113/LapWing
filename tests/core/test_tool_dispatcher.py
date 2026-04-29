import pytest
from unittest.mock import AsyncMock, MagicMock
from src.core.tool_dispatcher import ToolDispatcher, ServiceContextView
from src.core.task_runtime import TaskRuntime
from src.tools.types import ToolExecutionRequest, ToolExecutionResult
from src.core.authority_gate import AuthLevel
from src.logging.state_mutation_log import MutationType

@pytest.fixture
def mock_runtime():
    runtime = MagicMock(spec=TaskRuntime)
    runtime._resolve_profile.return_value = MagicMock(
        name="test_profile",
        include_internal=False,
        shell_policy_enabled=False
    )
    runtime._tool_registry = MagicMock()
    runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    return runtime

@pytest.fixture
def dispatcher(mock_runtime):
    return ToolDispatcher(mock_runtime)

@pytest.mark.asyncio
async def test_tool_dispatcher_unknown_tool(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = None
    req = ToolExecutionRequest(name="unknown_tool", arguments={})
    
    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
    )
    
    assert not result.success
    assert result.reason == "未知工具：unknown_tool"
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "unknown_tool"

@pytest.mark.asyncio
async def test_tool_dispatcher_profile_not_allowed(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    req = ToolExecutionRequest(name="disallowed_tool", arguments={})
    
    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
    )
    
    assert not result.success
    assert "不允许工具" in result.reason
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "profile_not_allowed"

@pytest.mark.asyncio
async def test_tool_dispatcher_authority_gate(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"execute_shell"}
    req = ToolExecutionRequest(name="execute_shell", arguments={})
    
    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        adapter="agent", # adapter triggers auth check
        user_id="untrusted_user",
    )
    
    assert not result.success
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "authority_gate"

@pytest.mark.asyncio
async def test_tool_dispatcher_agent_policy_missing_fail_closed(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})
    
    agent_spec = MagicMock(kind="dynamic", name="dyn_agent")
    
    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=agent_spec,
    )
    
    assert not result.success
    assert "missing AgentPolicy in services (fail-closed)" in result.reason
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "agent_policy"

@pytest.mark.asyncio
async def test_tool_dispatcher_agent_policy_denied(dispatcher, mock_runtime):
    mock_runtime._tool_registry.get.return_value = MagicMock()
    mock_runtime._tool_names_for_profile.return_value = {"allowed_tool"}
    req = ToolExecutionRequest(name="allowed_tool", arguments={})
    
    agent_spec = MagicMock(kind="dynamic", name="dyn_agent")
    mock_policy = MagicMock()
    mock_policy.validate_tool_access.return_value = False
    
    services = {
        "mutation_log": AsyncMock(),
        "agent_policy": mock_policy
    }
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
        agent_spec=agent_spec,
    )
    
    assert not result.success
    assert "blocked by AgentPolicy" in result.reason
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "agent_policy"

@pytest.mark.asyncio
async def test_tool_dispatcher_browser_guard_missing(dispatcher, mock_runtime):
    tool_mock = MagicMock(capability="browser")
    mock_runtime._tool_registry.get.return_value = tool_mock
    mock_runtime._tool_names_for_profile.return_value = {"browser_open"}
    req = ToolExecutionRequest(name="browser_open", arguments={})
    
    mock_runtime._browser_guard = None
    
    services = {"mutation_log": AsyncMock()}
    result = await dispatcher.dispatch(
        request=req,
        profile="test_profile",
        services=services,
    )
    
    assert not result.success
    assert "未挂载" in result.reason
    services["mutation_log"].record.assert_called_once()
    args, kwargs = services["mutation_log"].record.call_args
    assert args[0] == MutationType.TOOL_DENIED
    assert args[1]["guard"] == "browser_guard_missing"
