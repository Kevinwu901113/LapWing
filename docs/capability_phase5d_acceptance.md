# Phase 5D Acceptance: Controlled Auto-Proposal Persistence

Date: 2026-05-02 (hardening pass: 2026-05-02)

## Test Results

### Phase 5D-specific tests

```
tests/capabilities/test_phase5d_auto_proposal.py — 78 passed
```

### Full capability suite

```
tests/capabilities/ — 1032 passed (0 failed)
```

### Core test suites

```
tests/core/test_task_runtime.py + test_task_runtime_guards.py    — 43 passed
tests/core/test_state_view.py + test_state_view_builder.py etc. — 53 passed
tests/core/test_tool_dispatcher.py + test_dispatcher.py etc.     — 139 passed
tests/core/test_skill_registration.py                            — included above
tests/core/test_authority_gate*.py + test_brain_*.py             — 47 passed
```

### Skills and agents

```
tests/skills/ + tests/agents/ — 272 passed
```

### Full project suite

```
3327 passed, 13 failed, 13 skipped
```

All 13 failures are pre-existing (verified on base commit `aa19a32` during
Phase 5C hardening pass). Zero regressions introduced by Phase 5D.

### Pre-existing failure breakdown

| Test file | Count | Root cause |
|-----------|-------|-----------|
| `test_audit_logging.py` | 2 | Browser guard assertion mismatch (`profile_not_allowed` vs `browser_guard_missing`) |
| `test_brain_tools.py` | 9 | Tool loop tests (shell consent, continuation, fallback, events) |
| `test_chat_tools_centralized.py` | 1 | Profile lists companion surface tools |
| `test_import_smoke.py` | 1 | llm_router anthropic path missing dependency |

## Test Coverage

### 1. Feature Flag Tests (10)

- `auto_proposal_enabled` defaults to `false`
- `auto_proposal_min_confidence` defaults to `0.75`
- `auto_proposal_allow_high_risk` defaults to `false`
- `auto_proposal_max_per_session` defaults to `3`
- `auto_proposal_dedupe_window_hours` defaults to `24`
- Compat shims wired correctly
- Independence from `curator_enabled`, `lifecycle_tools_enabled`, `retrieval_enabled`

### 2. Feature Flag Behavior Matrix (5 — Cases A-E)

| Case | Result |
|------|--------|
| A: capabilities.enabled=false | No observer, fail-closed |
| B: no summary | Observer wired but never called |
| C: no dry-run | Decision absent, fail-closed |
| D: dry-run exists, auto-proposal off | Observer not wired |
| E: all flags enabled | Observer wired and callable |

### 3. AutoProposalResult Dataclass (5)

- Default values correct
- Success result: `applied=false`, `source="task_end_auto_proposal"`
- Skipped result: `persisted=false`, `skipped_reason` set
- `to_dict()` serialization correct
- Source always `"task_end_auto_proposal"`

### 4. TaskRuntime Behavior (10)

- Observer not set by default
- Setter works; `set_auto_proposal_observer(None)` clears
- Observer not called without summary
- Observer not called without curator decision
- Observer not called when `should_create=false`
- Observer called when all conditions met
- Observer failure returns None (no crash)
- Result replaced per turn, not accumulated
- `_last_auto_proposal_result` initialized as `None`
- No `src.capabilities` import in `task_runtime.py`

### 5. Gate Tests (11)

- `should_create=false` → skipped
- Confidence below threshold → skipped
- Confidence at threshold → passes
- Missing generalization_boundary → skipped
- Whitespace-only boundary → skipped
- High risk → skipped by default
- High risk → allowed when `allow_high_risk=true`
- Missing verification for medium risk → skipped
- Low risk without verification → passes
- Unsupported `recommended_action` → skipped
- `no_action` → skipped (not in allowed set)

### 6. Safety Tests (7)

- API keys in summary → secrets gate triggers
- Bearer tokens in summary → secrets gate triggers
- Password in summary → secrets gate triggers
- Already-redacted data passes secrets gate
- Prompt injection treated as data (`applied` always false)
- No network imports (urllib, httpx, requests, aiohttp, socket, subprocess)
- No LLM imports (anthropic, openai, llm_router)

### 7. Persistence Tests (8)

- Successful persistence writes `proposal.json`
- Writes `PROPOSAL.md`
- Writes `source_trace_summary.json`
- Proposal has `applied=false`
- No draft capability directory created (only `proposals/`)
- Gate failure creates no files
- Result has `source="task_end_auto_proposal"`
- Result `applied` is always false

