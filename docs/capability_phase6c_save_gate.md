# Capability Phase 6C — Save Gate for Persistent Dynamic Agents

**Date:** 2026-05-02
**Branch:** master

## Overview

Phase 6C introduces an optional, feature-gated approval/evidence gate for saving capability-backed persistent dynamic agents.

Default behavior is unchanged. The gate only activates when:
1. The feature flag `agents.require_candidate_approval_for_persistence` is `true`, AND
2. The agent spec is capability-backed (see definition below).

## Feature Flag

| Config Path | Type | Default | Env Var |
|---|---|---|---|
| `agents.require_candidate_approval_for_persistence` | `bool` | `false` | `AGENTS_REQUIRE_CANDIDATE_APPROVAL_FOR_PERSISTENCE` |

Set in `config.toml`:
```toml
[agents]
require_candidate_approval_for_persistence = false
```

Or via environment variable:
```bash
export AGENTS_REQUIRE_CANDIDATE_APPROVAL_FOR_PERSISTENCE=true
```

## When the Gate Applies

The gate only applies when ALL of:
1. Flag is enabled (`require_candidate_approval_for_persistence = true`)
2. Spec is capability-backed (see `is_capability_backed_agent` below)
3. Save target is persistent (not ephemeral/session)

### Capability-Backed Agent Definition

`is_capability_backed_agent(spec) -> bool` returns `True` when ANY of:
- `bound_capabilities` non-empty
- `capability_binding_mode != "metadata_only"`
- `risk_level in {"medium", "high"}`
- `eval_tasks` non-empty
- `approval_state != "not_required"`
- `allowed_delegation_depth > 0`

Returns `False` for old specs and ordinary metadata-only low-risk agents.

## Behavior Matrix

| Flag | Spec Type | Candidate | Result |
|---|---|---|---|
| `false` | Any | Any | Save as before (no gate) |
| `true` | Not capability-backed | Not required | Save as before |
| `true` | Capability-backed | Missing/None | Denied: `missing_candidate` |
| `true` | Capability-backed | Pending | Denied: `candidate_not_approved` |
| `true` | Capability-backed | Rejected | Denied: `candidate_not_approved` |
| `true` | Capability-backed | Approved + matching | Check evidence, then save |
| `true` | Capability-backed | Approved + mismatched hash | Denied: `spec_hash_mismatch` |
| `true` | Capability-backed | Approved + mismatched risk | Denied: `risk_level_mismatch` |

## Candidate Matching Rules

For a gated save to succeed, ALL of the following must hold:
1. `candidate_id` is provided
2. Candidate exists in `AgentCandidateStore`
3. `candidate.approval_state == "approved"`
4. `candidate.proposed_spec.spec_hash() == spec.spec_hash()`
5. `candidate.risk_level == spec.risk_level`
6. `validate_agent_candidate` passes
7. `validate_capability_metadata` passes

## Evidence Sufficiency Rules

| Risk Level | Minimum Evidence |
|---|---|
| `low` | Approved candidate (no evidence minimum) |
| `medium` | At least 1 passed evidence item |
| `high` | At least 1 passed `manual_review` + 1 passed `policy_lint` |

Only `passed=True` evidence counts. Failed evidence is ignored.

## Denial Behavior

All denials:
- Return `AgentPolicyViolation(save_gate_denied, ...)` with structured `details`
- Do NOT write a persistent agent file
- Do NOT modify the registry index/catalog
- Do NOT mutate the candidate
- Do NOT remove the agent from session/ephemeral dicts
- Surface clean error messages (no stack traces in user-facing result)

## Atomicity Guarantees

- Denied save: no persistent agent file written
- Denied save: no catalog mutation
- Denied save: candidate file unchanged
- Denied save: session/ephemeral state unchanged
- Successful save: identical path to pre-Phase 6C behavior

## Legacy Behavior Guarantee

- Default flag is `false`
- When flag is `false`, `save_agent` behavior is byte-for-byte identical to pre-Phase 6C
- Non-capability-backed agents are never gated, even when flag is `true`
- Existing tests continue to pass without modification

## Implementation

| Module | Change |
|---|---|
| `src/agents/spec.py` | Added `is_capability_backed_agent()` helper |
| `src/agents/policy.py` | Added `SaveGateResult`, `validate_persistent_save_gate()` |
| `src/agents/registry.py` | Extended `save_agent()` with `candidate_id`, `candidate_store`, `require_candidate_approval` params |
| `src/config/settings.py` | Added `AgentsConfig` with `require_candidate_approval_for_persistence` |
| `config/settings.py` | Added `AGENTS_REQUIRE_CANDIDATE_APPROVAL_FOR_PERSISTENCE` backward-compat constant |
| `config.toml` | Added `[agents]` section |

## No Capabilities Imports

Agent modules (`spec.py`, `policy.py`, `registry.py`, `candidate.py`, `candidate_store.py`) do NOT import from `src.capabilities`. The `is_capability_backed_agent` helper uses only AgentSpec fields, not capability store lookups.

## Evidence Types

Valid evidence types enforced by `AgentCandidate.__post_init__`: `dry_run`, `manual_review`, `policy_lint`, `regression_test`, `task_failure`, `task_success`. Invalid types are rejected at candidate creation time, not at save gate evaluation.

## Tool Integration (save_agent)

The `save_agent` tool (`src/tools/agent_tools.py:472`) calls `registry.save_agent()` without the `require_candidate_approval` parameter, so the gate is not applied through the tool even when the flag is enabled. This is acceptable for Phase 6C since the flag defaults to `false`. The tool does catch `AgentPolicyViolation` and return structured errors without stack traces.

## Future Phase 6D Notes

- Stale candidate detection (candidate older than N days)
- Archive-aware blocking (archived candidates blocked even if `approval_state == "approved"`)
- Evidence freshness requirements
- Auto-proposal candidate creation hook
- Runtime capability loading from bound_capabilities
- Wire `save_agent` tool to respect the feature flag
