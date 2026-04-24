"""端到端 delegation 测试：Lapwing → Agent → 结果。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.coder import Coder
from src.agents.registry import AgentRegistry
from src.agents.researcher import Researcher
from src.core.llm_types import ToolCallRequest, ToolTurnResult
from src.logging.state_mutation_log import MutationType
from src.tools.agent_tools import (
    delegate_to_researcher_executor,
    delegate_to_coder_executor,
    register_agent_tools,
)
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


class TestE2EDelegateToResearcher:
    """Lapwing → delegate_to_researcher → Researcher → result."""

    async def test_full_chain(self):
        mutation_log = _make_mutation_log()

        researcher_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="r1", name="research", arguments={"question": "RAG"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        researcher_round2 = ToolTurnResult(
            text="RAG 是检索增强生成技术。[来源: https://arxiv.org/abs/2025.12345]",
            tool_calls=[],
            continuation_message=None,
        )

        router = MagicMock()
        router.complete_with_tools = AsyncMock(
            side_effect=[researcher_round1, researcher_round2],
        )
        router.build_tool_result_message = MagicMock(
            return_value={"role": "user", "content": "tool result"},
        )

        tool_registry = MagicMock()
        tool_registry.get = MagicMock(return_value=MagicMock())
        tool_registry.function_tools = MagicMock(return_value=[])
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(
            success=True,
            payload={"answer": "RAG info", "evidence": [], "confidence": "high", "unclear": ""},
        ))

        agent_registry = AgentRegistry()
        agent_services = {"agent_registry": agent_registry}
        agent_registry.register(
            "researcher",
            Researcher.create(router, tool_registry, mutation_log, services=agent_services),
        )
        agent_registry.register(
            "coder",
            Coder.create(router, tool_registry, mutation_log, services=agent_services),
        )

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={"agent_registry": agent_registry},
        )
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "帮我查一下最新的 RAG 论文"},
        )

        result = await delegate_to_researcher_executor(req, ctx)
        assert result.success, f"delegate 失败: {result.reason}"
        assert "RAG" in result.payload["result"]

        # 2 次 LLM 调用（Researcher×2），不再有 TeamLead 中间层
        assert router.complete_with_tools.await_count == 2

        recorded_types = [
            call.args[0] for call in mutation_log.record.call_args_list if call.args
        ]
        assert MutationType.AGENT_STARTED in recorded_types
        assert MutationType.AGENT_TOOL_CALL in recorded_types
        assert MutationType.AGENT_COMPLETED in recorded_types


class TestE2ERealDelegation:
    """真实端到端测试：tool_registry.execute 不 mock。

    验证 services 传递正确：delegate executor → Agent → _execute_tool
    → ToolExecutionContext(services=...) → research 工具。
    """

    async def test_real_chain_lapwing_to_researcher(self):
        """delegate_to_researcher → Researcher → research → 结果。"""
        from src.tools.registry import ToolRegistry

        mutation_log = _make_mutation_log()
        real_registry = ToolRegistry()

        async def fake_research(req, ctx):
            return ToolExecutionResult(
                success=True,
                payload={
                    "answer": "RAG 是检索增强生成技术。",
                    "evidence": [{"source_url": "https://arxiv.org/abs/2025.12345"}],
                    "confidence": "high",
                    "unclear": "",
                },
            )

        real_registry.register(ToolSpec(
            name="research",
            description="回答需要查找信息的问题",
            json_schema={"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
            executor=fake_research,
            capability="web",
            risk_level="low",
        ))

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
        agent_services = {"agent_registry": agent_registry}

        router = MagicMock()

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

        router.complete_with_tools = AsyncMock(
            side_effect=[researcher_round1, researcher_round2],
        )
        router.build_tool_result_message = MagicMock(
            return_value={"role": "tool", "tool_call_id": "x", "name": "x", "content": "ok"},
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
            services={"agent_registry": agent_registry},
        )
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "帮我查一下最新的 RAG 论文"},
        )

        result = await delegate_to_researcher_executor(req, ctx)
        assert result.success, f"delegate 失败: {result.reason}"
        assert "RAG" in result.payload["result"]
        assert router.complete_with_tools.await_count == 2

        recorded_types = [
            call.args[0] for call in mutation_log.record.call_args_list if call.args
        ]
        assert MutationType.AGENT_STARTED in recorded_types
        assert MutationType.AGENT_COMPLETED in recorded_types

    async def test_dynamic_tool_registration(self):
        """register_agent_tools 注册 delegate_to_researcher + delegate_to_coder。"""
        from src.tools.registry import ToolRegistry

        tool_registry = ToolRegistry()
        register_agent_tools(tool_registry)

        researcher_spec = tool_registry.get("delegate_to_researcher")
        assert researcher_spec is not None
        assert "Researcher" in researcher_spec.description

        coder_spec = tool_registry.get("delegate_to_coder")
        assert coder_spec is not None
        assert "Coder" in coder_spec.description


class TestContextDigest:
    """context_digest 为空和非空两种场景。"""

    async def test_empty_context_digest_works(self):
        """最常见的生产场景——delegate 不带 context，agent 照常工作。"""
        mutation_log = _make_mutation_log()

        router = MagicMock()
        router.complete_with_tools = AsyncMock(return_value=ToolTurnResult(
            text="Done.", tool_calls=[], continuation_message=None,
        ))

        tool_registry = MagicMock()
        tool_registry.function_tools = MagicMock(return_value=[])

        agent_registry = AgentRegistry()
        agent_services = {"agent_registry": agent_registry}
        agent_registry.register(
            "researcher",
            Researcher.create(router, tool_registry, mutation_log, services=agent_services),
        )

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={"agent_registry": agent_registry},
        )
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "查天气"},
        )

        result = await delegate_to_researcher_executor(req, ctx)
        assert result.success

    async def test_context_digest_injected_into_prompt(self):
        """子 agent system prompt 确实包含了上下文段落。"""
        mutation_log = _make_mutation_log()

        captured_messages = []
        router = MagicMock()

        async def _capture_and_respond(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return ToolTurnResult(text="Done.", tool_calls=[], continuation_message=None)

        router.complete_with_tools = AsyncMock(side_effect=_capture_and_respond)

        tool_registry = MagicMock()
        tool_registry.function_tools = MagicMock(return_value=[])

        agent_registry = AgentRegistry()
        agent_services = {"agent_registry": agent_registry}
        agent_registry.register(
            "researcher",
            Researcher.create(router, tool_registry, mutation_log, services=agent_services),
        )

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={"agent_registry": agent_registry},
        )
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={
                "request": "查一下",
                "context_digest": "Kevin 在讨论 2026 年 RAG 论文",
            },
        )

        await delegate_to_researcher_executor(req, ctx)

        system_msg = next(m for m in captured_messages if m["role"] == "system")
        assert "来自主人格的上下文" in system_msg["content"]
        assert "2026 年 RAG 论文" in system_msg["content"]


class TestWhitelistViolation:
    """白名单违规：Researcher 被 LLM 要求调 execute_shell → 被 profile 拒绝。"""

    async def test_researcher_cannot_call_shell(self):
        from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE

        assert "execute_shell" not in AGENT_RESEARCHER_PROFILE.tool_names

    async def test_coder_cannot_call_shell(self):
        from src.core.runtime_profiles import AGENT_CODER_PROFILE

        assert "execute_shell" not in AGENT_CODER_PROFILE.tool_names


class TestParentTaskId:
    """mutation 事件带 parent_task_id。"""

    async def test_mutations_carry_parent_task_id(self):
        mutation_log = _make_mutation_log()

        router = MagicMock()
        router.complete_with_tools = AsyncMock(return_value=ToolTurnResult(
            text="Done.", tool_calls=[], continuation_message=None,
        ))

        tool_registry = MagicMock()
        tool_registry.function_tools = MagicMock(return_value=[])

        agent_registry = AgentRegistry()
        agent_services = {"agent_registry": agent_registry}
        agent_registry.register(
            "researcher",
            Researcher.create(router, tool_registry, mutation_log, services=agent_services),
        )

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={"agent_registry": agent_registry},
        )
        req = ToolExecutionRequest(
            name="delegate_to_researcher",
            arguments={"request": "test"},
        )

        await delegate_to_researcher_executor(req, ctx)

        started_calls = [
            call for call in mutation_log.record.call_args_list
            if call.args and call.args[0] == MutationType.AGENT_STARTED
        ]
        assert len(started_calls) >= 1