### 8. Dedup Tests (4)

- Same `source_trace_id` → duplicate skipped
- Same normalized name+scope → duplicate skipped
- Old duplicate outside window → passes
- Skipped result records clear reason string

### 9. Rate Limit Tests (2)

- `max_per_session` enforced (2 succeed, 3rd rate-limited)
- Skipped result records `"rate_limited"` reason

### 10. No-Mutation Tests (7)

- Adapter has no `CapabilityStore` import (code only, excluding docstrings)
- Adapter has no `CapabilityIndex` import
- Adapter has no `LifecycleManager`/`CapabilityLifecycleManager` import
- Adapter has no `create_draft` call
- Adapter has no `apply=true` or `apply = True`
- Adapter has no `promote` call
- `execution_summary.py` has no `src.capabilities` import

### 11. Import Hygiene (1)

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Only allowed files:
- `src/tools/capability_tools.py`
- `src/app/container.py`

### 12. Tool/Permission Isolation (3)

- `capability_tools.py` has no `auto_proposal` reference
- `runtime_profiles.py` has no `auto_proposal` reference
- Default tool registry has no auto_proposal tools

### 13. Failure Safety (4)

- Invalid summary dict (None) → returns None
- Empty summary → gates evaluated cleanly, no crash
- Malformed decision (empty dict) → gates handle missing keys
- All adapter exceptions caught, return None

## Hardening Pass — Additional Verifications

### Hard Constraint Verification (manual + automated)

| Constraint | Method | Result |
|-----------|--------|--------|
| No `src.capabilities` import in TaskRuntime | `grep` | Clean |
| No `src.capabilities` import in Brain | `grep` | Clean |
| No `src.capabilities` import in StateViewBuilder | `grep` | Clean |
| No `src.capabilities` import in SkillExecutor | `grep` | Clean |
| No `src.capabilities` import in ToolDispatcher | `grep` | Clean |
| No `src.capabilities` import in AgentRegistry | `grep` | Clean |
| No `src.capabilities` import in AgentPolicy | `grep` | Clean |
| No `CapabilityStore` import in adapter | `grep` (excl. docstrings) | Clean |
| No `CapabilityIndex` import in adapter | `grep` (excl. docstrings) | Clean |
| No `CapabilityLifecycleManager` import in adapter | `grep` (excl. docstrings) | Clean |
| No `EvalRecord` import in adapter | `grep` | Clean |
| No `VersionSnapshot` import in adapter | `grep` | Clean |
| No `create_draft` call in adapter | `grep` (excl. docstrings) | Clean |
| No `apply=true` in adapter | `grep` | Clean |
| No `.promote(` call in adapter | `grep` (excl. docstrings) | Clean |
| No network imports (http, urllib, requests, aiohttp, socket) | `grep` | Clean |
| No subprocess import | `grep` | Clean |
| No LLM imports (anthropic, openai, llm_router) | `grep` | Clean |
| No `run_capability` anywhere in src/ | `grep -rn` | Clean |
| No `auto_proposal` ref in capability_tools.py | `grep` | Clean |
| No `auto_proposal` ref in runtime_profiles.py | `grep` | Clean |
| StateView has no auto_proposal content | `grep` | Clean |
| StateViewBuilder has no auto_proposal content | `grep` | Clean |

### Adapter Internal Imports (verified correct boundary)

The adapter only imports from `src.capabilities`:
- `TraceSummary` — for sanitization
- `ExperienceCurator` — for summarization
- `persist_proposal`, `list_proposals` — for proposal-only persistence and dedup

It does NOT import: CapabilityStore, CapabilityIndex, CapabilityLifecycleManager,
CapabilityEvaluator, EvalRecord, VersionSnapshot, PromotionPlanner, or any
eval/memory modules.

### Config Defaults (all conservative)

```
auto_proposal_enabled = false
auto_proposal_min_confidence = 0.75
auto_proposal_allow_high_risk = false
auto_proposal_max_per_session = 3
auto_proposal_dedupe_window_hours = 24
```

### Feature Flag Independence

All 5 auto-proposal flags are independent — none imply or depend on:
- `curator_enabled`
- `lifecycle_tools_enabled`
- `retrieval_enabled`

## Files Changed

