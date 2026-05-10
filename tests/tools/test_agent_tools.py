"""delegate_to_researcher / delegate_to_coder 工具测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.types import AgentResult, ResearchResult, SourceRef
from src.tools.agent_tools import (
    delegate_to_researcher_executor,
    delegate_to_coder_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(**services_override):
    services = {
        "agent_registry": MagicMock(),
        "dispatcher": MagicMock(),
        "tool_dispatcher": MagicMock(),
        "tool_registry": MagicMock(),
        "llm_router": MagicMock(),
        "research_engine": MagicMock(),
        "ambient_store": MagicMock(),
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

    async def test_child_tool_hard_error_fails_delegation(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1",
            status="done",
            result="我查不到。",
            execution_trace=["started: researcher", "tool: research", "completed"],
            tool_errors=[{
                "tool": "research",
                "reason": "research_engine 未注入",
                "payload": {"error": "research_engine 未注入"},
            }],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "查斯诺克比分"},
            ),
            _make_ctx(agent_registry=registry),
        )

        assert not result.success
        assert "research_engine 未注入" in result.reason
        assert result.payload["agent_output"] == "我查不到。"

    async def test_soft_no_result_does_not_fail_delegation(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1",
            status="done",
            result="没有找到相关信息。",
            execution_trace=["started: researcher", "tool: research", "completed"],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "查一个冷门信息"},
            ),
            _make_ctx(agent_registry=registry),
        )

        assert result.success
        assert "没有找到相关信息" in result.payload["result"]

    async def test_hard_error_wins_over_soft_tool_error(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1",
            status="done",
            result="部分工具失败。",
            execution_trace=["started: researcher", "tool: research", "tool: browse"],
            tool_errors=[
                {"tool": "research", "reason": "没有找到相关信息"},
                {"tool": "browse", "reason": "browser_engine_unavailable"},
            ],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "查斯诺克比分"},
            ),
            _make_ctx(agent_registry=registry),
        )

        assert not result.success
        assert result.reason == "browser_engine_unavailable"


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


# ── P0 Contract Tests: delegation return type hardening ───────────────────


class TestDelegationContract:
    """Blueprint test matrix for tuple indexing contract hardening."""

    async def test_bare_tuple_result_does_not_crash(self):
        """Repro: agent.execute returns bare tuple → should not raise
        'tuple indices must be integers or slices, not str'."""
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=(True, {"summary": "ok"}, None))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "test tuple violation"},
            ),
            _make_ctx(agent_registry=registry),
        )
        # Should not crash — contract validation catches it.
        assert not result.success
        assert "bare tuple" in result.reason.lower()
        assert result.payload.get("contract_violation")

    async def test_research_result_dataclass_normal_path(self):
        """Researcher returns ResearchResult dataclass → structured_result
        should surface summary and sources."""
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1",
            status="done",
            result='{"summary": "天气晴朗", "sources": [{"ref": "weather.com"}]}',
            structured_result={"summary": "天气晴朗", "sources": [{"ref": "weather.com"}]},
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "查天气"},
            ),
            _make_ctx(agent_registry=registry),
        )
        assert result.success
        assert result.payload.get("summary") == "天气晴朗"
        assert result.payload.get("sources") == [{"ref": "weather.com"}]

    async def test_dict_result_normal_path(self):
        """Agent returns dict structured_result → keys surface on payload."""
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1",
            status="done",
            result="done",
            structured_result={"summary": "A", "sources": [{"ref": "a.com"}]},
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "test dict result"},
            ),
            _make_ctx(agent_registry=registry),
        )
        assert result.success
        assert result.payload["summary"] == "A"

    async def test_hard_error_passthrough(self):
        """Agent delegation fails → structured error, no tuple crash."""
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1",
            status="failed",
            result="",
            reason="research_engine 未注入",
            error_detail="missing service",
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "test hard error"},
            ),
            _make_ctx(agent_registry=registry),
        )
        assert not result.success
        assert "research_engine" in result.reason

    async def test_missing_service_returns_typed_error(self):
        """Missing agent_registry → typed ToolExecutionResult, no crash."""
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={},
        )
        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "test missing service"},
            ),
            ctx,
        )
        assert not result.success
        assert "未就绪" in result.reason

    async def test_structured_result_non_dict_does_not_crash(self):
        """structured_result is a non-dict (e.g. list) → normalize, no crash."""
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1",
            status="done",
            result="done",
            structured_result=["unexpected", "list"],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        result = await delegate_to_researcher_executor(
            ToolExecutionRequest(
                name="delegate_to_researcher",
                arguments={"task": "test non-dict structured"},
            ),
            _make_ctx(agent_registry=registry),
        )
        # Should not crash — type guard handles non-dict structured_result.
        assert result.success

    async def test_consecutive_success_no_burst_guard(self):
        """Consecutive successful delegations → no burst guard trigger."""
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="done", result="ok",
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        for i in range(5):
            result = await delegate_to_researcher_executor(
                ToolExecutionRequest(
                    name="delegate_to_researcher",
                    arguments={"task": f"test {i}"},
                ),
                ctx,
            )
            assert result.success
