"""delegate_to_researcher / delegate_to_coder 工具测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.types import AgentResult
from src.tools.agent_tools import (
    delegate_to_researcher_executor,
    delegate_to_coder_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(**services_override):
    services = {
        "agent_registry": MagicMock(),
    }
    services.update(services_override)
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        services=services,
    )


class TestDelegateToResearcher:
    async def test_empty_request_fails(self):
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": ""},
        )
        result = await delegate_to_researcher_executor(req, _make_ctx())
        assert not result.success

    async def test_no_registry_fails(self):
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(), shell_default_cwd=".",
            services={},
        )
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "search X"},
        )
        result = await delegate_to_researcher_executor(req, ctx)
        assert not result.success
        assert "未就绪" in result.reason

    async def test_agent_not_found_fails(self):
        registry = MagicMock()
        registry.get.return_value = None
        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "search X"},
        )
        result = await delegate_to_researcher_executor(req, ctx)
        assert not result.success
        assert "不可用" in result.reason

    async def test_success_flow(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="done", result="Found info.",
            evidence=[{"type": "url", "value": "https://example.com"}],
            execution_trace=["started: researcher", "tool: research", "completed"],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "查一下天气"},
        )
        result = await delegate_to_researcher_executor(req, ctx)
        assert result.success
        assert "Found info." in result.payload["result"]
        assert result.payload.get("execution_trace")

    async def test_failed_returns_error_detail(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="failed", result="",
            reason="LLM 调用超时",
            error_detail="asyncio.TimeoutError during LLM call",
            execution_trace=["started: researcher"],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "do stuff"},
        )
        result = await delegate_to_researcher_executor(req, ctx)
        assert not result.success
        assert result.payload.get("error_detail")
        assert "超时" in result.reason


class TestDelegateToCoder:
    async def test_empty_request_fails(self):
        req = ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"request": ""},
        )
        result = await delegate_to_coder_executor(req, _make_ctx())
        assert not result.success

    async def test_success_flow(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="done",
            result="Script written to workspace/hello.py",
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"request": "写个 hello world 脚本"},
        )
        result = await delegate_to_coder_executor(req, ctx)
        assert result.success
        assert "hello.py" in result.payload["result"]

    async def test_context_digest_passed_through(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="done", result="ok",
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={
                "request": "写脚本",
                "context_digest": "Kevin 在整理日志文件",
            },
        )
        await delegate_to_coder_executor(req, ctx)

        call_args = agent.execute.call_args
        msg = call_args[0][0]
        assert msg.context_digest == "Kevin 在整理日志文件"

    async def test_agent_exception_returns_error(self):
        agent = MagicMock()
        agent.execute = AsyncMock(side_effect=RuntimeError("boom"))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_coder",
            arguments={"request": "do something"},
        )
        result = await delegate_to_coder_executor(req, ctx)
        assert not result.success
        assert "boom" in result.reason
        assert result.payload.get("error_detail")
