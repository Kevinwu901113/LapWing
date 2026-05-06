# Capability Phase 6B — Acceptance Report

**Date:** 2026-05-02
**Branch:** master
**Status:** Accepted (Hardened)
**Hardening pass:** 2026-05-02

---

## Test Results

### New Phase 6B Tests

| Test File | Count | Passed | Failed |
|-----------|-------|--------|--------|
| `tests/agents/test_agent_candidate.py` | 49 | 49 | 0 |
| `tests/agents/test_agent_candidate_store.py` | 31 | 31 | 0 |
| `tests/agents/test_agent_candidate_policy.py` | 27 | 27 | 0 |
| **New Phase 6B total** | **107** | **107** | **0** |

### Full Suite Results (2026-05-02, post-hardening)

| Suite | Collected | Passed | Failed | Skipped |
|-------|-----------|--------|--------|---------|
| `tests/agents/` (incl. 67 Phase 6A + 107 Phase 6B) | 382 | 382 | 0 | 0 |
| `tests/capabilities/` | 1,032 | 1,032 | 0 | 0 |
| `tests/skills/` | 64 | 64 | 0 | 0 |
| `tests/logging/` | 32 | 32 | 0 | 0 |
| `tests/core/test_tool_dispatcher.py` + `test_runtime_profiles_exclusion.py` | 87 | 87 | 0 | 0 |
| **Relevant suites total** | **1,597** | **1,597** | **0** | **0** |

### Suite Composition

- **Pre-6B agent tests:** 275 (208 pre-6A + 67 Phase 6A)
- **New Phase 6B agent tests:** 107
- **agents/ + capabilities/ combined:** 1,414

---

## Files Changed

### Production code (3 files)

1. **`src/agents/candidate.py`** (new)
   - `AgentEvalEvidence` dataclass — evidence model with type validation, score bounds, path-safe ID
   - `AgentCandidateFinding` dataclass — policy finding with severity validation
   - `AgentCandidate` dataclass — full candidate model with serialization/deserialization, safe defaults
   - `validate_candidate_id()` — path-safe candidate ID validation
   - `validate_evidence_id()` — path-safe evidence ID validation
   - `redact_secrets_in_summary()` — best-effort secret redaction

2. **`src/agents/candidate_store.py`** (new)
   - `AgentCandidateStore` class — filesystem-backed candidate storage
   - Layout: `data/agent_candidates/<candidate_id>/candidate.json` + `evidence/<evidence_id>.json`
   - CRUD: `create_candidate`, `get_candidate`, `get_candidate_or_none`, `list_candidates`, `update_candidate`
   - Evidence: `add_evidence` with standalone evidence files
   - Approval: `update_approval` with reviewer/reason metadata
   - Archive: `archive_candidate` sets metadata, does NOT delete files
   - Atomic writes via `.tmp` → `replace`
   - Tolerates corrupt files in `list_candidates` (skips with warning)

3. **`src/agents/policy.py`** (updated)
   - Added `CandidateValidationResult` dataclass
   - Added `AgentPolicy.validate_agent_candidate()` method with 7 validation checks

### Test code (3 files)

4. **`tests/agents/test_agent_candidate.py`** (49 tests)
   - AgentEvalEvidence tests (12)
   - AgentCandidateFinding tests (5)
   - AgentCandidate tests (19)
   - validate_candidate_id tests (5)
   - validate_evidence_id tests (3)
   - Secret redaction tests (5)

5. **`tests/agents/test_agent_candidate_store.py`** (31 tests)
   - CreateCandidate (3)
   - GetCandidate (4)
   - ListCandidates (6)
   - AddEvidence (3)
   - UpdateApproval (3)
   - ArchiveCandidate (3)
   - UpdateCandidate (2)
   - PathSafety (2)
   - NoActiveAgentFiles (2)
   - CorruptFileHandling (2)
   - StoreRoundTrip (1)

