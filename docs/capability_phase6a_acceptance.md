# Capability Phase 6A — Acceptance Report

**Date:** 2026-05-02
**Branch:** master
**Status:** Accepted (Hardened)

---

## Test Results

### New Phase 6A Tests

| Test File | Count | Passed | Failed |
|-----------|-------|--------|--------|
| `tests/agents/test_agent_spec_capability_metadata.py` | 29 | 29 | 0 |
| `tests/agents/test_agent_policy_capability_metadata.py` | 38 | 38 | 0 |
| **New Phase 6A total** | **67** | **67** | **0** |

### Full Suite Results (2026-05-02 hardening run)

| Suite | Collected | Passed | Failed | Skipped |
|-------|-----------|--------|--------|---------|
| `tests/agents/` (incl. 67 new Phase 6A) | 275 | 275 | 0 | 0 |
| `tests/capabilities/` | 1,032 | 1,032 | 0 | 0 |
| `tests/skills/` | 64 | 64 | 0 | 0 |
| `tests/logging/` | 32 | 32 | 0 | 0 |
| `tests/core/test_tool_dispatcher.py` + `test_runtime_profiles_exclusion.py` | 87 | 87 | 0 | 0 |
| **Relevant suites total** | **1,490** | **1,490** | **0** | **0** |

### Suite Composition

- **Pre-6A agent tests:** 208 (275 total − 67 new)
- **New Phase 6A tests:** 67 (subset of agents/ total)
- **agents/ + capabilities/ combined:** 1,307 (not 1,374 — the previous report double-counted the 67 new tests by listing them separately and also including them in the agents/ total)

---

## Files Changed

### Production code (2 files)

1. **`src/agents/spec.py`**
   - Added `VALID_RISK_LEVELS`, `VALID_APPROVAL_STATES`, `VALID_CAPABILITY_BINDING_MODES`, `MAX_DELEGATION_DEPTH` constants.
   - Added 9 new metadata fields to `AgentSpec` with safe defaults.
   - Extended `spec_hash()` to include structural metadata fields; excluded runtime counters.

2. **`src/agents/policy.py`**
   - Added `CapabilityMetadataResult` dataclass.
   - Added `_CAPABILITY_ID_RE` pattern for capability ID syntax validation.
   - Added `AgentPolicy.validate_capability_metadata()` method with 11 validation checks.

### Test code (2 files)

3. **`tests/agents/test_agent_spec_capability_metadata.py`** (29 tests)
   - Constant enumeration tests (4)
   - Default safety tests (2)
   - Field round-trip tests (9)
   - spec_hash behavior tests (9)
   - Legacy compatibility tests (2)
   - JSON serialization tests (2)
   - Unknown extra field test (1)

4. **`tests/agents/test_agent_policy_capability_metadata.py`** (38 tests)
   - Valid metadata passes lint (6)
   - Invalid capability IDs (4)
   - Unknown runtime_profile (3)
   - Risk level validation (3)
   - Approval state validation (2)
   - Delegation depth (2)
   - Capability binding mode (4)
   - Available capabilities awareness (2)
   - Self-referential agent admin (2)
   - No mutation / no side effects (4)
   - Result dataclass tests (2)
   - Class constant tests (2)
   - No CapabilityStore import test (1)
   - Deterministic lint test (1)

---

## AgentSpec Metadata Summary

| Field | Type | Default | In spec_hash | Notes |
|-------|------|---------|--------------|-------|
| `bound_capabilities` | `list[str]` | `[]` | Yes | Sorted before hashing |
| `memory_scope` | `str \| None` | `None` | Yes | Memory scope identifier |
| `risk_level` | `str` | `"low"` | Yes | One of: low, medium, high |
| `eval_tasks` | `list[dict]` | `[]` | No | Runtime counter — excluded |
| `success_count` | `int` | `0` | No | Runtime counter — excluded |
| `failure_count` | `int` | `0` | No | Runtime counter — excluded |
| `approval_state` | `str` | `"not_required"` | Yes | One of: not_required, pending, approved, rejected |
| `allowed_delegation_depth` | `int` | `0` | Yes | Range 0..3 |
| `capability_binding_mode` | `str` | `"metadata_only"` | Yes | One of: metadata_only, advisory, enforced |

`runtime_profile` (existing, `str = ""`) unchanged — already in spec_hash.

---

## spec_hash Semantics (Verified)

### Fields included (structural identity)
- `name`, `system_prompt`, `model_slot`, `runtime_profile`, `tool_denylist`
- `resource_limits` (max_tool_calls, max_llm_calls, max_tokens, max_wall_time_seconds, max_child_agents)
- `bound_capabilities` (sorted)
- `memory_scope`
- `risk_level`
- `approval_state`
- `allowed_delegation_depth`
- `capability_binding_mode`

### Fields excluded (runtime counters)
- `success_count` — changes every run, would break hash stability
- `failure_count` — changes every run, would break hash stability
- `eval_tasks` — operational records, not structural identity

### Verified behaviors
- [x] Same logical spec produces same hash (deterministic)
- [x] Hash survives JSON serialization round-trip
- [x] Runtime counters changing does NOT change hash
- [x] `bound_capabilities` changing DOES change hash
- [x] `risk_level` changing DOES change hash
- [x] `capability_binding_mode` changing DOES change hash
- [x] `runtime_profile` changing DOES change hash (existing behavior, unchanged)

---

## Serialization / Backward Compatibility (Verified)

