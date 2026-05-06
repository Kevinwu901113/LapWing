"""src/agents/registry.py — AgentRegistry: facade over Catalog + Factory + Policy.

Per blueprint §6:
  Startup loads builtin specs into the catalog. At runtime, the registry
  resolves agent names by checking ephemeral → session → catalog, then asks
  Factory for a fresh instance each time. Session/ephemeral specs live in
  memory only; persistent specs go through `save_agent` and live in catalog.

Backwards-compat: the legacy zero-arg constructor + register/get/list_names
remain so the pre-Task-10 tests and AppContainer wiring still work.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agents.builtin_specs import (
    builtin_coder_spec as _builtin_coder_spec,
    builtin_researcher_spec as _builtin_researcher_spec,
)
from src.agents.spec import AgentSpec, AgentLifecyclePolicy

if TYPE_CHECKING:
    from src.agents.base import BaseAgent
    from src.agents.catalog import AgentCatalog
    from src.agents.factory import AgentFactory
    from src.agents.policy import AgentPolicy, CreateAgentInput

logger = logging.getLogger("lapwing.agents.registry")


@dataclass
class _SessionEntry:
    spec: AgentSpec
    scratchpad: str = ""
    created_at: float = 0.0
    last_used_at: float = 0.0
    run_count: int = 0


class AgentRegistry:
    """Facade over Catalog + Factory + Policy.

    Two-mode constructor:
      - AgentRegistry() — legacy mode, zero-arg, supports register()/get()/list_names()
      - AgentRegistry(catalog, factory, policy) — v2 mode, full API
    """

    def __init__(
        self,
        catalog: "AgentCatalog | None" = None,
        factory: "AgentFactory | None" = None,
        policy: "AgentPolicy | None" = None,
    ) -> None:
        self._catalog = catalog
        self._factory = factory
        self._policy = policy
        self._session_agents: dict[str, _SessionEntry] = {}
        self._ephemeral_agents: dict[str, AgentSpec] = {}
        # Legacy state — only populated via register()
        self._legacy_agents: dict[str, "BaseAgent"] = {}

    # ── v2 API ──

    async def init(self) -> None:
        """Ensure builtin specs exist in catalog (no-op if already there)."""
        if self._catalog is None:
            return
        for spec_factory in (_builtin_researcher_spec, _builtin_coder_spec):
            spec = spec_factory()
            existing = await self._catalog.get_by_name(spec.name)
            if existing is None:
                await self._catalog.save(spec)

    async def create_agent(
        self,
        request: "CreateAgentInput",
        ctx: Any,
    ) -> AgentSpec:
        """Create a dynamic agent. Validates via policy, places in
        session/ephemeral dict (NOT catalog)."""
        if self._policy is None:
            raise RuntimeError("AgentRegistry not configured with policy")
        # Free slots occupied by expired sessions, then count active ones.
        await self.cleanup_expired_sessions()
        session_count = len(self._session_agents)
        spec = await self._policy.validate_create(
            request, ctx, session_count=session_count,
        )
        if spec.lifecycle.mode == "ephemeral":
            self._ephemeral_agents[spec.name] = spec
        elif spec.lifecycle.mode == "session":
            now = time.monotonic()
            self._session_agents[spec.name] = _SessionEntry(
                spec=spec,
                created_at=now,
                last_used_at=now,
            )
        return spec

    async def get_or_create_instance(
        self,
        name: str,
        services_override: dict[str, Any] | None = None,
    ) -> "BaseAgent | None":
        """Return a fresh agent instance. Search order:
        ephemeral → session → catalog (builtin / persistent) → legacy → None.
        """
        # Legacy path: directly registered instances win for backwards-compat
        if name in self._legacy_agents:
            agent = self._legacy_agents[name]
            if services_override is not None:
                setattr(agent, "_services", services_override)
            return agent

        if self._factory is None:
            return None
        spec = await self._lookup_spec(name)
        if spec is None:
            return None

        # Update session last_used if this is a session agent
        if name in self._session_agents:
            self._session_agents[name].last_used_at = time.monotonic()

        return self._factory.create(spec, services_override=services_override)

    async def _lookup_spec(self, name: str) -> AgentSpec | None:
        if name in self._ephemeral_agents:
            return self._ephemeral_agents[name]
        if name in self._session_agents:
            return self._session_agents[name].spec
        if self._catalog is not None:
            return await self._catalog.get_by_name(name)
        return None

    async def destroy_agent(self, name: str) -> bool:
        """Remove a dynamic agent. Cannot destroy builtins."""
        if self._catalog is not None:
            spec = await self._catalog.get_by_name(name)
            if spec is not None and spec.kind == "builtin":
                return False
            if spec is not None and spec.lifecycle.mode == "persistent":
                # Archive rather than delete (audit trail)
                await self._catalog.archive(spec.id)
                return True
        if name in self._session_agents:
            del self._session_agents[name]
            return True
        if name in self._ephemeral_agents:
            del self._ephemeral_agents[name]
            return True
        return False

    async def save_agent(
        self,
        name: str,
        reason: str,
        run_history: list[str],
        *,
        candidate_id: str | None = None,
        candidate_store: Any = None,
        require_candidate_approval: bool = False,
    ) -> None:
        """Persist a dynamic agent's spec. Validates via policy.

        When require_candidate_approval is True and the spec is capability-backed,
        an approved AgentCandidate with sufficient evidence must be provided.
        """
        if self._policy is None or self._catalog is None:
            raise RuntimeError("AgentRegistry not configured")
        spec = await self._lookup_spec(name)
        if spec is None:
            from src.agents.policy import AgentPolicyViolation
            raise AgentPolicyViolation("agent_not_found", {"name": name})
        if spec.kind == "builtin":
            from src.agents.policy import AgentPolicyViolation
            raise AgentPolicyViolation("cannot_save_builtin", {"name": name})

        # ── Phase 6C save gate ──
        candidate = None
        if require_candidate_approval and candidate_store is not None and candidate_id is not None:
            try:
                candidate = candidate_store.get_candidate(candidate_id)
            except Exception as exc:
                from src.agents.policy import AgentPolicyViolation
                raise AgentPolicyViolation(
                    "candidate_lookup_failed",
                    {"candidate_id": candidate_id, "error": str(exc)},
                )

        gate_result = self._policy.validate_persistent_save_gate(
            spec,
            candidate=candidate,
            require_candidate_approval=require_candidate_approval,
        )
        if not gate_result.allowed:
            from src.agents.policy import AgentPolicyViolation
            raise AgentPolicyViolation(
                "save_gate_denied",
                {"reason": gate_result.reason, "denials": gate_result.denials},
            )

        await self._policy.validate_save(spec, run_history)

        # Promote to persistent and write to catalog
        from dataclasses import replace
        promoted = replace(
            spec,
            lifecycle=AgentLifecyclePolicy(
                mode="persistent",
                ttl_seconds=spec.lifecycle.ttl_seconds,
                max_runs=spec.lifecycle.max_runs,
                reusable=spec.lifecycle.reusable,
            ),
            created_reason=reason,
            version=spec.version + 1,
        )
        await self._catalog.save(promoted)

        # Remove from session/ephemeral dicts
        self._session_agents.pop(name, None)
        self._ephemeral_agents.pop(name, None)

    async def list_agents(self, *, full: bool = False, include_inactive: bool = False) -> list[dict]:
        """List all available agents (builtin + persistent + session + ephemeral).

        When include_inactive is False (default), only active agents are
        returned from the catalog. When True, archived/disabled agents are
        included too.
        """
        items: list[dict] = []
        seen_names: set[str] = set()

        # Catalog (builtin + persistent)
        if self._catalog is not None:
            status_filter = None if include_inactive else "active"
            try:
                specs = await self._catalog.list_specs(status=status_filter)
            except Exception:
                logger.exception("[AgentRegistry] catalog list_specs failed")
                raise
            for s in specs:
                items.append(self._spec_to_summary(s, full=full))
                seen_names.add(s.name)

        # Session
        for name, entry in self._session_agents.items():
            if name not in seen_names:
                items.append(self._spec_to_summary(entry.spec, full=full))
                seen_names.add(name)

        # Ephemeral
        for name, spec in self._ephemeral_agents.items():
            if name not in seen_names:
                items.append(self._spec_to_summary(spec, full=full))
                seen_names.add(name)

        # Legacy (only if no v2 catalog wired)
        if self._catalog is None:
            for name, agent in self._legacy_agents.items():
                if name not in seen_names:
                    items.append({
                        "name": agent.spec.name,
                        "description": agent.spec.description,
                    })
                    seen_names.add(name)

        return items

    def _spec_to_summary(self, spec: AgentSpec, *, full: bool) -> dict:
        compact = {
            "name": spec.name,
            "kind": spec.kind,
            "status": spec.status,
            "description": spec.description,
            "runtime_profile": spec.runtime_profile,
            "lifecycle_mode": spec.lifecycle.mode,
            "model_slot": spec.model_slot,
        }
        if not full:
            return compact
        return {
            **compact,
            "system_prompt_preview": (spec.system_prompt or "")[:200],
            "lifecycle": {
                "mode": spec.lifecycle.mode,
                "ttl_seconds": spec.lifecycle.ttl_seconds,
                "max_runs": spec.lifecycle.max_runs,
            },
            "resource_limits": {
                "max_tool_calls": spec.resource_limits.max_tool_calls,
                "max_llm_calls": spec.resource_limits.max_llm_calls,
                "max_tokens": spec.resource_limits.max_tokens,
                "max_wall_time_seconds": spec.resource_limits.max_wall_time_seconds,
            },
            "created_reason": spec.created_reason,
        }

    def render_agent_summary_for_stateview(self) -> str:
        """Synchronous, in-memory summary for StateView injection.

        Format:
          可用 Agent:
          - name: kind, description (≤30c), lifecycle hint
        Rules:
          - status="active" only
          - builtin always shown
          - dynamic only active session+ephemeral, ≤5 (truncate)
          - never system_prompt
        """
        lines = ["可用 Agent:"]

        # Builtin: pulled from catalog snapshot at init() time. Since this is
        # a sync method and we can't read catalog inline, we instead use the
        # builtin spec factories directly.
        for spec_factory in (_builtin_researcher_spec, _builtin_coder_spec):
            spec = spec_factory()
            desc = (spec.description or "")[:30]
            lines.append(f"- {spec.name}: {spec.kind}, {desc}")

        # Dynamic: session + ephemeral (active only, ≤5 truncate)
        dynamic_specs = []
        for entry in self._session_agents.values():
            if entry.spec.status == "active":
                dynamic_specs.append(entry.spec)
        for spec in self._ephemeral_agents.values():
            if spec.status == "active":
                dynamic_specs.append(spec)

        truncated = False
        if len(dynamic_specs) > 5:
            dynamic_specs = dynamic_specs[:5]
            truncated = True
        for spec in dynamic_specs:
            desc = (spec.description or "")[:30]
            profile = spec.runtime_profile or ""
            lines.append(f"- {spec.name}: {spec.kind}, {profile}, {spec.lifecycle.mode}, {desc}")
        if truncated:
            lines.append("- ... 更多用 list_agents 查看")

        return "\n".join(lines)

    async def cleanup_expired_sessions(self) -> int:
        """Remove session entries whose TTL has elapsed. Returns count cleaned."""
        now = time.monotonic()
        expired = []
        for name, entry in self._session_agents.items():
            ttl = entry.spec.lifecycle.ttl_seconds
            if ttl is None:
                continue
            if now - entry.last_used_at > ttl:
                expired.append(name)
        for name in expired:
            del self._session_agents[name]
        return len(expired)

    # ── Legacy compatibility methods ──

    def register(self, name: str, agent: "BaseAgent"):
        self._legacy_agents[name] = agent
        logger.info("Agent '%s' 已注册 (legacy)", name)

    def get(self, name: str) -> "BaseAgent | None":
        return self._legacy_agents.get(name)

    def list_names(self) -> list[str]:
        return list(self._legacy_agents.keys())

    # list_specs() with no args is the legacy compact form (sync, dict list).
    # The v2 list_agents() is async. Existing tests use the sync form.
    def list_specs(self) -> list[dict]:
        return [
            {"name": a.spec.name, "description": a.spec.description}
            for a in self._legacy_agents.values()
        ]
