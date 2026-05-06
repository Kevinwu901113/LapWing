# Capability Phase 6B — Agent Candidates

**Date:** 2026-05-02
**Status:** Implemented

---

## Purpose

Introduce an explicit `AgentCandidate` object and evidence model for future persistent dynamic agent promotion. Candidates live in filesystem storage separate from the active `AgentCatalog` and cannot run as agents.

Phase 6B is **data-model and storage only** — it does not change existing `save_agent` behavior or require candidate approval for persistence.

---

## AgentCandidate Model

`src/agents/candidate.py`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `candidate_id` | `str` | `"cand_" + hex` | Path-safe, validated `[a-z][a-z0-9_-]{2,63}` |
| `name` | `str` | `""` | Human-readable name |
| `description` | `str` | `""` | Brief description |
| `proposed_spec` | `AgentSpec` | `AgentSpec()` | The proposed agent specification |
| `created_at` | `str` | ISO timestamp | Creation time |
| `created_by` | `str \| None` | `None` | Who created this candidate |
| `source_trace_id` | `str \| None` | `None` | Trace ID from which candidate was proposed |
| `source_task_summary` | `str \| None` | `None` | Task summary (redacted) |
| `reason` | `str` | `""` | Why this candidate was created |
| `approval_state` | `str` | `"pending"` | `not_required` / `pending` / `approved` / `rejected` |
| `risk_level` | `str` | `"low"` | `low` / `medium` / `high` |
| `requested_runtime_profile` | `str \| None` | `None` | Desired runtime profile |
| `requested_tools` | `list[str]` | `[]` | Requested tool names |
| `bound_capabilities` | `list[str]` | `[]` | Capability IDs (strings only) |
| `eval_evidence` | `list[AgentEvalEvidence]` | `[]` | Accumulated evaluation evidence |
| `policy_findings` | `list[AgentCandidateFinding]` | `[]` | Policy lint findings |
| `version` | `str` | `"1"` | Schema version |
| `metadata` | `dict` | `{}` | Extensible metadata |

### Serialization

- `to_dict()` → flat dict with `proposed_spec` as nested dict via `dataclasses.asdict()`
- `to_json()` → JSON string
- `from_dict(data)` → AgentCandidate (non-mutating: copies input dicts)
- `from_json(json_str)` → AgentCandidate

### Validation (constructor)

- `candidate_id` — path-safe, regex-validated
- `approval_state` — must be in `VALID_APPROVAL_STATES`
- `risk_level` — must be in `VALID_RISK_LEVELS`

---

## AgentEvalEvidence

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `evidence_id` | `str` | `"ev_" + hex` | Path-safe, validated |
| `created_at` | `str` | ISO timestamp | When evidence was created |
| `evidence_type` | `str` | `"task_success"` | `task_success` / `task_failure` / `manual_review` / `policy_lint` / `dry_run` / `regression_test` |
| `summary` | `str` | `""` | Human-readable summary |
| `passed` | `bool` | `True` | Whether this evidence point passed |
| `score` | `float \| None` | `None` | Optional score 0.0-1.0 |
| `trace_id` | `str \| None` | `None` | Associated trace ID |
| `details` | `dict` | `{}` | Arbitrary details |

Score bounded to `[0.0, 1.0]` when set. Evidence ID path-safe validated.

---

## AgentCandidateFinding

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `severity` | `str` | `"info"` | `info` / `warning` / `error` |
| `code` | `str` | `""` | Machine-readable finding code |
| `message` | `str` | `""` | Human-readable message |
| `details` | `dict` | `{}` | Arbitrary details |

---

## Candidate Store

`src/agents/candidate_store.py`

Filesystem layout under `data/agent_candidates/`:

```
<candidate_id>/
  candidate.json
  evidence/
    <evidence_id>.json
```

### API

| Method | Returns | Notes |
|--------|---------|-------|
| `create_candidate(candidate)` | `AgentCandidate` | Rejects duplicate `candidate_id` |
| `get_candidate(candidate_id)` | `AgentCandidate` | Raises `CandidateStoreError` if not found |
| `get_candidate_or_none(candidate_id)` | `AgentCandidate \| None` | Safe lookup |
| `list_candidates(approval_state=None, risk_level=None)` | `list[AgentCandidate]` | Filtered list |
| `update_candidate(candidate)` | `AgentCandidate` | Overwrites existing |
| `add_evidence(candidate_id, evidence)` | `AgentCandidate` | Appends evidence, writes standalone file |
| `update_approval(candidate_id, approval_state, reviewer=None, reason=None)` | `AgentCandidate` | Updates approval with metadata |
| `archive_candidate(candidate_id, reason=None)` | `AgentCandidate` | Sets `metadata["archived"]=True`, does NOT delete |

### Safety

- All path operations validated through `validate_candidate_id` / `validate_evidence_id`
- Atomic writes: writes to `.tmp` then `replace`
- Tolerates corrupt candidate files in `list_candidates` (skips with warning)
- No active agent files created
- Separate from `AgentCatalog` (SQLite `lapwing.db`)

---

## Candidate Policy

`AgentPolicy.validate_agent_candidate(candidate, *, known_profiles=None, available_tools=None) -> CandidateValidationResult`

### Validation Rules

| # | Check | Type | Behavior |
|---|-------|------|----------|
| 1 | `proposed_spec` is not an `AgentSpec` | Denial | Blocks |
| 2 | `requested_runtime_profile` not in `known_profiles` (when provided) | Denial | Blocks |
| 3 | `bound_capabilities` entry fails `[a-z][a-z0-9_]{2,63}` syntax | Denial | Blocks |
| 4 | `bound_capabilities` contains agent_admin/agent_create IDs | Denial | Blocks |
| 5 | `requested_tools` not in `available_tools` (when provided) | Warning | Does not block |
| 6 | `risk_level == "high"` and `approval_state != "approved"` | Warning | Future-phase notice |
| 7 | `approval_state == "rejected"` | Warning | Future-phase notice |

### Properties

- Deterministic (same input → same output)
- Non-mutating (does not modify candidate)
- No `src.capabilities` imports
- No I/O or side effects
- Returns `CandidateValidationResult(allowed, warnings, denials)`

---

## Secret Redaction

`redact_secrets_in_summary(summary: str | None) -> str | None`

Best-effort defense-in-depth for user-provided task summaries:
- OpenAI API keys (`sk-...`)
- Bearer tokens
- Password assignments
- Base64 blobs

---

## Non-Runtime Guarantee

Phase 6B does **not** change:
- Dynamic agent execution semantics
- `save_agent` behavior
- `AgentRegistry.list_agents` output (candidates not included)
- `AgentRegistry.load` / AgentCatalog behavior
- ToolDispatcher behavior
- RuntimeProfile resolution
- Brain / TaskRuntime / StateView behavior
- No capability loading or execution
- No `run_capability`

---

## Relationship to Future Phases

- **Phase 6C:** Enforce candidate approval before promotion to persistent; bind capabilities based on candidate metadata.
- **Phase 6D:** Auto-promotion workflow with evidence thresholds; save gates integrate candidate validation.