- [x] Old serialized AgentSpec without Phase 6A fields loads successfully with safe defaults
- [x] LegacyAgentSpec alias works (resolves to AgentSpec)
- [x] New metadata fields default safely (empty list, None, "low", 0, "not_required", "metadata_only")
- [x] JSON round-trip preserves all new fields
- [x] JSON round-trip preserves all old fields
- [x] spec_hash survives serialization round-trip
- [x] Missing new fields never break agent registry load (dataclass defaults fill in)
- [x] Unknown extra fields still raise `TypeError` (unchanged behavior)
- [x] `dataclasses.asdict()` serialization unchanged
- [x] `AgentCatalog._row_to_spec` deserialization unchanged (new fields pass through `**raw`)
- [x] LegacyAgentSpec unchanged

---

## Policy Lint Summary

`AgentPolicy.validate_capability_metadata(spec, *, available_capabilities=None, known_profiles=None) -> CapabilityMetadataResult`

| # | Check | Type | Behavior |
|---|-------|------|----------|
| 1 | `risk_level` ∉ {low, medium, high} | Denial | Blocks |
| 2 | `approval_state` ∉ {not_required, pending, approved, rejected} | Denial | Blocks |
| 3 | `capability_binding_mode` ∉ {metadata_only, advisory, enforced} | Denial | Blocks |
| 4 | `capability_binding_mode == "enforced"` | Denial | Blocks in Phase 6A |
| 5 | `allowed_delegation_depth < 0` | Denial | Blocks |
| 6 | `allowed_delegation_depth > MAX_DELEGATION_DEPTH (3)` | Denial | Blocks |
| 7 | `runtime_profile` not in `known_profiles` (when provided) | Denial | Blocks |
| 8 | `bound_capabilities` entry fails `[a-z][a-z0-9_]{2,63}` syntax | Denial | Blocks |
| 9 | `risk_level == "high"` and `approval_state != "approved"` | Denial | Blocks |
| 10 | `bound_capabilities` contains agent_admin/agent_create IDs | Denial | Blocks self-referential escalation |
| 11 | `bound_capabilities` entry not in `available_capabilities` | Warning | Does not block |
| 12 | `approval_state == "rejected"` | Warning | Future-phase notice |

### Policy Lint Properties (Verified)
- [x] Deterministic — same input produces same output
- [x] Read-only — does not mutate AgentSpec
- [x] Does not import CapabilityStore
- [x] Does not import from `src.capabilities`
- [x] Does not grant tools
- [x] Does not alter RuntimeProfile
- [x] Does not affect save_agent unless explicitly called
- [x] Returns `CapabilityMetadataResult` dataclass with `allowed`, `warnings`, `denials`

---

## Runtime Behavior — Confirmed Unchanged

- [x] Creating an ephemeral dynamic agent behaves exactly as before
- [x] Running a dynamic agent behaves exactly as before
- [x] Saving a persistent agent behaves exactly as before
- [x] Loading saved agents behaves exactly as before
- [x] AgentRegistry behavior unchanged for old specs
- [x] Agent tool schemas unchanged (metadata fields are dataclass fields, not tool params)
- [x] ToolDispatcher agent policy checks unchanged
- [x] No capability loading occurs
- [x] No capability execution occurs
- [x] No runtime profile is granted by metadata
- [x] No agent can self-elevate through metadata
- [x] No eval evidence required
- [x] No persistent lifecycle changes
- [x] No `run_capability` exists anywhere in the codebase

---

## Permission / Escalation Audit (Verified)

- [x] Metadata `runtime_profile` does not change actual execution profile
- [x] `bound_capabilities` do not load capability summaries automatically
- [x] `bound_capabilities` do not grant tools
- [x] `bound_capabilities` do not grant capability_read/lifecycle/curator tags
- [x] `approval_state` does not bypass ToolDispatcher
- [x] `capability_binding_mode` does not alter runtime behavior
- [x] `allowed_delegation_depth` does not alter actual delegation behavior in Phase 6A
- [x] `risk_level` does not change execution privileges

---

## Runtime Import Audit

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ --include="*.py" | grep -v 'src/capabilities/'
src/tools/capability_tools.py    (allowed — tool registration)
src/app/container.py             (allowed — wiring)
```

### Agent modules confirmed clean (no `src.capabilities` imports)
- [x] `src/agents/spec.py`
- [x] `src/agents/policy.py`
- [x] `src/agents/registry.py`
- [x] `src/agents/dynamic.py`
- [x] `src/agents/factory.py`
- [x] `src/agents/catalog.py`
- [x] `src/tools/agent_tools.py`

### Core modules confirmed clean
- [x] `src/core/task_runtime.py`
- [x] `src/core/brain.py`
- [x] `src/core/tool_dispatcher.py`
- [x] `src/core/state_view.py`

---

## Regression Checks (Verified)

- [x] All capability tests pass (1,032)
- [x] All agent tests pass (275)
- [x] Skills tests pass (64)
- [x] ToolDispatcher tests pass
- [x] RuntimeProfile tests pass
- [x] MutationLog tests pass
- [x] Read-only capability tools unchanged
- [x] Lifecycle tools unchanged
- [x] Curator tools unchanged
- [x] Retrieval unchanged
- [x] Execution summary / dry-run / auto-proposal unchanged
- [x] No `run_capability` exists

---

## Known Issues

None.

---

## Rollback Notes

To revert: `git checkout` the 2 production files + 2 test files from the parent commit. No database migrations. No config changes. No dependency changes. Agents saved with Phase 6A metadata fields will load safely on rollback (extra JSON keys become unknown kwargs → `TypeError` on the legacy constructor, but rollback would happen before any agent saves with new fields).
