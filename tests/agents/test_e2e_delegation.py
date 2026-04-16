"""端到端 delegation 测试：Lapwing → Team Lead → Agent → 结果。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.coder import Coder
from src.agents.registry import AgentRegistry
from src.agents.researcher import Researcher
from src.agents.team_lead import TeamLead
from src.agents.types import AgentResult
from src.core.llm_types import ToolCallRequest, ToolTurnResult
from src.tools.agent_tools import delegate_executor, register_agent_tools
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)


def _make_dispatcher():
    d = AsyncMock()
    d.submit = AsyncMock(return_value="evt_001")
    return d


def _make_registry_with_agents(router, tool_registry, dispatcher, services=None):
    """Build a full agent registry with all three agents."""
    reg = AgentRegistry()
    reg.register("team_lead", TeamLead.create(router, tool_registry, dispatcher, services=services))
    reg.register("researcher", Researcher.create(router, tool_registry, dispatcher, services=services))
    reg.register("coder", Coder.create(router, tool_registry, dispatcher, services=services))
    return reg


class TestE2EDelegateToResearcher:
    """Lapwing delegates → Team Lead → Researcher → result."""

    async def test_full_chain(self):
        dispatcher = _make_dispatcher()

        # Researcher LLM responses
        researcher_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="r1", name="web_search", arguments={"query": "X"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        researcher_round2 = ToolTurnResult(
            text="Found: X is interesting. [来源: https://example.com]",
            tool_calls=[],
            continuation_message=None,
        )

        # Team Lead LLM responses
        tl_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(
                id="tl1", name="delegate_to_agent",
                arguments={"agent": "researcher", "instruction": "search for X"},
            )],
            continuation_message={"role": "assistant", "content": ""},
        )
        tl_round2 = ToolTurnResult(
            text="调研结果：X is interesting。",
            tool_calls=[],
            continuation_message=None,
        )

        # Router returns different responses per call.
        # Order: TL round1, Researcher round1, Researcher round2, TL round2
        router = MagicMock()
        router.complete_with_tools = AsyncMock(
            side_effect=[tl_round1, researcher_round1, researcher_round2, tl_round2],
        )
        router.build_tool_result_message = MagicMock(
            return_value={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        )

        # Tool registry: needs delegate_to_agent and web_search specs
        tool_registry = MagicMock()

        def _get_tool(name):
            spec = MagicMock()
            spec.name = name
            spec.description = f"{name} tool"
            spec.json_schema = {"type": "object", "properties": {}}
            return spec

        tool_registry.get = MagicMock(side_effect=_get_tool)
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(
            success=True, payload={"results": ["X info"]},
        ))

        # Build registry
        agent_registry = _make_registry_with_agents(router, tool_registry, dispatcher)

        # Execute delegate
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={
                "agent_registry": agent_registry,
                "dispatcher": dispatcher,
            },
        )
        req = ToolExecutionRequest(
            name="delegate",
            arguments={"request": "帮我查一下 X 是什么"},
        )

        result = await delegate_executor(req, ctx)
        assert result.success
        assert "X" in result.payload.get("result", "")


class TestE2ERealDelegation:
    """真实端到端测试：tool_registry.execute 不 mock，实际调用 delegate_to_agent_executor。

    验证 services 传递正确：TeamLead → _execute_tool → ToolExecutionContext(services=...)
    → delegate_to_agent_executor 能拿到 agent_registry → 找到 Researcher → Researcher 执行。
    """

    async def test_real_chain_lapwing_to_researcher(self):
        """delegate → TeamLead → delegate_to_agent → Researcher → web_search → 结果。"""
        from src.tools.agent_tools import delegate_to_agent_executor
        from src.tools.registry import ToolRegistry

        dispatcher = _make_dispatcher()

        # ── 构建真实 ToolRegistry，注册 delegate_to_agent 和 web_search ──
        real_registry = ToolRegistry()

        # delegate_to_agent 工具（Team Lead 会调用这个）
        real_registry.register(ToolSpec(
            name="delegate_to_agent",
            description="把子任务派给一个具体的 Agent。",
            json_schema={
                "type": "object",
                "properties": {
                    "agent": {"type": "string"},
                    "instruction": {"type": "string"},
                },
                "required": ["agent", "instruction"],
            },
            executor=delegate_to_agent_executor,
            capability="agent",
            risk_level="low",
        ))

        # web_search 工具（Researcher 会调用这个）—— executor 返回假搜索结果
        async def fake_web_search(req, ctx):
            return ToolExecutionResult(
                success=True,
                payload={
                    "results": [
                        {"title": "RAG Paper 2025", "url": "https://arxiv.org/abs/2025.12345",
                         "snippet": "RAG is a retrieval augmented generation technique."},
                    ],
                },
            )

        real_registry.register(ToolSpec(
            name="web_search",
            description="搜索网页",
            json_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            executor=fake_web_search,
            capability="web",
            risk_level="low",
        ))

        # ── 构建 agent_registry，传入 services ──
        agent_registry = AgentRegistry()
        agent_services = {
            "agent_registry": agent_registry,
            "dispatcher": dispatcher,
        }

        # ── Mock LLM router ──
        # 调用顺序：
        #   1. Team Lead round 1 → 调用 delegate_to_agent(researcher, "...")
        #   2. Researcher round 1 → 调用 web_search(query="RAG")
        #   3. Researcher round 2 → 返回最终文本（无 tool_calls）
        #   4. Team Lead round 2 → 返回汇总文本（无 tool_calls）
        router = MagicMock()

        tl_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(
                id="tl1", name="delegate_to_agent",
                arguments={"agent": "researcher", "instruction": "搜索 RAG 最新论文"},
            )],
            continuation_message={"role": "assistant", "content": ""},
        )

        researcher_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(
                id="r1", name="web_search",
                arguments={"query": "RAG 最新论文"},
            )],
            continuation_message={"role": "assistant", "content": ""},
        )

        researcher_round2 = ToolTurnResult(
            text="RAG 是检索增强生成技术。[来源: https://arxiv.org/abs/2025.12345]",
            tool_calls=[],
            continuation_message=None,
        )

        tl_round2 = ToolTurnResult(
            text="调研结果：RAG 是检索增强生成技术，最新论文见 arxiv。",
            tool_calls=[],
            continuation_message=None,
        )

        router.complete_with_tools = AsyncMock(
            side_effect=[tl_round1, researcher_round1, researcher_round2, tl_round2],
        )
        # build_tool_result_message 返回 OpenAI 格式的 tool result
        router.build_tool_result_message = MagicMock(
            return_value={"role": "tool", "tool_call_id": "x", "name": "x", "content": "ok"},
        )

        # ── 注册 Agent（使用真实 registry + services） ──
        agent_registry.register(
            "team_lead",
            TeamLead.create(router, real_registry, dispatcher, services=agent_services),
        )
        agent_registry.register(
            "researcher",
            Researcher.create(router, real_registry, dispatcher, services=agent_services),
        )
        agent_registry.register(
            "coder",
            Coder.create(router, real_registry, dispatcher, services=agent_services),
        )

        # ── 执行 delegate ──
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={
                "agent_registry": agent_registry,
                "dispatcher": dispatcher,
            },
        )
        req = ToolExecutionRequest(
            name="delegate",
            arguments={"request": "帮我查一下最新的 RAG 论文"},
        )

        result = await delegate_executor(req, ctx)

        # ── 验证 ──
        assert result.success, f"delegate 失败: {result.reason}"
        assert "RAG" in result.payload["result"]

        # 验证 LLM 被调用了 4 次（TL×2 + Researcher×2）
        assert router.complete_with_tools.await_count == 4

        # 验证 dispatcher 收到了各层事件
        event_types = [c.kwargs["event_type"] for c in dispatcher.submit.call_args_list]
        assert "agent.task_started" in event_types
        assert "agent.tool_called" in event_types
        assert "agent.task_done" in event_types
