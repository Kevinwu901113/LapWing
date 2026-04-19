"""端到端 delegation 测试：Lapwing → Team Lead → Agent → 结果。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.coder import Coder
from src.agents.registry import AgentRegistry
from src.agents.researcher import Researcher
from src.agents.team_lead import TeamLead
from src.core.llm_types import ToolCallRequest, ToolTurnResult
from src.logging.state_mutation_log import MutationType
from src.tools.agent_tools import delegate_executor, register_agent_tools
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)


def _make_mutation_log():
    ml = AsyncMock()
    ml.record = AsyncMock(return_value=1)
    return ml


def _make_registry_with_agents(router, tool_registry, mutation_log, services=None):
    """Build a full agent registry with all three agents."""
    reg = AgentRegistry()
    reg.register("team_lead", TeamLead.create(router, tool_registry, mutation_log, services=services))
    reg.register("researcher", Researcher.create(router, tool_registry, mutation_log, services=services))
    reg.register("coder", Coder.create(router, tool_registry, mutation_log, services=services))
    return reg


class TestE2EDelegateToResearcher:
    """Lapwing delegates → Team Lead → Researcher → result."""

    async def test_full_chain(self):
        mutation_log = _make_mutation_log()

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

        # Order: TL round1, Researcher round1, Researcher round2, TL round2
        router = MagicMock()
        router.complete_with_tools = AsyncMock(
            side_effect=[tl_round1, researcher_round1, researcher_round2, tl_round2],
        )
        router.build_tool_result_message = MagicMock(
            return_value={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        )

        # Tool registry: mock function_tools + execute
        tool_registry = MagicMock()

        def _get_tool(name):
            spec = MagicMock()
            spec.name = name
            spec.description = f"{name} tool"
            spec.json_schema = {"type": "object", "properties": {}}
            return spec

        tool_registry.get = MagicMock(side_effect=_get_tool)
        tool_registry.function_tools = MagicMock(return_value=[])
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(
            success=True, payload={"results": ["X info"]},
        ))

        agent_registry = _make_registry_with_agents(router, tool_registry, mutation_log)

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={
                "agent_registry": agent_registry,
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

    Step 6 对齐：mutation_log 埋点替代 dispatcher——验证 agent.task_started /
    agent.tool_called / agent.task_done 都走 mutation_log.record。
    """

    async def test_real_chain_lapwing_to_researcher(self):
        """delegate → TeamLead → delegate_to_agent → Researcher → research → 结果。"""
        from src.tools.agent_tools import delegate_to_agent_executor
        from src.tools.registry import ToolRegistry

        mutation_log = _make_mutation_log()

        real_registry = ToolRegistry()

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

        async def fake_research(req, ctx):
            return ToolExecutionResult(
                success=True,
                payload={
                    "answer": "RAG 是检索增强生成技术。",
                    "evidence": [{
                        "source_url": "https://arxiv.org/abs/2025.12345",
                        "source_name": "RAG Paper 2025",
                        "quote": "RAG is a retrieval augmented generation technique.",
                    }],
                    "confidence": "high",
                    "unclear": "",
                },
            )

        real_registry.register(ToolSpec(
            name="research",
            description="回答需要查找信息的问题",
            json_schema={
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            executor=fake_research,
            capability="web",
            risk_level="low",
        ))

        # browse 也在 Researcher profile 白名单里；注册一个 noop 满足严格
        # 校验（Step 1 §4.2：tool_names 必须全部已注册）。
        async def _noop_browse(req, ctx):
            return ToolExecutionResult(success=True, payload={})

        real_registry.register(ToolSpec(
            name="browse",
            description="browse noop",
            json_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            executor=_noop_browse,
            capability="browser",
            risk_level="low",
        ))

        agent_registry = AgentRegistry()
        agent_services = {
            "agent_registry": agent_registry,
        }

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
                id="r1", name="research",
                arguments={"question": "RAG 最新论文"},
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
        router.build_tool_result_message = MagicMock(
            return_value={"role": "tool", "tool_call_id": "x", "name": "x", "content": "ok"},
        )

        agent_registry.register(
            "team_lead",
            TeamLead.create(router, real_registry, mutation_log, services=agent_services),
        )
        agent_registry.register(
            "researcher",
            Researcher.create(router, real_registry, mutation_log, services=agent_services),
        )
        agent_registry.register(
            "coder",
            Coder.create(router, real_registry, mutation_log, services=agent_services),
        )

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={
                "agent_registry": agent_registry,
            },
        )
        req = ToolExecutionRequest(
            name="delegate",
            arguments={"request": "帮我查一下最新的 RAG 论文"},
        )

        result = await delegate_executor(req, ctx)

        assert result.success, f"delegate 失败: {result.reason}"
        assert "RAG" in result.payload["result"]

        # 4 次 LLM 调用（TL×2 + Researcher×2）
        assert router.complete_with_tools.await_count == 4

        # mutation_log 收到了各层事件
        recorded_types = [
            call.args[0] for call in mutation_log.record.call_args_list if call.args
        ]
        assert MutationType.AGENT_STARTED in recorded_types
        assert MutationType.AGENT_TOOL_CALL in recorded_types
        assert MutationType.AGENT_COMPLETED in recorded_types

    async def test_dynamic_agent_list_in_description(self):
        """Step 6 改动 5：register_agent_tools 从 AgentRegistry 动态填充 description。"""
        from src.agents.registry import AgentRegistry
        from src.tools.registry import ToolRegistry

        tool_registry = ToolRegistry()
        agent_registry = AgentRegistry()

        mutation_log = _make_mutation_log()
        router = MagicMock()

        agent_registry.register(
            "researcher",
            Researcher.create(router, tool_registry, mutation_log),
        )
        agent_registry.register(
            "coder",
            Coder.create(router, tool_registry, mutation_log),
        )

        register_agent_tools(tool_registry, agent_registry)

        delegate_spec = tool_registry.get("delegate")
        assert delegate_spec is not None
        assert "researcher" in delegate_spec.description
        assert "coder" in delegate_spec.description

        to_agent_spec = tool_registry.get("delegate_to_agent")
        assert to_agent_spec is not None
        # agent 参数 enum 动态填充
        agent_prop = to_agent_spec.json_schema["properties"]["agent"]
        assert "enum" in agent_prop
        assert set(agent_prop["enum"]) == {"researcher", "coder"}