| File | Change |
|------|--------|
| `src/config/settings.py` | 5 new CapabilitiesConfig fields + 5 env var mappings |
| `config/settings.py` | 5 new compat shim constants |
| `src/core/execution_summary.py` | AutoProposalResult dataclass + AutoProposalObserver protocol |
| `src/core/task_runtime.py` | 2 new fields, setter, Phase 5D finally-block code |
| `src/capabilities/auto_proposal_adapter.py` | New file: gate logic, dedup, rate limit, persistence |
| `src/app/container.py` | 5 new flag imports + Phase 5D wiring block |
| `tests/capabilities/test_phase5d_auto_proposal.py` | New file: 78 tests |
| `tests/capabilities/test_phase4_hardening.py` | Added auto_proposal_adapter.py to allowed ExperienceCurator files |
| `docs/capability_evolution_architecture.md` | Phase 5D section added |
| `docs/capability_phase5d_acceptance.md` | This file (created, then hardened) |

## Feature Flag Matrix

| auto_proposal_enabled | Other new flags | Default |
|-----------------------|-----------------|---------|
| `auto_proposal_enabled` | — | `false` |
| `auto_proposal_min_confidence` | — | `0.75` |
| `auto_proposal_allow_high_risk` | — | `false` |
| `auto_proposal_max_per_session` | — | `3` |
| `auto_proposal_dedupe_window_hours` | — | `24` |

All defaults are conservative.

## Auto-Proposal Gates (verified)

All 9 gates tested and working:

1. `should_create=true` required
2. `recommended_action` in allowed set
3. Confidence >= threshold
4. Risk level gating (high blocked unless allowed)
5. Generalization boundary required
6. Verification required for medium/high risk
7. Secrets double-check
8. Rate limit
9. Deduplication

## Persistence Behavior

- Writes `proposal.json`, `PROPOSAL.md`, `source_trace_summary.json` to `data/capabilities/proposals/<id>/`
- `applied` always `false`
- No draft capability directories created
- No `CapabilityStore`, `CapabilityIndex`, `CapabilityLifecycleManager` access
- No EvalRecords, version snapshots, memories

## Deduplication

- Filesystem-based: scans existing proposals
- Compares `source_trace_id`, `proposed_capability_id`, normalized name+scope
- Configurable window (default 24 hours)
- Returns clear `skipped_reason`

## Rate Limiting

- In-memory counter per adapter instance
- Configurable max per session (default 3)
- Returns `skipped_reason="rate_limited"`

## Safety / Redaction

- Defense-in-depth secrets double-check before persistence
- API keys, Bearer tokens, password values, PEM keys detected
- Already-redacted data (`<REDACTED>`) passes
- Prompt injection treated as data
- No network, shell, LLM, subprocess imports in adapter

## No-Draft Proof

- Adapter source contains no `create_draft` call (verified by AST-like check)
- Adapter source contains no `apply=true` (verified)
- Adapter source contains no `CapabilityStore` import (verified)
- Persisted proposal.json has `applied: false`
- No `data/capabilities/<scope>/<id>/` directories created

## No-Index Proof

- Adapter source contains no `CapabilityIndex` import
- `execution_summary.py` contains no `src.capabilities` import
- No index mutations in adapter

## Runtime Import Grep

```
src/tools/capability_tools.py  ← allowed
src/app/container.py           ← allowed
```

TaskRuntime, Brain, StateViewBuilder, SkillExecutor, ToolDispatcher,
AgentRegistry, AgentPolicy — all clean.

## User-Facing Behavior

Unchanged. Result is in `_last_auto_proposal_result` only. No "I created a
proposal" message appended to responses.

## No `run_capability`

Confirmed: no `run_capability` exists anywhere in `src/`. No capability execution.

## Known Issues

13 pre-existing test failures (all verified as pre-existing on `aa19a32`):

- 2 `test_audit_logging.py` (browser guard)
- 9 `test_brain_tools.py` (tool loop tests)
- 1 `test_chat_tools_centralized.py` (profile lists)
- 1 `test_import_smoke.py` (llm_router anthropic path)

Zero failures introduced by Phase 5D.

## Rollback Notes

To roll back Phase 5D:
1. Set `CAPABILITIES_AUTO_PROPOSAL_ENABLED=false` (already the default)
2. Or remove the `if CAPABILITIES_AUTO_PROPOSAL_ENABLED:` block from `container.py`
3. Observer fields in TaskRuntime are harmless when `_auto_proposal_observer is None`

No database migrations, no persistent state to unwind.
