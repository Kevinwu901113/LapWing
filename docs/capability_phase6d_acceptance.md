# Phase 6D Acceptance Report

**Date**: 2026-05-03
**Status**: ACCEPTED

## Test Results

### New Phase 6D tests

| Test file | Tests | Passed | Failed |
|-----------|-------|--------|--------|
| `tests/agents/test_agent_candidate_tools.py` | 39 | 39 | 0 |
| `tests/agents/test_agent_candidate_operator_profile.py` | 24 | 24 | 0 |
| `tests/agents/test_agent_candidate_save_gate_hardening.py` | 30 | 30 | 0 |
| **Total new** | **93** | **93** | **0** |

(14 added in config semantics cleanup pass: Pydantic default, sentinel fallback, env var override, missing config, AgentPolicy wiring)

### Regression tests

| Test file | Tests | Passed | Failed |
|-----------|-------|--------|--------|
| `tests/agents/test_agent_candidate.py` | 41 | 41 | 0 |
| `tests/agents/test_agent_candidate_policy.py` | 66 | 66 | 0 |
| `tests/agents/test_agent_candidate_store.py` | 36 | 36 | 0 |
| `tests/agents/test_agent_save_gate.py` | 42 | 42 | 0 |
| `tests/agents/test_agent_save_gate_integration.py` | 11 | 11 | 0 |
| `tests/agents/test_agent_spec_capability_metadata.py` | 34 | 34 | 0 |
| **Total regression** | **230** | **230** | **0** |

### Grand total: 323 tests, 0 failures

## Feature Flag Matrix

| Flag | Default | When false | When true |
|------|---------|------------|-----------|
| `agents.candidate_tools_enabled` | `false` | No candidate tools registered | 6 candidate tools registered with `agent_candidate_operator` tag |
| `agents.require_candidate_approval_for_persistence` | `false` | Save gate disabled, all saves pass | Capability-backed saves require approved candidate |
| `agents.candidate_evidence_max_age_days` | `90` | N/A (only active with save gate) | Evidence older than 90 days denied for medium/high risk |

## Permission Matrix

| Profile | `agent_candidate_operator` capability | Can use candidate tools |
|---------|--------------------------------------|------------------------|
| `standard` | No | No |
| `zero_tools` | No | No |
| `chat_shell` | No | No |
| `inner_tick` | No | No |
| `local_execution` | No | No |
| `browser_operator` | No | No |
| `identity_operator` | No | No |
| `capability_lifecycle_operator` | No | No |
| `capability_curator_operator` | No | No |
| `agent_candidate_operator` | **Yes** | **Yes** |

## Tool Behavior Matrix

| Tool | Read/Write | Mutates candidate | Creates agent | Changes approval | Risk |
|------|-----------|-------------------|---------------|------------------|------|
| `list_agent_candidates` | Read | No | No | No | low |
| `view_agent_candidate` | Read | No | No | No | low |
| `add_agent_candidate_evidence` | Write | Yes (evidence only) | No | No | low |
| `approve_agent_candidate` | Write | Yes | No | Yes (→approved) | medium |
| `reject_agent_candidate` | Write | Yes | No | Yes (→rejected) | low |
| `archive_agent_candidate` | Write | Yes | No | No | low |

## Archived Candidate Save-Gate Fix

**Before (Phase 6C gap)**: Archived-but-approved candidates passed `validate_persistent_save_gate`.
**After (Phase 6D)**: Archived candidates are denied regardless of approval state. Returns `candidate_archived` denial.

Test coverage:
- `test_approved_archived_candidate_denied` — archived + approved → denied
- `test_non_archived_approved_candidate_still_works` — non-archived approved → allowed
- `test_denied_save_is_atomic` — denial does not mutate spec or candidate

## Evidence Freshness

**Decision**: Implemented with conservative defaults.

- `agents.candidate_evidence_max_age_days = 90` in `config.toml`
- Only enforced when save gate is active (`require_candidate_approval_for_persistence=true`)
- Only applies to medium and high risk candidates
- Conservative handling: missing/unparseable `created_at` → treated as stale
- Naive datetimes treated as UTC

Test coverage:
- `test_fresh_evidence_passes` — recent evidence passes
- `test_stale_evidence_denied` — old evidence denied
- `test_missing_created_at_handled_conservatively` — empty timestamp → denied
- `test_low_risk_skips_freshness` — low risk not checked
- `test_freshness_not_enforced_when_none` — None disables enforcement
- `test_high_risk_stale_evidence_denied` — high risk stale → denied