6. **`tests/agents/test_agent_candidate_policy.py`** (27 tests)
   - ValidCandidatePassesLint (4)
   - UnknownRuntimeProfile (3)
   - RequestedTools (3)
   - HighRiskRequiresApproval (3)
   - RejectedCandidate (2)
   - InvalidBoundCapabilityId (3)
   - LintDeterministic (1)
   - LintDoesNotMutateCandidate (1)
   - LintDoesNotImportSrcCapabilities (1)
   - LintDoesNotGrantPermissions (2)
   - CandidateValidationResult (2)
   - SelfReferentialCapability (2)

---

## 1. Active Registry Separation (Verified)

- [x] AgentCandidateStore wired to `data/agent_candidates/` only; no cross-reference to AgentCatalog path (`lapwing.db`)
- [x] Candidate files never written into active AgentRegistry storage
- [x] `AgentRegistry.list_agents()` lists catalog (builtin + persistent), session agents, ephemeral agents, and legacy agents — no candidates
- [x] `AgentRegistry.get_or_create_instance()` does not load candidates
- [x] Dynamic agent factory (`src/agents/dynamic.py`, `src/agents/factory.py`) does not reference candidates
- [x] `src/tools/agent_tools.py` does not import or reference candidates
- [x] Candidate IDs use `cand_` prefix (e.g., `cand_a1b2c3d4e5f6`); cannot collide with agent names (`[a-z0-9_]+`, max 32 chars)
- [x] Creating a candidate with overlapping name/ID does not mutate any active agent
- [x] Archiving a candidate does not affect active agents
- [x] No candidate code in `AgentCatalog`, `AgentRegistry`, `AgentFactory`, `Brain`, `TaskRuntime`, `ToolDispatcher`, or `StateView`

---

## 2. Candidate Cannot Execute (Verified)

- [x] No `run_candidate` tool exists anywhere in `src/`
- [x] No `approve_and_run_candidate` tool exists anywhere in `src/`
- [x] No candidate execution path exists in any module
- [x] `AgentCandidate` cannot be passed to existing `run_agent` APIs — no conversion exists in Phase 6B
- [x] `AgentCandidate.proposed_spec` is inert metadata (never used to construct an active agent)
- [x] `bound_capabilities` are inert string IDs (never loaded, never executed)
- [x] `requested_tools` are inert metadata (never granted to any agent)
- [x] `requested_runtime_profile` is inert metadata (never resolved to a RuntimeProfile)
- [x] `approval_state == "approved"` does not make candidate runnable
- [x] `eval_evidence` entries with `passed=true` do not make candidate runnable
- [x] No `run_capability` exists anywhere in the codebase

---

## 3. Storage Safety (Verified)

