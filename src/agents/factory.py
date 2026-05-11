"""src/agents/factory.py — AgentFactory: AgentSpec → BaseAgent instance."""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.agents.coder import Coder
from src.agents.dynamic import DynamicAgent
from src.agents.researcher import Researcher
from src.agents.resident_operator import ResidentOperator
from src.agents.spec import AgentSpec, DYNAMIC_AGENT_DENYLIST
from src.core.runtime_profiles import RuntimeProfile, get_runtime_profile

if TYPE_CHECKING:
    from src.agents.base import BaseAgent
    from src.core.llm_router import LLMRouter
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry

logger = logging.getLogger("lapwing.agents.factory")

DYNAMIC_AGENT_WORKSPACE_ROOT = "/tmp/lapwing/agents"


class AgentFactory:
    """Construct an Agent instance from an AgentSpec.

    Builtin specs (kind=="builtin") route to existing Researcher/Coder
    classmethods, ignoring the new AgentSpec's prompt/limits — the builtin
    constructors generate their own LegacyAgentSpec internally.

    Dynamic specs construct DynamicAgent with the resolved RuntimeProfile
    (with DYNAMIC_AGENT_DENYLIST + spec.tool_denylist merged in) and a
    workspace cwd at /tmp/lapwing/agents/{spec.id}/.
    """

    def __init__(
        self,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        mutation_log: "StateMutationLog | None",
    ) -> None:
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.mutation_log = mutation_log

    def create(
        self,
        spec: AgentSpec,
        services_override: dict[str, Any] | None = None,
    ) -> "BaseAgent":
        if spec.kind == "builtin":
            return self._create_builtin(spec, services_override=services_override)
        return self._create_dynamic(spec, services_override=services_override)

    def _create_builtin(
        self,
        spec: AgentSpec,
        *,
        services_override: dict[str, Any] | None = None,
    ) -> "BaseAgent":
        if spec.name == "researcher":
            return Researcher.create(
                self.llm_router,
                self.tool_registry,
                self.mutation_log,
                services=services_override,
            )
        if spec.name == "coder":
            return Coder.create(
                self.llm_router,
                self.tool_registry,
                self.mutation_log,
                services=services_override,
            )
        if spec.name == "resident_operator":
            # Post-v1 A §2.2: kernel-driven persistent-identity worker.
            # services_override is expected to contain "kernel" (wired by
            # LapwingBrain._build_services from AppContainer._init_kernel).
            return ResidentOperator.create(
                self.llm_router,
                self.tool_registry,
                self.mutation_log,
                services=services_override,
            )
        raise ValueError(f"Unknown builtin agent name: {spec.name}")

    def _create_dynamic(
        self,
        spec: AgentSpec,
        *,
        services_override: dict[str, Any] | None = None,
    ) -> "BaseAgent":
        profile = self._resolve_profile(spec)
        # Side effect: create the workspace dir on disk so shell_default_cwd
        # is valid before BaseAgent runs any shell tool.
        workspace = os.path.join(DYNAMIC_AGENT_WORKSPACE_ROOT, spec.id)
        os.makedirs(workspace, exist_ok=True)
        services: dict[str, Any] = dict(services_override or {})
        services["shell_default_cwd"] = workspace
        return DynamicAgent(
            spec=spec,
            profile=profile,
            llm_router=self.llm_router,
            tool_registry=self.tool_registry,
            mutation_log=self.mutation_log,
            services=services,
        )

    def _resolve_profile(self, spec: AgentSpec) -> RuntimeProfile:
        """Look up the named RuntimeProfile and merge denylists.

        For dynamic agents only: union spec.tool_denylist + DYNAMIC_AGENT_DENYLIST
        into the profile's exclude_tool_names.

        Builtins are returned with their original profile unchanged — they're
        trusted and the runtime denylist doesn't apply.
        """
        base = get_runtime_profile(spec.runtime_profile)
        if spec.kind != "dynamic":
            return base
        merged_excludes = (
            base.exclude_tool_names
            | frozenset(spec.tool_denylist)
            | DYNAMIC_AGENT_DENYLIST
        )
        return replace(base, exclude_tool_names=merged_excludes)
