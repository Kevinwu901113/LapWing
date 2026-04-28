"""src/agents/dynamic.py — STUB. Task 8 replaces this with full implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agents.base import BaseAgent
from src.agents.types import LegacyAgentSpec

if TYPE_CHECKING:
    from src.agents.spec import AgentSpec
    from src.core.llm_router import LLMRouter
    from src.core.runtime_profiles import RuntimeProfile
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry


class DynamicAgent(BaseAgent):
    """Stub — Task 8 fleshes out runtime denylist + budget hooks."""

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
        # Task 8 may revisit this if BaseAgent grows native AgentSpec support.
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
        # Keep the new spec accessible for the runtime denylist check (Task 8).
        self.dynamic_spec = spec
