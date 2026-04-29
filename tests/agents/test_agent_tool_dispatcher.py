import pytest
from unittest.mock import AsyncMock, MagicMock
import json
from src.agents.base import BaseAgent
from src.agents.dynamic import DynamicAgent
from src.tools.types import ToolExecutionRequest, ToolExecutionResult
from src.core.authority_gate import AuthLevel
from src.logging.state_mutation_log import MutationType

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
    assert kwargs["agent_spec"] == agent.spec

