# Phase 6D: Agent Candidate Operator Tools

## Overview

Phase 6D exposes explicit, feature-gated, operator-only tools for managing AgentCandidate objects and evidence. Candidates remain non-runnable, non-executable staging objects separate from active agents.

## Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `agents.candidate_tools_enabled` | `false` | Gates registration of all 6 candidate operator tools |
| `agents.candidate_evidence_max_age_days` | `90` | Evidence staleness threshold in days; set to `None` or `0` to disable freshness checks |

Both flags live in `[agents]` section of `config.toml` and `AgentsConfig` in `src/config/settings.py`.

Default `90` is consistent across all layers: Pydantic model, `config.toml`, compat shim, `AgentPolicy.__init__`, and the save gate sentinel fallback. Missing `[agents]` TOML section or missing key within it falls back to `90` (not `None`).

## Tools

All 6 tools require the `agent_candidate_operator` capability tag. They register only when `agents.candidate_tools_enabled=true`.

| Tool | Risk | Write | Description |
|------|------|-------|-------------|
| `list_agent_candidates` | low | no | List candidates with filters (approval_state, risk_level, include_archived, limit) |
| `view_agent_candidate` | low | no | View candidate details, evidence, spec metadata, policy findings |
| `add_agent_candidate_evidence` | low | yes | Append evidence record; does not change approval_state |
| `approve_agent_candidate` | medium | yes | Approve candidate after policy validation; refuses archived |
| `reject_agent_candidate` | low | yes | Reject candidate; changes state only, no file deletion |
| `archive_agent_candidate` | low | yes | Archive candidate; excluded from default listing and save gate |

### Forbidden tools (not registered)

- `run_agent_candidate`
- `promote_agent_candidate`
- `save_candidate_as_agent`
- `execute_candidate`
- `auto_approve_agent_candidate`
- `run_capability`

## Permission Model

### Capability Tag

`agent_candidate_operator` — a new capability tag used exclusively by candidate operator tools.

### Runtime Profile

`AGENT_CANDIDATE_OPERATOR_PROFILE` (`name="agent_candidate_operator"`) carries the `agent_candidate_operator` capability.

### Profile Access Matrix

| Profile | Has `agent_candidate_operator`? | Can use candidate tools? |
|---------|--------------------------------|--------------------------|
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

### Key property

`candidate_tools_enabled=true` registers the tools but **does not grant permissions** to any profile. Only agents explicitly assigned `agent_candidate_operator` profile can call these tools.

## Read/Write Behavior

- **Read tools** (`list`, `view`): pure read, no mutations, no execution
- **Write tools** (`add_evidence`, `approve`, `reject`, `archive`): mutate candidate state on disk only
- **All tools**: no active agent creation, no registry mutation, no execution, no auto-promotion

## Non-Execution Guarantee

- No tool creates an `AgentRuntime` or `TaskRuntime`
- No tool calls `AgentRegistry.get_or_create_instance`
- No tool modifies `Brain`, `StateView`, or `ToolDispatcher`
- Approval changes `approval_state` only — it does not create agents, save agents, or grant permissions
- Evidence is append-only — it never changes `approval_state`

## Phase 6C Gap Fixes

### Gap 1: Archived candidates pass save gate (FIXED)

**Before**: `validate_persistent_save_gate` checked `candidate.approval_state == "approved"` but did not check `candidate.metadata.get("archived")`. An archived-but-approved candidate passed the gate.

**After**: Step 1a added — archived candidates are denied even if `approval_state` is `"approved"`. Returns `candidate_archived` denial.

### Gap 2: No evidence freshness check (FIXED)

**Before**: Evidence sufficiency checked presence/type of evidence but not age.

**After**: When `evidence_max_age_days` is set (default 90), high/medium risk evidence items older than the threshold are denied. Conservative handling:
- Missing `created_at` → treated as stale
- Unparseable `created_at` → treated as stale
- Naive datetimes → treated as UTC
- Low risk → freshness check skipped

### Gap 3: save_agent tool does not pass through feature flag (NOT ADDRESSED)

Deferred per spec. The flag is available in config for future wiring.

## Files Changed

### New files
- `src/tools/agent_candidate_tools.py` — 6 candidate operator tools
- `tests/agents/test_agent_candidate_tools.py` — 39 tool behavior tests
- `tests/agents/test_agent_candidate_operator_profile.py` — 24 permission/profile tests
- `tests/agents/test_agent_candidate_save_gate_hardening.py` — 16 save gate hardening tests

### Modified files
- `src/config/settings.py` — added `candidate_tools_enabled`, `candidate_evidence_max_age_days` to `AgentsConfig` and env var map
- `config/settings.py` — added constant exports
- `config.toml` — added `candidate_tools_enabled`, `candidate_evidence_max_age_days`
- `src/core/runtime_profiles.py` — added `AGENT_CANDIDATE_OPERATOR_PROFILE`
- `src/agents/policy.py` — added archived check and evidence freshness to `validate_persistent_save_gate`
- `src/app/container.py` — wired candidate store creation and tool registration
- `tests/agents/test_agent_save_gate.py` — updated archived candidate test for Phase 6D behavior

## Rollback Notes

- Set `agents.candidate_tools_enabled = false` to disable all candidate tools
- Set `agents.candidate_evidence_max_age_days` to empty/null to disable freshness checks
- Archived candidate blocking is always active when save gate is enabled (cannot be rolled back independently)
