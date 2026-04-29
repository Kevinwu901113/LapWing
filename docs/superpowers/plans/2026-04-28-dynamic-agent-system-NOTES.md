# Dynamic Agent System — Implementation Notes

**Branch:** `feat/dynamic-agent-system`
**Plan:** [2026-04-28-dynamic-agent-system.md](./2026-04-28-dynamic-agent-system.md)
**Blueprint:** [2026-04-28-dynamic-agent-system-blueprint.md](./2026-04-28-dynamic-agent-system-blueprint.md)

## What landed

Brain can now create, delegate to, save, and destroy dynamic agents at runtime. Five new tools are wired into `chat_extended` (`delegate_to_agent`, `list_agents`) and `task_execution` (the previous two plus `create_agent`, `destroy_agent`, `save_agent`). Builtin Researcher and Coder are migrated from instance registration to spec-driven catalog rows; the runtime instance is materialised fresh per delegation by `AgentFactory`.

Permissions are spec-driven via existing `RuntimeProfile` plus a hard-coded `DYNAMIC_AGENT_DENYLIST`. `AgentPolicy` enforces creation rules with a NIM-based semantic lint that fail-closes on anything other than `verdict="safe"`. A turn-shared `BudgetLedger` caps `llm_calls` / `tool_calls` / `tokens` / `wall_time` / `delegation_depth` across Brain plus every delegated agent.

Legacy `delegate_to_researcher` / `delegate_to_coder` are now compatibility shims forwarding to `delegate_to_agent` and are hidden from every `RuntimeProfile.tool_names`. They stay registered for any persisted plans / older clients.

## File map

**New modules**

| File | Purpose |
|------|---------|
| `src/agents/spec.py` | `AgentSpec`, lifecycle / resource limits, `DYNAMIC_AGENT_DENYLIST` constants |
| `src/agents/catalog.py` | SQLite-backed `AgentCatalog` (init/save/get/list/archive/delete/count) |
| `src/agents/factory.py` | `AgentFactory.create(spec)` — dispatches builtin vs dynamic, merges denylist into profile |
| `src/agents/dynamic.py` | `DynamicAgent` — overrides `_execute_tool` to enforce denylist at runtime |
| `src/agents/policy.py` | `AgentPolicy` validate_create / validate_save / validate_tool_access + NIM lint |
| `src/agents/budget.py` | `BudgetLedger` + `BudgetExhausted` |
| `src/agents/builtin_specs.py` | Inline `AgentSpec` factories for `researcher` and `coder` |

**Modified**

| File | Change |
|------|--------|
| `src/agents/types.py` | Legacy `AgentSpec` aliased as `LegacyAgentSpec`; `AgentResult.budget_status` field added |
| `src/agents/registry.py` | Refactored to v2 facade; legacy `register/get/list_names/list_specs` preserved |
| `src/agents/base.py` | `BaseAgent.execute()` charges per-turn `BudgetLedger` and emits `AGENT_BUDGET_EXHAUSTED` |
| `src/tools/agent_tools.py` | 5 new executors + schemas; legacy delegates are now thin shims |
| `src/core/runtime_profiles.py` | `chat_extended` swaps research/browse for `delegate_to_agent`; `task_execution` excludes legacy delegates; `compose_proactive` swaps too |
| `src/core/authority_gate.py` | Owner-level auth registered for all 5 new tools |
| `src/core/state_view.py` | `StateView.agent_summary` field |
| `src/core/state_view_builder.py` | Optional `agent_registry` ctor arg + `_build_agent_summary()` helper |
| `src/core/state_serializer.py` | Renders `agent_summary` in the runtime-state block before commitments |
| `src/core/brain.py` | `_build_services()` now creates a fresh per-turn `BudgetLedger` from `settings.budget` |
| `src/app/container.py` | Wires Catalog + Factory + Policy + v2 Registry on boot; schedules session cleanup |
| `src/config/settings.py` | New `AgentTeamDynamicConfig` + `BudgetConfig` |
| `src/logging/state_mutation_log.py` | 5 new `MutationType` members (`AGENT_CREATED/SAVED/DESTROYED/SPEC_UPDATED/BUDGET_EXHAUSTED`) |
| `config.toml` / `config.example.toml` | `[agent_team.dynamic]` and `[budget]` sections |

**Deprecated (kept for compatibility)**

- `delegate_to_researcher` / `delegate_to_coder` — registered as ToolSpecs but excluded from every `RuntimeProfile`. Internally forward to `delegate_to_agent_executor`. Full deletion is explicitly out of scope per blueprint §16.

