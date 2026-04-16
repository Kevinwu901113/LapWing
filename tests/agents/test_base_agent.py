"""BaseAgent tool loop 测试。"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import BaseAgent
from src.agents.types import AgentMessage, AgentResult, AgentSpec


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
        from_agent="team_lead",
        to_agent="test_agent",
        task_id=task_id,
        content=content,
        message_type="request",
    )


def _make_deps(tool_turn_result=None, tool_exec_result=None):
    """Create mock llm_router, tool_registry, dispatcher."""
    from src.core.llm_types import ToolCallRequest, ToolTurnResult

    router = MagicMock()
    if tool_turn_result is None:
        tool_turn_result = ToolTurnResult(
            text="Done.", tool_calls=[], continuation_message=None,
        )
    router.complete_with_tools = AsyncMock(return_value=tool_turn_result)
    router.build_tool_result_message = MagicMock(return_value={"role": "user", "content": "tool result"})

    registry = MagicMock()
    tool_spec = MagicMock()
    tool_spec.name = "web_search"
    tool_spec.description = "Search the web"
    tool_spec.json_schema = {"type": "object", "properties": {}}
    registry.get = MagicMock(return_value=tool_spec)

    if tool_exec_result is None:
        from src.tools.types import ToolExecutionResult
        tool_exec_result = ToolExecutionResult(
            success=True, payload={"results": ["result1"]},
        )
    registry.execute = AsyncMock(return_value=tool_exec_result)

    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")

    return router, registry, dispatcher


class TestBaseAgentNoCalls:
    async def test_returns_done(self):
        spec = _make_spec()
        router, registry, dispatcher = _make_deps()
        agent = BaseAgent(spec, router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "done"
        assert result.result == "Done."

    async def test_publishes_start_event(self):
        spec = _make_spec()
        router, registry, dispatcher = _make_deps()
        agent = BaseAgent(spec, router, registry, dispatcher)
        await agent.execute(_make_message())
        # Verify at least one call with agent.task_started
        started_calls = [
            c for c in dispatcher.submit.call_args_list
            if c.kwargs.get("event_type") == "agent.task_started"
        ]
        assert len(started_calls) >= 1


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

        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])

        agent = BaseAgent(_make_spec(), router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "done"
        assert result.result == "Found results."
        assert registry.execute.await_count == 1

    async def test_publishes_tool_called_event(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={"query": "q"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(text="ok", tool_calls=[], continuation_message=None)

        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])

        agent = BaseAgent(_make_spec(), router, registry, dispatcher)
        await agent.execute(_make_message())

        tool_events = [
            c for c in dispatcher.submit.call_args_list
            if c.kwargs.get("event_type") == "agent.tool_called"
        ]
        assert len(tool_events) >= 1


class TestBaseAgentMaxRounds:
    async def test_fails_on_max_rounds(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        always_calls = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={})],
            continuation_message={"role": "assistant", "content": ""},
        )

        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(return_value=always_calls)

        spec = _make_spec(max_rounds=3)
        agent = BaseAgent(spec, router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "failed"
        assert "3" in result.reason


class TestBaseAgentTimeout:
    async def test_timeout_returns_failed(self):
        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=asyncio.TimeoutError)

        spec = _make_spec(timeout_seconds=1)
        agent = BaseAgent(spec, router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "failed"
        assert "超时" in result.reason or "timeout" in result.reason.lower()
