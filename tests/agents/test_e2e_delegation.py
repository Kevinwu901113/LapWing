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


def _make_registry_with_agents(router, tool_registry, dispatcher):
    """Build a full agent registry with all three agents."""
    reg = AgentRegistry()
    reg.register("team_lead", TeamLead.create(router, tool_registry, dispatcher))
    reg.register("researcher", Researcher.create(router, tool_registry, dispatcher))
    reg.register("coder", Coder.create(router, tool_registry, dispatcher))
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
