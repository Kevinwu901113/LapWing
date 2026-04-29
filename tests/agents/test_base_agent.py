"""BaseAgent tool loop 测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.agents.base import BaseAgent
from src.agents.types import AgentMessage, AgentSpec
from src.logging.state_mutation_log import MutationType


def _make_spec(**overrides):
    defaults = dict(
        name="test_agent",
        description="test",
        system_prompt="You are a test agent.",
        model_slot="agent_execution",
        tools=["web_search"],
        max_rounds=5,
        max_tokens=10000,
        timeout_seconds=10,
    )
    defaults.update(overrides)
    return AgentSpec(**defaults)


def _make_message(content="do something", task_id="t1"):
    return AgentMessage(
        from_agent="lapwing",
        to_agent="test_agent",
        task_id=task_id,
        content=content,
        message_type="request",
    )


def _make_deps(tool_turn_result=None, tool_exec_result=None):
    """Create mock llm_router, tool_registry, mutation_log."""
    from src.core.llm_types import ToolTurnResult

    router = MagicMock()
    if tool_turn_result is None:
        tool_turn_result = ToolTurnResult(
            text="Done.", tool_calls=[], continuation_message=None,
        )
    router.complete_with_tools = AsyncMock(return_value=tool_turn_result)
    router.build_tool_result_message = MagicMock(
        return_value={"role": "user", "content": "tool result"},
    )

    registry = MagicMock()
    tool_spec = MagicMock()
    tool_spec.name = "web_search"
    tool_spec.description = "Search the web"
    tool_spec.json_schema = {"type": "object", "properties": {}}
    tool_spec.to_function_tool = MagicMock(
        return_value={"type": "function", "function": {"name": "web_search"}},
    )
    registry.get = MagicMock(return_value=tool_spec)
    registry.function_tools = MagicMock(
        return_value=[{"type": "function", "function": {"name": "web_search"}}],
    )

    if tool_exec_result is None:
        from src.tools.types import ToolExecutionResult
        tool_exec_result = ToolExecutionResult(
            success=True, payload={"results": ["result1"]},
        )
    registry.execute = AsyncMock(return_value=tool_exec_result)

    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock(return_value=1)

    return router, registry, mutation_log


def _event_types_called(mutation_log):
    """Collect MutationType values passed as first positional arg to record()."""
    return [
        call.args[0] if call.args else call.kwargs.get("event_type")
        for call in mutation_log.record.call_args_list
    ]


class TestBaseAgentNoCalls:
    async def test_returns_done(self):
        spec = _make_spec()
        router, registry, mutation_log = _make_deps()
        agent = BaseAgent(spec, router, registry, mutation_log)
        result = await agent.execute(_make_message())
        assert result.status == "done"
        assert result.result == "Done."

    async def test_emits_started_and_completed_mutations(self):
        spec = _make_spec()
        router, registry, mutation_log = _make_deps()
        agent = BaseAgent(spec, router, registry, mutation_log)
        await agent.execute(_make_message())
        types = _event_types_called(mutation_log)
        assert MutationType.AGENT_STARTED in types
        assert MutationType.AGENT_COMPLETED in types
        assert MutationType.AGENT_FAILED not in types


class TestBaseAgentWithToolCalls:
    async def test_executes_tool_and_returns(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={"query": "test"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(
            text="Found results.", tool_calls=[], continuation_message=None,
        )

        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])

        agent = BaseAgent(_make_spec(), router, registry, mutation_log)
        result = await agent.execute(_make_message())
        assert result.status == "done"
        assert result.result == "Found results."
        assert registry.execute.await_count == 1

    async def test_emits_tool_call_mutation(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={"query": "q"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(text="ok", tool_calls=[], continuation_message=None)

        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])

        agent = BaseAgent(_make_spec(), router, registry, mutation_log)
        await agent.execute(_make_message())

        types = _event_types_called(mutation_log)
        assert MutationType.AGENT_TOOL_CALL in types

    async def test_tool_call_count_in_completed_payload(self):
        """AGENT_COMPLETED payload 带 tool_calls_made 与 duration。"""
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(text="done", tool_calls=[], continuation_message=None)

        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])

        agent = BaseAgent(_make_spec(), router, registry, mutation_log)
        await agent.execute(_make_message())

        completed_calls = [
            call for call in mutation_log.record.call_args_list
            if call.args and call.args[0] == MutationType.AGENT_COMPLETED
        ]
        assert len(completed_calls) == 1
        payload = completed_calls[0].kwargs.get("payload") or completed_calls[0].args[1]
        assert payload["tool_calls_made"] == 1
        assert "duration_seconds" in payload
        assert payload["agent_name"] == "test_agent"


class TestBaseAgentMaxRounds:
    async def test_fails_on_max_rounds(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        always_calls = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={})],
            continuation_message={"role": "assistant", "content": ""},
        )

        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(return_value=always_calls)

        spec = _make_spec(max_rounds=3)
        agent = BaseAgent(spec, router, registry, mutation_log)
        result = await agent.execute(_make_message())
        assert result.status == "failed"
        assert "3" in result.reason
        types = _event_types_called(mutation_log)
        assert MutationType.AGENT_FAILED in types


class TestBaseAgentTimeout:
    async def test_timeout_returns_failed(self):
        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=asyncio.TimeoutError)

        spec = _make_spec(timeout_seconds=1)
        agent = BaseAgent(spec, router, registry, mutation_log)
        result = await agent.execute(_make_message())
        assert result.status == "failed"
        assert "超时" in result.reason or "timeout" in result.reason.lower()
        types = _event_types_called(mutation_log)
        assert MutationType.AGENT_FAILED in types


class TestEvidenceCollection:
    """修复 B：evidence 应在 _execute_tool 中即时收集，而不是回头解析 messages。"""

    async def test_evidence_collected_from_top_level_source_url(self):
        """工具 payload 含顶层 source_url → evidence 至少一条。"""
        from src.core.llm_types import ToolCallRequest, ToolTurnResult
        from src.tools.types import ToolExecutionResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search",
                                        arguments={"query": "RAG"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(text="done", tool_calls=[],
                                continuation_message=None)

        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])
        registry.execute = AsyncMock(return_value=ToolExecutionResult(
            success=True,
            payload={
                "answer": "RAG 论文摘要",
                "source_url": "https://arxiv.org/abs/2026.12345",
                "snippet": "This paper proposes a new method.",
            },
        ))

        agent = BaseAgent(_make_spec(), router, registry, mutation_log)
        result = await agent.execute(_make_message())

        assert result.status == "done"
        assert len(result.evidence) >= 1
        urls = [e.get("source_url") for e in result.evidence]
        assert "https://arxiv.org/abs/2026.12345" in urls
        assert all(e["tool"] == "web_search" for e in result.evidence)

    async def test_evidence_collected_from_nested_evidence_list(self):
        """payload.evidence 是 list[dict] → 每条 source_url 都被收进来。"""
        from src.core.llm_types import ToolCallRequest, ToolTurnResult
        from src.tools.types import ToolExecutionResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search",
                                        arguments={"q": "x"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(text="ok", tool_calls=[],
                                continuation_message=None)

        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])
        registry.execute = AsyncMock(return_value=ToolExecutionResult(
            success=True,
            payload={
                "answer": "综合答案",
                "evidence": [
                    {"source_url": "https://a.example/1", "title": "A"},
                    {"source_url": "https://b.example/2", "title": "B"},
                ],
            },
        ))

        agent = BaseAgent(_make_spec(), router, registry, mutation_log)
        result = await agent.execute(_make_message())

        urls = {e.get("source_url") for e in result.evidence
                if e.get("source_url")}
        assert "https://a.example/1" in urls
        assert "https://b.example/2" in urls

    async def test_evidence_empty_when_no_source(self):
        """无 url / 文件 / answer 字段 → evidence 应为空。"""
        from src.core.llm_types import ToolCallRequest, ToolTurnResult
        from src.tools.types import ToolExecutionResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="run_python_code",
                                        arguments={"code": "1+1"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(text="ok", tool_calls=[],
                                continuation_message=None)

        router, registry, mutation_log = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])
        registry.execute = AsyncMock(return_value=ToolExecutionResult(
            success=True,
            payload={"stdout": "2\n", "exit_code": 0},
        ))

        agent = BaseAgent(_make_spec(), router, registry, mutation_log)
        result = await agent.execute(_make_message())

        assert result.status == "done"
        assert result.evidence == []

    async def test_evidence_reset_between_executions(self):
        """同一个 agent 实例跑两次任务，第二次 evidence 不应包含第一次的内容。"""
        from src.core.llm_types import ToolCallRequest, ToolTurnResult
        from src.tools.types import ToolExecutionResult

        def _make_round_pair(url):
            r1 = ToolTurnResult(
                text="",
                tool_calls=[ToolCallRequest(id="tc1", name="web_search",
                                            arguments={})],
                continuation_message={"role": "assistant", "content": ""},
            )
            r2 = ToolTurnResult(text="ok", tool_calls=[],
                                continuation_message=None)
            return r1, r2

        router, registry, mutation_log = _make_deps()
        first_pair = _make_round_pair("first")
        second_pair = _make_round_pair("second")
        router.complete_with_tools = AsyncMock(side_effect=[
            *first_pair, *second_pair,
        ])

        # 第一次返回 url=first，第二次返回不带 url 的 payload
        registry.execute = AsyncMock(side_effect=[
            ToolExecutionResult(success=True,
                                payload={"source_url": "https://first/x"}),
            ToolExecutionResult(success=True,
                                payload={"stdout": "no source"}),
        ])

        agent = BaseAgent(_make_spec(), router, registry, mutation_log)
        r1 = await agent.execute(_make_message(task_id="t1"))
        r2 = await agent.execute(_make_message(task_id="t2"))

        assert any(e.get("source_url") == "https://first/x"
                   for e in r1.evidence)
        assert r2.evidence == [], "第二次 execute 不应残留第一次的 evidence"


class TestBaseAgentRuntimeProfile:
    """Step 6 改动 2：_get_tools 走 RuntimeProfile 过滤。"""

    async def test_profile_drives_tool_filtering(self):
        from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE

        spec = _make_spec(
            tools=[],
            runtime_profile=AGENT_RESEARCHER_PROFILE,
        )
        router, registry, mutation_log = _make_deps()

        agent = BaseAgent(spec, router, registry, mutation_log)
        await agent.execute(_make_message())

        # 走 profile 路径：function_tools(capabilities=None, tool_names={
        # research, browse, get_sports_score}) — get_sports_score migrated
        # to the Researcher's surface in the agents-as-tools refactor.
        assert registry.function_tools.called
        call_kwargs = registry.function_tools.call_args.kwargs
        assert call_kwargs.get("tool_names") == {
            "research", "browse", "get_sports_score",
        }
        assert call_kwargs.get("include_internal") is False

    async def test_legacy_tools_fallback(self):
        """没有 profile 时走 tools 列表——仅为过渡期 fixtures 服务。"""
        spec = _make_spec(tools=["web_search"], runtime_profile=None)
        router, registry, mutation_log = _make_deps()

        agent = BaseAgent(spec, router, registry, mutation_log)
        await agent.execute(_make_message())

        # 走 legacy 路径：registry.get(tool_name) 按名取 spec
        assert registry.get.called
        assert not registry.function_tools.called


# ----------------------------------------------------------------------------
# Task 9: BudgetLedger hooks (T-08)
# ----------------------------------------------------------------------------

import pytest
from src.agents.budget import BudgetLedger


def _build_simple_agent(ledger: BudgetLedger | None = None,
                        max_rounds: int = 5, fake_llm_response=None):
    """Construct a BaseAgent backed by a fake LLM router that loops."""
    from src.agents.types import LegacyAgentSpec

    spec = LegacyAgentSpec(
        name="probe", description="", system_prompt="p",
        model_slot="agent_researcher", max_rounds=max_rounds,
    )
    router = MagicMock()
    if fake_llm_response is None:
        from dataclasses import dataclass, field as _f

        @dataclass
        class _R:
            text: str = ""
            tool_calls: list = _f(default_factory=list)
            continuation_message: dict | None = None

        @dataclass
        class _TC:
            name: str = "research"
            arguments: dict = _f(default_factory=dict)
            id: str = "c1"

        n = {"i": 0}

        async def _complete(**kwargs):
            n["i"] += 1
            if n["i"] <= max_rounds:
                return _R(
                    tool_calls=[_TC()],
                    continuation_message={"role": "assistant", "content": ""},
                )
            return _R(text="done")

        router.complete_with_tools = AsyncMock(side_effect=_complete)
    else:
        router.complete_with_tools = AsyncMock(return_value=fake_llm_response)
    router.build_tool_result_message = MagicMock(
        return_value={"role": "user", "content": "x"},
    )

    class _Reg:
        def __init__(self):
            self.executed = []

        def function_tools(self, **kw):
            return []

        def get(self, name):
            return None

        async def execute(self, req, context):
            self.executed.append(req.name)
            from src.tools.types import ToolExecutionResult
            return ToolExecutionResult(success=True, payload={})

    class _Log:
        def __init__(self):
            self.events = []

        async def record(self, event_type, payload, **kwargs):
            self.events.append((event_type, payload))

    services = {}
    if ledger is not None:
        services["budget_ledger"] = ledger
    log = _Log()
    agent = BaseAgent(spec, router, _Reg(), log, services=services)
    return agent, log


@pytest.mark.asyncio
async def test_t08_llm_call_budget_exhausted():
    """Ledger with max_llm_calls=2: third LLM call raises → AgentResult.budget_status."""
    ledger = BudgetLedger(max_llm_calls=2)
    agent, log = _build_simple_agent(ledger=ledger, max_rounds=10)
    msg = AgentMessage(
        from_agent="lapwing", to_agent="probe", task_id="t1",
        content="x", message_type="request",
    )
    result = await agent.execute(msg)
    assert result.budget_status == "budget_exhausted"
    assert result.status == "done"
    types = [evt[0] for evt in log.events]
    assert MutationType.AGENT_BUDGET_EXHAUSTED in types


@pytest.mark.asyncio
async def test_t08_tool_call_budget_exhausted():
    """Ledger with max_tool_calls=1: second tool call raises → done with budget_status."""
    ledger = BudgetLedger(max_llm_calls=100, max_tool_calls=1)
    agent, log = _build_simple_agent(ledger=ledger, max_rounds=10)
    msg = AgentMessage(
        from_agent="lapwing", to_agent="probe", task_id="t",
        content="x", message_type="request",
    )
    result = await agent.execute(msg)
    assert result.budget_status == "budget_exhausted"


@pytest.mark.asyncio
async def test_no_ledger_means_no_budget_enforcement():
    """When services has no 'budget_ledger', tool loop runs unchanged."""
    agent, log = _build_simple_agent(ledger=None, max_rounds=2)
    msg = AgentMessage(
        from_agent="lapwing", to_agent="probe", task_id="t",
        content="x", message_type="request",
    )
    result = await agent.execute(msg)
    assert result.budget_status == ""


@pytest.mark.asyncio
async def test_budget_exhausted_payload_structure():
    """AGENT_BUDGET_EXHAUSTED payload contains required keys per blueprint §11.2."""
    ledger = BudgetLedger(max_llm_calls=1)
    agent, log = _build_simple_agent(ledger=ledger, max_rounds=10)
    msg = AgentMessage(
        from_agent="lapwing", to_agent="probe", task_id="task42",
        content="x", message_type="request",
    )
    await agent.execute(msg)
    bx = [(t, p) for (t, p) in log.events
          if t == MutationType.AGENT_BUDGET_EXHAUSTED]
    assert bx, "expected AGENT_BUDGET_EXHAUSTED event"
    payload = bx[0][1]
    assert payload["agent_name"] == "probe"
    assert payload["task_id"] == "task42"
    assert payload["dimension"] == "llm_calls"
    assert payload["used"] == 2  # incremented before exception
    assert payload["limit"] == 1
    assert "partial_result" in payload