- [x] `validate_candidate_id` rejects: empty, non-string, `..`, `/`, `\`, too short (<3 chars), bad chars (uppercase, dots, etc.)
- [x] `validate_evidence_id` rejects: empty, non-string, `..`, `/`, `\`, too long (>128 chars)
- [x] Candidate store writes only under configured `base_dir` (tests use `tmp_path`)
- [x] Atomic writes: `.tmp` file then `os.replace()` — no partial `candidate.json` on normal failure
- [x] Duplicate `candidate_id` rejected with `CandidateStoreError`
- [x] Corrupt `candidate.json` raises `CandidateStoreError` on direct `get`; skipped with warning in `list_candidates`
- [x] Corrupt evidence file: evidence is stored separately per evidence ID; main candidate JSON references it via list
- [x] `list_candidates` tolerates directories without `candidate.json` (skips)
- [x] `list_candidates` tolerates corrupt candidate files (skips with warning)
- [x] `list_candidates` tolerates non-conforming directory names that fail `validate_candidate_id` (skips)
- [x] Path traversal rejected on `create_candidate`, `get_candidate`, `add_evidence`
- [x] All tests use `tmp_path` fixtures; no real data directories touched

---

## 4. Privacy / Redaction (Verified)

### Secrets redaction at trace_summary level (Phase 5A — primary defense)
- [x] `src/capabilities/trace_summary.py` `_DROP_KEYS` drops: `_cot`, `_chain_of_thought`, `chain_of_thought`, `_internal`, `_reasoning`, `_thinking`, `reasoning_trace`, `scratchpad`, `hidden_thoughts`, `internal_notes`
- [x] `trace_summary.py` `_SECRET_PATTERNS` redacts: `sk-*` tokens, API_KEY assignments, Bearer tokens, password assignments, PEM private keys (RSA, EC, DSA, OpenSSH)
- [x] `trace_summary.py` `_MAX_STR_LEN = 50_000` bounds string fields

### Secrets redaction at candidate level (defense-in-depth)
- [x] `redact_secrets_in_summary()` redacts: `sk-*` tokens, Bearer tokens, `password=` assignments, `api_key=` assignments, base64 blobs
- [x] Returns `None` for `None` input
- [x] Clean text passes through unchanged

### What is NOT stored
- [x] Raw full transcripts not stored — task summary is a processed summary, not raw conversation
- [x] CoT/internal fields dropped upstream by trace_summary before any candidate ingestion
- [x] PEM private keys redacted upstream by trace_summary

### Known hardening gap (acceptable for Phase 6B)
- [ ] `evidence.details` and `candidate.metadata` are NOT sanitized by Phase 6B code. These are populated by system code (not user input) and are documented as "arbitrary details" / "extensible metadata." Sanitization at the boundaries where user data enters these fields is a concern for future phases or for the callers that populate them.
- [ ] Long log truncation is handled by trace_summary (`_MAX_STR_LEN = 50,000`), not duplicated in the candidate layer. If summary text is constructed outside trace_summary, length bounds are the caller's responsibility.

### Prompt injection
- [x] All candidate text fields (`source_task_summary`, `reason`, `description`, etc.) are treated as data — they are never interpreted as instructions
- [x] No LLM calls made on candidate data in Phase 6B

---

## 5. Policy Lint Hardening (Verified)

- [x] `validate_agent_candidate()` is deterministic: same input → same `CandidateValidationResult`
- [x] Does not mutate `AgentCandidate` (all 18 fields verified unchanged post-lint)
- [x] Does not mutate `proposed_spec` (AgentSpec verified unchanged)
- [x] Does not import `src.capabilities` (verified via `sys.modules` check in tests)
- [x] Does not call `CapabilityStore` (no capability imports period)
- [x] Does not grant tools, profiles, or permissions (result is plain data)
- [x] Does not perform I/O or have side effects

### Validation rules verified
| # | Rule | Verdict | Test coverage |
|---|------|---------|---------------|
| 1 | `proposed_spec` must be `AgentSpec` | Denial | ValidCandidatePassesLint tests |
| 2 | Unknown `requested_runtime_profile` | Denial (when `known_profiles` provided) | TestUnknownRuntimeProfile |
| 3 | Invalid `bound_capability` syntax | Denial | TestInvalidBoundCapabilityId |
| 4 | Self-referential agent_admin/agent_create | Denial | TestSelfReferentialCapability |
| 5 | Unknown `requested_tools` | Warning (when `available_tools` provided) | TestRequestedTools |
| 6 | `risk_level == "high"` without approval | Warning | TestHighRiskRequiresApproval |
| 7 | `approval_state == "rejected"` | Warning (future-phase) | TestRejectedCandidate |

- [x] `CandidateValidationResult.allowed` = `False` when any denial present
- [x] `CandidateValidationResult.allowed` = `True` when only warnings
- [x] Policy findings are data records only; they do not become permissions
- [x] `_CAPABILITY_ID_RE` reused from policy.py (no import from capabilities package)

---

## 6. Evidence / Approval Semantics (Verified)

- [x] `add_evidence()` appends evidence to `eval_evidence` list without changing `approval_state`
- [x] `add_evidence()` also writes standalone evidence JSON file under `evidence/<evidence_id>.json`
- [x] `update_approval()` changes only `approval_state` and optionally `metadata["reviewer"]` / `metadata["approval_reason"]`
- [x] `update_approval()` validates that new state is in `VALID_APPROVAL_STATES`
- [x] `approval_state == "approved"` does NOT create an active agent
- [x] `approval_state == "approved"` does NOT change `proposed_spec.runtime_profile`
- [x] Evidence `passed=true` does NOT change `risk_level` automatically
- [x] `archive_candidate()` does NOT delete evidence files
- [x] `archive_candidate()` sets `metadata["archived"] = True`, `metadata["archived_at"]`, optional `metadata["archive_reason"]`
- [x] Archived candidates are still listable (filtered by `approval_state` or `risk_level` as usual)
- [x] Rejected candidates receive a warning that they will be blocked from future promotion
- [x] No auto-approval logic exists anywhere in Phase 6B

---

## 7. Serialization / Compatibility (Verified)

- [x] `AgentCandidate` JSON round-trips exactly (all 18 fields preserved)
- [x] `AgentEvalEvidence` JSON round-trips exactly
- [x] `AgentCandidateFinding` JSON round-trips exactly
- [x] Unknown extra keys in JSON tolerated by `from_dict` (ignored)
- [x] Legacy/minimal JSON without Phase 6B fields loads with safe defaults
- [x] `from_dict` rejects `proposed_spec` that is neither `dict` nor `AgentSpec`
- [x] `from_dict` copies input dicts — does not mutate caller's data
- [x] `to_dict` / `to_json` does not mutate candidate or `proposed_spec`
- [x] Existing `AgentSpec` serialization unchanged (Phase 6A `spec_hash` still works)
- [x] Existing persistent agent files still load (no schema change to catalog)
- [x] Candidate store does not touch `LegacyAgentSpec` paths
- [x] `spec_hash` behavior from Phase 6A unchanged (verified by existing agent tests)

---

## 8. Runtime Behavior Unchanged (Verified)

- [x] `save_agent` behavior unchanged (77 existing agent regression tests pass)
- [x] `run_agent` behavior unchanged (dynamic agent execution tests pass)
- [x] `list_agents` behavior unchanged (no candidate entries)
- [x] Dynamic agent creation unchanged (`validate_create` untouched)
- [x] `ToolDispatcher` behavior unchanged (87 core tests pass)
- [x] `RuntimeProfile` resolution unchanged
- [x] `Brain` / `TaskRuntime` / `StateView` unchanged
- [x] Capability read/list/search tools unchanged (1,032 capability tests pass)
- [x] Lifecycle evaluate/plan/transition tools unchanged
- [x] Curator tools unchanged
- [x] Capability retrieval unchanged
- [x] Execution summary / dry-run / auto-proposal unchanged
- [x] No `run_capability` exists
- [x] Existing `save_agent` behavior unchanged (does not require candidate approval)
- [x] Existing `AgentRegistry` behavior unchanged for old specs

---

## 9. Runtime Import Audit (Verified)

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ --include="*.py" | grep -v 'src/capabilities/'
src/tools/capability_tools.py    (allowed — tool registration)
src/app/container.py             (allowed — wiring)
```

