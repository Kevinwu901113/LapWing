"""src/agents/dynamic.py — Dynamic agents with runtime denylist enforcement.

Per blueprint §3:
  DynamicAgent extends BaseAgent. It runs the same tool loop, but every tool
  call is gated through DYNAMIC_AGENT_DENYLIST + spec.tool_denylist BEFORE
  reaching the registry. Denied calls emit TOOL_DENIED (guard=
  "dynamic_agent_denylist") and return a synthetic tool_result to the LLM
  so the loop can continue (the LLM gets a chance to redirect).

Budget integration lives in BaseAgent (Task 9) so both builtin and dynamic
agents share the per-turn BudgetLedger.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.agents.base import BaseAgent
from src.agents.spec import DYNAMIC_AGENT_DENYLIST
from src.agents.types import LegacyAgentSpec
from src.logging.state_mutation_log import MutationType

if TYPE_CHECKING:
    from src.agents.spec import AgentSpec
    from src.agents.types import AgentMessage
    from src.core.llm_router import LLMRouter
    from src.core.runtime_profiles import RuntimeProfile
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry

logger = logging.getLogger("lapwing.agents.dynamic")


class DynamicAgent(BaseAgent):
    """Configuration-driven agent with hardcoded runtime denylist.

    Differences from BaseAgent:
      1. Constructor takes the new AgentSpec; adapts to LegacyAgentSpec
         internally so BaseAgent's existing tool loop still works.
      2. _execute_tool checks DYNAMIC_AGENT_DENYLIST + spec.tool_denylist
         before delegating to super(); denied calls emit TOOL_DENIED.
    """

    def __init__(
        self,
        spec: "AgentSpec",
        profile: "RuntimeProfile",
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        mutation_log: "StateMutationLog | None",
        services: dict[str, Any] | None = None,
    ):
        # Adapt the new AgentSpec to a LegacyAgentSpec so BaseAgent can consume it.
        # NOTE: only the fields BaseAgent already understands are mapped here.
        # Tasks 8 & 9 read the remaining resource_limits fields directly off
        # self.dynamic_spec.resource_limits — specifically max_llm_calls and
        # max_child_agents are NOT mirrored into the legacy spec because
        # BaseAgent has no concept of them. Enforcement happens via
        # BudgetLedger / runtime denylist, not via the legacy spec.
        legacy = LegacyAgentSpec(
            name=spec.name,
            description=spec.description,
            system_prompt=spec.system_prompt,
            model_slot=spec.model_slot,
            tools=[],
            runtime_profile=profile,
            max_rounds=spec.resource_limits.max_tool_calls,
            max_tokens=spec.resource_limits.max_tokens,
            timeout_seconds=spec.resource_limits.max_wall_time_seconds,
        )
        super().__init__(legacy, llm_router, tool_registry, mutation_log, services)
        # Keep the new spec accessible for the runtime denylist check (Task 8)
        # and the budget hooks (Task 9 reads dynamic_spec.resource_limits).
        self.dynamic_spec = spec

    async def _execute_tool(self, tool_call, message: "AgentMessage") -> str:
        """Override: enforce DYNAMIC_AGENT_DENYLIST + spec.tool_denylist
        before delegating to BaseAgent._execute_tool."""

        denied_reason = self._is_denied(tool_call.name)
        if denied_reason is not None:
            guard_name = "agent_policy" if "AgentPolicy" in denied_reason else "dynamic_agent_denylist"
            await self._emit(
                MutationType.TOOL_DENIED,
                payload={
                    "tool": tool_call.name,
                    "guard": guard_name,
                    "reason": denied_reason,
                    "auth_level": 1,
                    "agent_name": self.spec.name,
                },
            )
            logger.info(
                "[dynamic_agent] denied tool=%s agent=%s reason=%s",
                tool_call.name, self.spec.name, denied_reason,
            )
            return json.dumps(
                {
                    "error": "tool_forbidden",
                    "tool": tool_call.name,
                    "reason": denied_reason,
                },
                ensure_ascii=False,
            )

        return await super()._execute_tool(tool_call, message)

    def _is_denied(self, tool_name: str) -> str | None:
        """Return a human-readable reason if the tool is denied, else None."""
        policy = (self._services or {}).get("agent_policy")
        if not policy:
            return "missing AgentPolicy in services (fail-closed)"

        if not policy.validate_tool_access(self.dynamic_spec, tool_name):
            return "blocked by AgentPolicy"

        if tool_name in DYNAMIC_AGENT_DENYLIST:
            return "blocked by DYNAMIC_AGENT_DENYLIST"
        if tool_name in self.dynamic_spec.tool_denylist:
            return "blocked by spec.tool_denylist"
        return None