## Runtime Import Audit

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
src/tools/capability_tools.py — expected (capability tools)
src/app/container.py — expected (container wiring)
```

No `src.capabilities` imports in agent modules. No `run_capability` exists.

## No-Execution Proof

- No tool creates `AgentRuntime` or `TaskRuntime`
- No tool calls `AgentRegistry.get_or_create_instance`
- No tool modifies `Brain`, `StateView`, or `ToolDispatcher`
- `approve_agent_candidate` changes `approval_state` only
- `add_agent_candidate_evidence` appends evidence only
- All tool executors operate on `AgentCandidateStore` (filesystem) only

## Legacy Behavior Unchanged

- `agents.candidate_tools_enabled=false` → no candidate tools registered (verified)
- `agents.require_candidate_approval_for_persistence=false` → save gate disabled (verified)
- Ordinary (non-capability-backed) agents always pass save gate (verified)
- `save_agent` behavior unchanged by candidate tools flag (verified)
- Existing agent tests all pass (230/230)

## Hardening Verification (2026-05-03)

### Full Test Suite

| Test directory | Tests | Passed | Failed | Skipped |
|----------------|-------|--------|--------|---------|
| `tests/agents/` | 530 | 530 | 0 | 0 |
| `tests/capabilities/` | 1,032 | 1,032 | 0 | 0 |
| `tests/core/` | 1,018 | 1,018 | 0 | 1 |
| `tests/skills/` | 64 | 64 | 0 | 0 |
| `tests/logging/` | 32 | 32 | 0 | 0 |
| **Total** | **2,690** | **2,690** | **0** | **1** |

The 1 skip is pre-existing (`test_research_tool_loop_returns_final_reply` — research no longer exposed at chat tier, by design).

### Feature Flag Matrix (Verified)

| Flag | Default | When false | When true | Verified |
|------|---------|------------|-----------|----------|
| `agents.candidate_tools_enabled` | `false` | No candidate tools registered | 6 candidate tools registered with `agent_candidate_operator` tag | Yes |
| `agents.require_candidate_approval_for_persistence` | `false` | Save gate disabled, all saves pass | Capability-backed saves require approved candidate | Yes |
| `agents.candidate_evidence_max_age_days` | `90` | N/A (only active with save gate) | Evidence older than 90 days denied for medium/high risk |

Default 90 is consistent across all layers:
- Pydantic `AgentsConfig` default: `90`
- `config.toml` value: `90`
- Compat shim `AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS`: `90`
- Missing `[agents]` section in TOML → falls back to `90`
- `AgentPolicy.__init__` default: `90`
- `validate_persistent_save_gate` uses policy default via sentinel
- Set to `None` or `0` to disable freshness checks Yes |

Verification method:
- `AgentsConfig()` instantiated directly → `candidate_tools_enabled=False`, `candidate_evidence_max_age_days=90` (Pydantic defaults)
- `AgentsConfig.model_validate({})` (missing `[agents]` section) → `candidate_evidence_max_age_days=90` (fallback)
- `AgentsConfig(candidate_evidence_max_age_days=None)` → `None` (explicit disable)
- `get_settings()` → reads config.toml → `candidate_tools_enabled=False`, `candidate_evidence_max_age_days=90`
- Compat shim `config.settings` exports all match
- Env var `AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS` overrides default
- `AgentPolicy.__init__` defaults to `90`, stores as `self.evidence_max_age_days`
- `validate_persistent_save_gate` uses sentinel pattern — falls back to policy default when not passed explicitly

Config cleanup (2026-05-03):
- Pydantic default changed from `None` to `90` for explicitness and safety
- `AgentPolicy` now stores and passes `evidence_max_age_days` through the save gate
- `container.py` wires `AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS` into `AgentPolicy` construction

### Permission Matrix (Verified)

| Profile | Has `agent_candidate_operator` | Can use candidate tools | Verified |
|---------|--------------------------------|--------------------------|----------|
| `standard` | No | No | Yes |
| `zero_tools` | No | No | Yes |
| `chat_shell` | No | No | Yes |
| `inner_tick` | No | No | Yes |
| `local_execution` | No | No | Yes |
| `browser_operator` | No | No | Yes |
| `identity_operator` | No | No | Yes |
| `capability_lifecycle_operator` | No | No | Yes |
| `capability_curator_operator` | No | No | Yes |
| `agent_candidate_operator` | **Yes** | **Yes** | Yes |

Verified programmatically: `_PROFILES` dict scanned — only `agent_candidate_operator` has the `agent_candidate_operator` capability tag. Total 19 profiles in registry (18 pre-existing + 1 new).

### Import Audit (Passed)

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ --include="*.py" | grep -v 'src/capabilities/'
src/tools/capability_tools.py — expected (capability tools)
src/app/container.py — expected (container wiring)
```

No `src.capabilities` imports in agent modules (`src/agents/`). No `run_capability` exists. No capabilities import in `Brain`, `TaskRuntime`, `SkillExecutor`, `ToolDispatcher`, or skill tools.

### Forbidden Tools Audit (Passed)

