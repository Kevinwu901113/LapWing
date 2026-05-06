# Capability Phase 6A — Dynamic Agent Metadata Foundation

**Date:** 2026-05-02
**Status:** Implemented

---

## Purpose

Prepare dynamic agents to become capability-backed in later phases by adding safe metadata fields, validation, serialization, and policy linting — without changing runtime behavior.

## New AgentSpec Metadata Fields

All fields have safe defaults and are optional. Existing code that creates `AgentSpec` without these fields continues to work.

| Field | Type | Default | In spec_hash | Notes |
|-------|------|---------|--------------|-------|
| `bound_capabilities` | `list[str]` | `[]` | Yes | Capability IDs this agent is bound to |
| `memory_scope` | `str \| None` | `None` | Yes | Memory scope identifier |
| `risk_level` | `str` | `"low"` | Yes | `low` / `medium` / `high` |
| `eval_tasks` | `list[dict]` | `[]` | No | Operational task records |
| `success_count` | `int` | `0` | No | Runtime counter — excluded from hash |
| `failure_count` | `int` | `0` | No | Runtime counter — excluded from hash |
| `approval_state` | `str` | `"not_required"` | Yes | `not_required` / `pending` / `approved` / `rejected` |
| `allowed_delegation_depth` | `int` | `0` | Yes | Max delegation depth (0–3) |
| `capability_binding_mode` | `str` | `"metadata_only"` | Yes | `metadata_only` / `advisory` / `enforced` |

`runtime_profile` already existed on `AgentSpec` (type `str`, default `""`) and is included in `spec_hash`. No duplicate added.

## Validation Rules (AgentPolicy.validate_capability_metadata)

| Rule | Type | Behavior |
|------|------|----------|
| `risk_level` ∉ {low, medium, high} | Denial | Blocks |
| `approval_state` ∉ {not_required, pending, approved, rejected} | Denial | Blocks |
| `capability_binding_mode` ∉ {metadata_only, advisory, enforced} | Denial | Blocks |
| `capability_binding_mode == "enforced"` | Denial | Blocks in Phase 6A |
| `allowed_delegation_depth < 0` | Denial | Blocks |
| `allowed_delegation_depth > 3` | Denial | Blocks |
| `runtime_profile` not in known_profiles | Denial | Blocks (only when known_profiles provided) |
| `bound_capabilities` entry fails `[a-z][a-z0-9_]{2,63}` syntax | Denial | Blocks |
| `risk_level == "high"` and `approval_state != "approved"` | Denial | Blocks |
| `bound_capabilities` contains agent_admin/agent_create | Denial | Blocks self-referential escalation |
| `bound_capabilities` entry not in available_capabilities | Warning | Does not block |
| `approval_state == "rejected"` | Warning | Future-phase notice |

## Serialization / Backward Compatibility

- **Legacy specs** (without Phase 6A fields) load safely — dataclass defaults fill in missing fields.
- **New specs** serialize with `dataclasses.asdict()` → `json.dumps(default=str)`, same as before.
- **Deserialization** via `AgentCatalog._row_to_spec` passes extra keys through `**raw` to the constructor.
- **spec_hash** includes structural metadata fields (bound_capabilities, memory_scope, risk_level, approval_state, allowed_delegation_depth, capability_binding_mode).
- **spec_hash** excludes runtime counters (success_count, failure_count, eval_tasks) so hash remains stable across runs.
- **Unknown extra fields** still raise `TypeError` — unchanged behavior.
- **LegacyAgentSpec** unchanged and still usable.

## Policy Lint Behavior

- `validate_capability_metadata` is a **read-only** method. It does not mutate the spec, does not change save enforcement, and does not call CapabilityStore.
- Returns `CapabilityMetadataResult(allowed, warnings, denials)`.
- Accepts optional `available_capabilities` (list of strings) and `known_profiles` (list of strings) as plain data.
- No import from `src.capabilities` in any agent module.

## Non-Runtime Guarantee

Phase 6A does **not** change:
- Dynamic agent execution semantics
- ToolDispatcher behavior
- RuntimeProfile resolution
- save_agent enforcement gates
- Agent creation flow
- Agent lifecycle management
- Brain/TaskRuntime/StateView behavior

Phase 6A **only** adds:
- Optional metadata fields with safe defaults to AgentSpec
- Read-only policy lint method that returns a result
- Tests for metadata validation

## Future Phase Placeholders

- **Phase 6B:** Bind capabilities at runtime based on metadata; auto-load capabilities into agents.
- **Phase 6C:** Enforce capability_binding_mode; use eval_tasks for evidence-driven promotion.
- **Phase 6D:** Capability-aware dynamic agent save gates.