## Acceptance test mapping (T-01 … T-14)

| ID | Test file |
|----|-----------|
| T-01 | `tests/agents/test_e2e_dynamic.py::test_t01_delegate_to_agent_builtin_{researcher,coder}` |
| T-02 | `tests/agents/test_e2e_shim.py::test_t02_{researcher,coder}_shim_matches_new_path` |
| T-03 | `tests/core/test_stateview_agent_summary.py::test_serializer_renders_agent_summary_in_runtime_state_block` |
| T-04 | `tests/agents/test_policy.py::test_validate_create_rejects_{unknown_profile,unknown_model_slot,persistent_lifecycle}` |
| T-05 | `tests/agents/test_dynamic_agent.py::test_t05_send_message_blocked_at_runtime` |
| T-06 | `tests/agents/test_dynamic_agent.py::test_t06_runtime_denylist_with_empty_spec_tool_denylist` |
| T-07 | `tests/agents/test_policy.py::test_lint_fail_closed_on_{unsafe,uncertain,exception}` |
| T-08 | `tests/agents/test_budget.py::test_{llm_call,tool_call,token,wall_time,delegation_depth}_limit` + `tests/agents/test_base_agent.py::test_t08_{llm_call,tool_call}_budget_exhausted` |
| T-09 | `tests/agents/test_registry_v2.py::test_t09_session_agent_fresh_runtime` |
| T-10 | `tests/agents/test_registry_v2.py::test_t10_save_agent_persists_spec_only` |
| T-11 | `tests/agents/test_policy.py::test_validate_save_{rejects_unrun_agent,rejects_when_max_persistent_reached,rejects_duplicate_name}` |
| T-12 | `tests/core/test_runtime_profiles_exclusion.py::TestProfileExclusivity::test_{chat_extended_has_delegate_to_agent_no_research,task_execution_has_all_five_dynamic_agent_tools,chat_minimal_has_no_agent_tools}` |
| T-13 | `tests/agents/test_e2e_dynamic.py::test_t13_full_lifecycle_emits_audit_chain` |
| T-14 | `tests/agents/test_dynamic_agent.py::test_t14_every_denylist_tool_blocked_at_runtime` (parametrized over all 23 denylist members) |

## Migration notes

- **Brain LLM**: New tool surface is `delegate_to_agent(agent_name, task, context?, expected_output?)`. The legacy `delegate_to_researcher` / `delegate_to_coder` calls still work via shim if a persisted plan still references them, but the LLM never sees them anymore.
- **chat_extended profile** no longer exposes `research` / `browse` — chat queries that need research must go through `delegate_to_agent(agent_name="researcher", ...)`. One previously-passing test (`test_brain_tools.py::test_research_tool_loop_returns_final_reply`) was marked skip with a reason citing this change; the equivalent flow is covered by `tests/tools/test_agent_tools_v2.py`.
- **VitalGuard**: dynamic agents get a `/tmp/lapwing/agents/{spec.id}/` workspace, but actual cwd-confinement enforcement still relies on the existing VitalGuard path-protection (no new guard code in this PR per blueprint §12). If a future Brain LLM tries to write outside its agent workspace, that surface needs another check.

## Out-of-scope (per blueprint §16)

- Full deletion of `delegate_to_researcher` / `delegate_to_coder` (kept as shims here).
- AgentPool / instance pooling.
- CapabilityGrant permission model.
- Formal spec versioning beyond `version: int`.
- Parallel `delegate_many`.
- DAG / workflow engine.
- Dynamic creation of new RuntimeProfile or model_slot at runtime.
- Long-term memory namespace for persistent agents.

## Pending follow-ups

Tracked from code-review feedback during implementation:

1. **Policy `validate_tool_access` exception handling** — currently swallows all `getattr` errors silently. Add a `logger.exception(...)` so future profile-name typos are observable. (Important, non-blocking.)
2. **Process-global side-tables in `agent_tools.py`** (`_ephemeral_run_counts`, `_completed_delegations`) — keyed by name only; would conflate state across concurrent chats if names collide. Consider moving onto the registry. (Important, non-blocking.)
3. **`MAX_SESSION_AGENTS` is unused** — defined in `AgentPolicy` but no enforcement at create time. Wire into `validate_create` when `lifecycle == "session"`.
4. **`BudgetSnapshot` should be `frozen=True`** — currently mutable; tests imply immutability.
5. **`BudgetExhausted.dimension="tokens"`** vs constructor kwarg `max_total_tokens` — minor naming inconsistency.

None of these block downstream callers.