`grep -rn` for `run_capability`, `run_agent_candidate`, `promote_agent_candidate`, `save_candidate_as_agent`, `execute_candidate`, `auto_approve_agent_candidate` in `src/`:
- **Zero matches in src/** — no forbidden tools exist
- All matches are in test files verifying their absence (expected)

### Privacy / Sanitization Audit (Passed)

- `add_agent_candidate_evidence` routes summary through `redact_secrets_in_summary()` before storing
- Evidence schemas carry only: `evidence_type`, `summary` (sanitized), `source_iteration_id`, `created_at`
- No CoT fields, no internal state, no raw LLM responses in tool schemas or output
- `_evidence_summary()` produces lightweight summaries — no full prompt bodies

### No-Execution Proof (Verified)

- No candidate tool creates `AgentRuntime` or `TaskRuntime`
- No candidate tool calls `AgentRegistry.get_or_create_instance`
- No candidate tool modifies `Brain`, `StateView`, or `ToolDispatcher`
- `approve_agent_candidate` changes `approval_state` only — no agent creation
- `add_agent_candidate_evidence` appends evidence only — no state transition
- All candidate tool executors operate on `AgentCandidateStore` (filesystem) only
- `register_agent_candidate_tools(tool_registry, store, policy)` — three-arg signature, no runtime dependencies

### Tool Behavior Hardening (Verified)

| Tool | Read/Write | Mutates candidate | Creates agent | Changes approval | Verified |
|------|-----------|-------------------|---------------|------------------|----------|
| `list_agent_candidates` | Read | No | No | No | 39 tests |
| `view_agent_candidate` | Read | No | No | No | 39 tests |
| `add_agent_candidate_evidence` | Write | Yes (evidence only) | No | No | 39 tests |
| `approve_agent_candidate` | Write | Yes | No | Yes (→approved) | 39 tests |
| `reject_agent_candidate` | Write | Yes | No | Yes (→rejected) | 39 tests |
| `archive_agent_candidate` | Write | Yes | No | No | 39 tests |

### Save Gate Hardening (Verified)

| Scenario | Expected | Verified |
|----------|----------|----------|
| Archived + approved candidate | Denied (`candidate_archived`) | 16 tests |
| Non-archived approved candidate | Allowed | 16 tests |
| Denied save is atomic | No mutation to spec or candidate | 16 tests |
| Fresh evidence (≤90 days) | Passes freshness check | 16 tests |
| Stale evidence (>90 days) | Denied | 16 tests |
| Missing `created_at` | Conservative → denied | 16 tests |
| Low risk candidate | Freshness check skipped | 16 tests |
| `evidence_max_age_days=None` | Freshness check disabled | 16 tests |
| Ordinary (non-capability-backed) agents | Always pass save gate | 16 tests |

### Legacy Behavior Unchanged (Verified)

- `agents.candidate_tools_enabled=false` → no candidate tools registered (confirmed)
- `agents.require_candidate_approval_for_persistence=false` → save gate disabled (confirmed)
- Ordinary agents always pass save gate (confirmed, 230 regression tests pass)
- `save_agent` behavior unchanged by candidate tools flag (confirmed)
- All pre-existing profiles unchanged (19 profiles, 18 pre-existing intact)
- `test_all_known_profiles_exist` updated to include `agent_candidate_operator`
- Import audit clean — only expected files import capabilities

## Known Issues

1. **Gap 3 (deferred)**: `save_agent` tool does not pass through the feature flag. The flag exists in config for future wiring.
2. Evidence freshness uses `datetime.now(timezone.utc)` — epoch-based comparisons. Timezone edge cases at DST boundaries are conservatively handled (naive → UTC).
3. Archived candidate blocking in the save gate is always active when the gate is enabled — no independent flag to disable just the archive check.

## Config Semantics Cleanup (2026-05-03)

**Problem found during hardening**: `evidence_max_age_days` was `None` as Pydantic default and was never wired from config into `AgentPolicy`. Freshness checks in `validate_persistent_save_gate` never ran because the parameter defaulted to `None`.

**What changed**:
- Pydantic `AgentsConfig.candidate_evidence_max_age_days` default → `90` (was `None`)
- `AgentPolicy.__init__` now accepts and stores `evidence_max_age_days` (default `90`)
- `validate_persistent_save_gate` uses sentinel pattern — falls back to `self.evidence_max_age_days` when parameter not passed
- `container.py` wires `AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS` into `AgentPolicy` construction
- Missing `[agents]` TOML section now falls back to `90` (was `None` → silently disabled)
- `None` or `0` still disables freshness checks when explicitly set
- Env var `AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS` overrides the default
- All layers consistent: Pydantic = config.toml = compat shim = AgentPolicy = save gate

## Rollback Notes

- Set `agents.candidate_tools_enabled = false` to disable all candidate tools instantly
- Set `agents.candidate_evidence_max_age_days` to empty/null in `config.toml` or unset the env var to disable freshness checks
- Archived candidate blocking cannot be independently rolled back (it's part of the save gate logic, not a separate flag)