### Agent modules confirmed clean (no `src.capabilities` imports)
- [x] `src/agents/spec.py`
- [x] `src/agents/policy.py`
- [x] `src/agents/registry.py`
- [x] `src/agents/candidate.py`
- [x] `src/agents/candidate_store.py`
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

## 10. Permission / Escalation Audit (Verified)

- [x] Candidates do NOT grant runtime profiles (no profile resolution from candidate)
- [x] `bound_capabilities` are string IDs only — no loading or execution
- [x] Candidates do NOT create active agents
- [x] Candidates do NOT affect `ToolDispatcher`
- [x] Candidate approval does NOT bypass any existing gate
- [x] Candidate metadata does NOT change execution privileges
- [x] No self-referential capability binding allowed (agent_admin/agent_create blocked by policy)
- [x] No agent can self-elevate through candidates
- [x] No eval evidence required for existing agent operations
- [x] No persistent lifecycle changes from candidate operations

---

## Model Behavior (Verified)

- [x] AgentCandidate defaults safe (pending, low risk, empty lists)
- [x] AgentCandidate serializes to JSON with nested proposed_spec
- [x] AgentCandidate deserializes from JSON with full proposed_spec
- [x] AgentCandidate round-trip preserves all fields including evidence, findings, metadata
- [x] Invalid candidate_id rejected (bad chars, too short, path traversal)
- [x] Invalid approval_state rejected
- [x] Invalid risk_level rejected
- [x] Invalid evidence_type rejected
- [x] Evidence score bounds enforced [0.0, 1.0]
- [x] Evidence path traversal rejected
- [x] Evidence too-long ID rejected
- [x] Finding severity validated
- [x] proposed_spec not mutated during serialization
- [x] Legacy/minimal JSON without Phase 6B extra fields loads safely
- [x] Tolerates unknown extra keys in from_dict
- [x] from_dict rejects proposed_spec that is neither dict nor AgentSpec
- [x] Secret redaction strips API keys, bearer tokens, passwords
- [x] Secret redaction leaves clean text unchanged
- [x] Secret redaction of None returns None

