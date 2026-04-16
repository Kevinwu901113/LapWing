"""delegate / delegate_to_agent 工具测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.types import AgentResult
from src.tools.agent_tools import delegate_executor, delegate_to_agent_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(**services_override):
    services = {
        "agent_registry": MagicMock(),
        "dispatcher": AsyncMock(),
    }
    services.update(services_override)
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        services=services,
    )


class TestDelegateExecutor:
    async def test_empty_request_fails(self):
        req = ToolExecutionRequest(name="delegate", arguments={"request": ""})
        result = await delegate_executor(req, _make_ctx())
        assert not result.success

    async def test_no_registry_fails(self):
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(), shell_default_cwd=".",
            services={},
        )
        req = ToolExecutionRequest(name="delegate", arguments={"request": "test"})
        result = await delegate_executor(req, ctx)
        assert not result.success

    async def test_no_team_lead_fails(self):
        registry = MagicMock()
        registry.get.return_value = None
        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(name="delegate", arguments={"request": "test"})
        result = await delegate_executor(req, ctx)
        assert not result.success

    async def test_success_flow(self):
        team_lead = MagicMock()
        team_lead.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="done", result="Here are the results.",
        ))
        registry = MagicMock()
        registry.get.return_value = team_lead

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate",
            arguments={"request": "查一下天气"},
        )
        result = await delegate_executor(req, ctx)
        assert result.success
        assert result.payload.get("result") == "Here are the results."

    async def test_failed_delegation(self):
        team_lead = MagicMock()
        team_lead.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="failed", result="", reason="timeout",
        ))
        registry = MagicMock()
        registry.get.return_value = team_lead

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(name="delegate", arguments={"request": "do stuff"})
        result = await delegate_executor(req, ctx)
        assert not result.success


class TestDelegateToAgentExecutor:
    async def test_missing_params_fails(self):
        req = ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent": "", "instruction": ""},
        )
        result = await delegate_to_agent_executor(req, _make_ctx())
        assert not result.success

    async def test_unknown_agent_fails(self):
        registry = MagicMock()
        registry.get.return_value = None
        registry.list_names.return_value = ["researcher", "coder"]
        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent": "nonexistent", "instruction": "do stuff"},
        )
        result = await delegate_to_agent_executor(req, ctx)
        assert not result.success
        assert "researcher" in result.reason

    async def test_success_flow(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="sub1", status="done", result="Found info.",
            evidence=[{"type": "url", "value": "https://example.com"}],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent": "researcher", "instruction": "search for X"},
        )
        result = await delegate_to_agent_executor(req, ctx)
        assert result.success
        assert "Found info." in result.payload.get("result", "")