## Storage Behavior (Verified)

- [x] create_candidate writes candidate.json in correct directory
- [x] create_candidate creates evidence subdirectory
- [x] Duplicate candidate_id rejected
- [x] get_candidate reads persisted candidate correctly
- [x] get_candidate raises for nonexistent
- [x] get_candidate_or_none returns None for nonexistent
- [x] list_candidates returns all, empty when store empty
- [x] list_candidates filters by approval_state
- [x] list_candidates filters by risk_level
- [x] list_candidates skips directories without candidate.json
- [x] list_candidates skips corrupt candidate files
- [x] add_evidence appends to candidate and writes standalone evidence file
- [x] Multiple evidence appended in order
- [x] update_approval sets approval_state and metadata (reviewer, reason)
- [x] update_approval rejects invalid approval_state
- [x] Approval changes persist across re-reads
- [x] archive_candidate does NOT delete files
- [x] archive_candidate sets metadata (archived, archived_at, archive_reason)
- [x] update_candidate overwrites existing
- [x] update_candidate rejects nonexistent
- [x] Candidate path traversal rejected on create
- [x] Evidence path traversal rejected on add
- [x] No active agent files created by candidate store
- [x] Corrupt JSON raises CandidateStoreError on get
- [x] Bad schema proposed_spec raises CandidateStoreError on get

## Policy Behavior (Verified)

- [x] Valid minimal candidate passes lint
- [x] Candidate with known runtime_profile passes
- [x] Candidate with valid bound_capabilities passes
- [x] Unknown runtime_profile denied when known_profiles provided
- [x] Unknown runtime_profile not checked when no known_profiles
- [x] Empty/null profile skipped when known_profiles provided
- [x] Unknown requested_tool warned when available_tools provided
- [x] All-known tools produce no warning
- [x] Requested tools not checked when no available_tools
- [x] High risk + not approved → warning
- [x] High risk + approved → no high-risk warning
- [x] Low risk → no approval warning
- [x] Rejected approval_state → future-phase warning
- [x] Invalid bound_capability syntax → denial
- [x] Valid bound_capabilities → passes
- [x] Empty bound_capabilities → passes
- [x] Lint deterministic (same input → same output)
- [x] Lint does not mutate candidate
- [x] Lint does not import src.capabilities
- [x] Lint does not grant tools/profiles/permissions
- [x] Self-referential agent_admin capability → denial
- [x] Self-referential agent_create capability → denial

---

## Known Issues

### Hardening gap (not blocking)
- `evidence.details` and `candidate.metadata` are not sanitized by Phase 6B. These are system-populated fields, but callers should sanitize them before storing user-derived data. The primary defense is that Phase 6B code does not populate these from user input; future phases or callers should add sanitization at their boundaries.

### Not in scope for Phase 6B
- Candidate store is not wired into the application container (`src/app/container.py`). Storage is available as a library component but not instantiated at runtime. Wiring is a Phase 6C concern.
- No tool exposes candidate CRUD to agents. Tool exposure is a Phase 6C concern.

---

## Rollback Notes

To revert: delete the 3 new production files + 3 new test files and revert `policy.py`. No database migrations. No config changes. No dependency changes. Candidate store directories created during testing are under `tmp_path` (pytest fixtures) and are cleaned automatically.

Backward compatibility: existing AgentCatalog and AgentRegistry are completely unaffected — the new code is additive.
