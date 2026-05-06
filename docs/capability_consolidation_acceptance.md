# Capability System Consolidation — Audit Acceptance

**Date:** 2026-05-02
**Baseline commit:** `aa19a32` (Phase 3+4) + Phase 5A/B/C/D changes
**Scope:** Audit and consolidation only — no new features, tools, or automation

---

## 1. Test Results

### Capability Tests

```
tests/capabilities/ — 1,032 passed, 0 failed
```

Breakdown:
- Phase 0/1 regression: all passing
- Phase 2A/B store/index/search/tools: all passing
- Phase 3A policy/evaluator/records/promotion: all passing
- Phase 3B lifecycle/hardening/regression/transition_atomicity: all passing
- Phase 3C lifecycle tools: all passing
- Phase 4 retriever/state_view/hardening: all passing
- Phase 5A trace_summary/curator/proposal/tools/safety: all passing
- Phase 5B execution_summary: all passing
- Phase 5C curator_dry_run: all passing
- Phase 5D auto_proposal: all passing

### Cross-Cut Regression Suites

| Suite | Tests | Result |
|-------|-------|--------|
| tests/capabilities/ | 1,032 | PASSED |
| Cross-cut (core/skills/agents/logging) | 537 | PASSED |

### Full Suite

```
3,327 passed, 13 failed, 11 skipped
```

### Pre-Existing Failures (13 total)

| # | Test file | Failures | Root Cause |
|---|-----------|----------|------------|
| 1-9 | `test_brain_tools.py` | 9 | Pre-existing tool loop / shell policy test issues |
| 10-11 | `test_audit_logging.py` | 2 | Pre-existing browser_guard audit test issues |
| 12 | `test_chat_tools_centralized.py` | 1 | `list_agents` profile not updated in test (commit `4a45f46`) |
| 13 | `test_import_smoke.py` | 1 | `UnboundLocalError` in `llm_router.py:1075` — variable scoping bug |

All 13 failures are pre-existing and unrelated to the capability system. Zero capability system regressions.

---

## 2. Runtime Import Audit

**Finding: CLEAN**

Only two files import from `src.capabilities/` outside the package itself:

| File | Imports | Status |
|------|---------|--------|
| `src/tools/capability_tools.py` | CapabilityStore, CapabilityIndex, CapabilityLifecycleManager, schema enums, parser, curator, proposal, evaluator | ALLOWED — tool implementation |
| `src/app/container.py` | CapabilityIndex, CapabilityStore, CapabilityEvaluator, CapabilityPolicy, PromotionPlanner, CapabilityLifecycleManager, CapabilityRetriever, TraceSummaryObserver, CuratorDryRunAdapter, AutoProposalAdapter | ALLOWED — wiring/DI |

**Verified NOT importing from src.capabilities:**
- Brain — no direct import (accesses via `_capability_store`, `_capability_index`, `_capability_lifecycle` stored attributes)
- TaskRuntime — no import (uses protocol-based observers set via `set_*_observer()` methods)
- StateViewBuilder — no import (duck-typed `_capability_retriever` attribute set by container)
- SkillExecutor — no import
- ToolDispatcher — no import
- AgentRegistry — no import
- AgentPolicy — no import
- Dynamic agent runtime paths — no import

---

## 3. Feature Flag Matrix (Detailed)

| Flag | Default | Owner Intent | Enables | Must NOT Enable | Registers Tools | Grants Permissions | Mutates Files | Affects StateView | Touches TaskRuntime |
|------|---------|-------------|---------|-----------------|-----------------|-------------------|---------------|-------------------|---------------------|
| `enabled` | `false` | Master kill-switch | All capability features | — | Yes (3 read-only tools) | No | No | No | No |
| `retrieval_enabled` | `false` | Progressive disclosure | Retriever → StateView | Tool reg, mutation | No | No | No | Yes (compact summaries) | No |
| `curator_enabled` | `false` | Manual curation | reflect_experience, propose_capability tools | Auto-curation | Yes (2 curator tools) | No | Only with apply=true | No | No |
| `lifecycle_tools_enabled` | `false` | Operator-controlled transitions | evaluate/plan/transition tools | Auto-promotion | Yes (3 lifecycle tools) | No (requires operator profile) | Yes (transition_capability) | No | No |
| `execution_summary_enabled` | `false` | Task-end trace capture | Sanitized summary capture | Persistence, curator calls | No | No | No (in-memory) | No | Yes (observer call) |
| `curator_dry_run_enabled` | `false` | Task-end dry-run | In-memory curator decision | Proposal creation | No | No | No (in-memory) | No | Yes (observer call) |
| `auto_proposal_enabled` | `false` | Controlled auto-proposal | Proposal file persistence | Drafts, index, promotion | No | No | Yes (proposal files only) | No | Yes (observer call) |
| `auto_draft_enabled` | `false` | Legacy (superseded) | — | — | No | No | No | No | No |
| `auto_proposal_min_confidence` | `0.75` | Quality gate | Confidence threshold | — | No | No | No | No | No |
| `auto_proposal_allow_high_risk` | `false` | Safety gate | Allow high-risk auto | — | No | No | No | No | No |
| `auto_proposal_max_per_session` | `3` | Rate limit | Max proposals/session | — | No | No | No | No | No |
| `auto_proposal_dedupe_window_hours` | `24` | Dedup | Dedup window | — | No | No | No | No | No |
| `data_dir` | `data/capabilities` | Data root | Store location | — | No | No | No | No | No |
| `index_db_path` | `data/capabilities/capability_index.sqlite` | Index location | SQLite path | — | No | No | No | No | No |

**Defaults are conservative:** All boolean capability flags default to `false`.

---

## 4. Tool Surface Matrix (Detailed)

### Read-only Tools

| Field | `list_capabilities` | `search_capability` | `view_capability` |
|-------|-------------------|--------------------|--------------------|
| Feature flags | `enabled` | `enabled` | `enabled` |
| Capability tag | `capability_read` | `capability_read` | `capability_read` |
| RuntimeProfile | Standard | Standard | Standard |
| risk_level | low | low | low |
| Mutates store | no | no | no |
| Mutates index | no | no | no |
| Writes proposals | no | no | no |
| Creates draft | no | no | no |
| Can promote | no | no | no |
| Can execute scripts | no | no | no |
| Can call run_capability | no | no | no |

### Lifecycle Tools

| Field | `evaluate_capability` | `plan_capability_transition` | `transition_capability` |
|-------|----------------------|----------------------------|------------------------|
| Feature flags | `enabled` + `lifecycle_tools_enabled` | `enabled` + `lifecycle_tools_enabled` | `enabled` + `lifecycle_tools_enabled` |
| Capability tag | `capability_lifecycle` | `capability_lifecycle` | `capability_lifecycle` |
| RuntimeProfile | Operator | Operator | Operator |
| risk_level | low | low | medium |
| Mutates store | no (optional eval record) | no | yes |
| Mutates index | no | no | yes (refresh) |
| Writes proposals | no | no | no |
| Creates draft | no | no | no |
| Can promote | no | no | yes (controlled) |
| Can execute scripts | no | no | no |
| Can call run_capability | no | no | no |

### Curator Tools

| Field | `reflect_experience` | `propose_capability` |
|-------|---------------------|---------------------|
| Feature flags | `enabled` + `curator_enabled` | `enabled` + `curator_enabled` |
| Capability tag | `capability_curator` | `capability_curator` |
| RuntimeProfile | Operator | Operator |
| risk_level | low | medium |
| Mutates store | no | only with apply=true |
| Mutates index | no | only with apply=true |
| Writes proposals | no | yes |
| Creates draft | no | only with apply=true |
| Can promote | no | no (draft only) |
| Can execute scripts | no | no |
| Can call run_capability | no | no |

### Forbidden Tools — Verified Absent

`run_capability`, `execute_capability`, `auto_propose_capability`, `task_end_curator`, `create_capability`, `install_capability`, `patch_capability`, `auto_promote_capability` — **none exist in codebase** (confirmed by grep for quoted tool names in all `src/**/*.py` files).

---

## 5. Mutation Path Audit

### Allowed Write Paths

| Path | Trigger | What It Writes | Gates |
|------|---------|---------------|-------|
| `CapabilityStore.create_draft` | `propose_capability(apply=true)` or direct call | CAPABILITY.md, manifest.json, standard dirs | FileExistsError on collision |
| `CapabilityStore.disable` | `transition_capability(target=disabled)` | manifest.json (status update) | LifecycleManager checks |
| `CapabilityStore.archive` | `transition_capability(target=archived)` | manifest.json + directory move to archived/ | LifecycleManager checks + timestamp collision handling |
| `CapabilityStore.rebuild_index` | Explicit tool call | SQLite FTS index rebuild | N/A (read from store) |
| `CapabilityLifecycleManager.apply_transition` (maturity) | `transition_capability` | Version snapshot + manifest update + index refresh + mutation log | Planner → Policy → Evaluator triple gate |
| `CapabilityLifecycleManager.apply_transition` (status) | `transition_capability(target=disabled\|archived)` | Version snapshot + delegate to store | Plan → Policy double gate |
| `persist_proposal` | `propose_capability(apply=false)` or `AutoProposalAdapter` | proposal.json, PROPOSAL.md, source_trace_summary.json | FileExistsError on collision |
| `persist_proposal` + `store.create_draft` | `propose_capability(apply=true)` | All of the above + draft capability | High risk requires approval object |
| `AutoProposalAdapter.capture` | Task end (auto) | Proposal files only (via persist_proposal) | 9 gates: should_create, action, confidence, risk, boundary, verification, secrets, rate-limit, dedup |

### Read-Only Paths (No Mutation)

- CapabilityRetriever.retrieve / summarize
- ExperienceCurator.should_reflect / summarize
- CuratorDryRunAdapter.capture (in-memory)
- TraceSummaryObserver.capture (in-memory)
- All read-only tools
- evaluate_capability (optional eval record is idempotent, not mutation)
- plan_capability_transition (pure preview)
- transition_capability(dry_run=true) (pure preview)
- StateView / StateViewBuilder (read summaries only)

### Confirmed: No Hidden Write Paths

- No writes from retriever, ranker, curator (non-proposal), dry-run adapter, execution summary observer
- No writes from StateView, StateViewBuilder, Brain, TaskRuntime, SkillExecutor, ToolDispatcher
- Auto-proposal never calls create_draft, never touches index, never runs evaluator, never promotes

---

## 6. Data Directory Audit

### Verified Safe

- **Path traversal:** `proposed_id` validated in `ExperienceCurator.propose_capability` — rejects `..`, `/`, `\`. Store paths always `data_dir / scope.value / cap_id` with enum-validated scope.
- **Archive path safe:** `archived/<scope>/<cap_id>` or `archived/<scope>/<cap_id>_<timestamp>` for collisions.
- **Snapshots path safe:** Written under capability directory's `versions/` subdirectory.
- **No writes outside data root:** All paths derived from configured `data_dir`. No symlink following, no absolute path injection.
- **Tests use tmp_path:** All capability tests use `tmp_path` fixtures. No test writes to real user data directories.

---

## 7. StateView / Prompt Injection Audit

### Verified Safe

- **Capability summaries are compact:** `CapabilitySummary` dataclass in `state_view.py` contains only: id, name, description, type, scope, maturity, risk_level, triggers, required_tools, match_reason.
- **No full CAPABILITY.md body injected:** `CapabilityRetriever.summarize()` deliberately excludes body, procedure, scripts, traces, evals, and version contents.
- **No Procedure injected:** Procedure section from CAPABILITY.md is part of the body, which is excluded from summaries.
- **No script/test/example/eval/trace/version contents:** `_list_files()` returns file names only, never file contents.
- **Malicious CAPABILITY.md text treated as data:** The `description` field is stored as a plain string. It is never interpreted as instructions. StateView renders it as a reference, not an instruction.
- **Summary section frames capabilities as references:** The `CapabilitySummary` docstring says "reference hint, not an instruction."
- **Prompt-size bound top-k is enforced:** `DEFAULT_MAX_RESULTS = 5`, capped by `context.max_results`.
- **Retrieval failures fail closed:** All exceptions in `retrieve()`, `filter_candidates()`, `rank_candidates()` return empty lists.

---

## 8. Safety and Privacy Audit

### Secrets Redaction

| Pattern | Replacement | Location |
|---------|------------|----------|
| `sk-[a-zA-Z0-9]{20,}` | `sk-<REDACTED>` | `TraceSummary._SECRET_PATTERNS` |
| `API_KEY=...` | `API_KEY=<REDACTED>` | `TraceSummary._SECRET_PATTERNS` |
| `Authorization: Bearer ...` | `Authorization: Bearer <REDACTED>` | `TraceSummary._SECRET_PATTERNS` |
| `password=...` | `password=<REDACTED>` | `TraceSummary._SECRET_PATTERNS` |
| `-----BEGIN PRIVATE KEY-----...` | `-----BEGIN PRIVATE KEY----- <REDACTED> -----END PRIVATE KEY-----` | `TraceSummary._SECRET_PATTERNS` |

Defense-in-depth: `AutoProposalAdapter._summary_contains_unredacted_secrets()` re-checks before persistence.

### CoT / Internal Fields Dropped

`TraceSummary._DROP_KEYS` drops: `_cot`, `_chain_of_thought`, `chain_of_thought`, `_internal`, `_reasoning`, `_thinking`, `reasoning_trace`, `scratchpad`, `hidden_thoughts`, `internal_notes`. Also drops any key starting with `__`.

### Long Content Truncation

- String fields: 50,000 chars (`_MAX_STR_LEN`)
- Commands: 2,000 chars
- File paths: 1,000 chars

### Inert String Fields

- `files_touched` — stored as plain strings, never interpreted as paths by capability code
- `commands_run` — stored as plain strings, never executed by capability code

### No Shell Execution

- Curator: deterministic heuristics only, no shell
- Proposal: filesystem writes only, no shell
- Auto-proposal: filesystem writes only, no shell
- Retriever: index queries only, no shell
- Policy/Evaluator/Planner: pure computation, no shell

### No Network / LLM Access

- All retriever, curator, policy, evaluator, planner, ranker code is deterministic
- No HTTP calls, no LLM API calls, no embeddings
- No network access in any capability path

### No Raw Transcript Persistence

- Execution summary observer sanitizes before in-memory storage
- Proposals persist only curated, sanitized data
- `source_trace_summary.json` in proposal dirs is already sanitized

---

## 9. E2E Smoke Test Coverage

All five required flows are covered by existing tests. Summary of coverage:

### Flow A: Read-Only Capability Discovery

Covered by: `test_phase2b_tools.py`, `test_phase2_store.py`, `test_phase2_search.py`
- `list_capabilities` returns compact summaries
- `search_capability` finds by keyword and filters
- `view_capability` returns manifest + optional body/files
- No mutation occurs

### Flow B: Lifecycle Operator Transition

Covered by: `test_phase3b_lifecycle.py`, `test_phase3c_lifecycle_tools.py`, `test_phase3b_transition_atomicity.py`
- draft → testing → stable transitions
- Snapshot written before mutation
- Index refreshed after mutation
- Failed transitions produce zero file changes
- No script execution

### Flow C: Retrieval into StateView

Covered by: `test_phase4_retriever.py`, `test_phase4_state_view.py`
- Stable fixture capabilities retrieved
- StateView receives compact summaries only
- Full body, procedures, scripts absent from summaries
- Retrieval failures return empty lists

### Flow D: Manual Proposal

Covered by: `test_phase5a_tools.py`, `test_phase5a_proposal.py`
- `reflect_experience` returns decision without mutation
- `propose_capability(apply=false)` writes proposal files only
- `propose_capability(apply=true)` creates draft only (never stable)
- High-risk proposals require approval

### Flow E: Task-End Observer Chain

Covered by: `test_phase5b_execution_summary.py`, `test_phase5c_curator_dry_run.py`, `test_phase5d_auto_proposal.py`
- Execution summary captured when flag enabled
- Curator dry-run creates in-memory decision
- Auto-proposal persists proposal only when all 9 gates pass
- No draft, index, or lifecycle mutation in auto path

---

## 10. Test Suite Consolidation

### Duplicated Helpers Identified

| Helper | Appears In | Consolidation Safe? |
|--------|-----------|---------------------|
| `_make_store` | 6 files | Yes — identical implementations |
| `_make_manifest` | 5 files | Yes — similar signatures |
| `_make_lifecycle` | 4 files | Partially — slightly different kwargs |
| `_create_cap` | 4 files | Partially — different default params |
| `_write_capability_dir` | 3 files | No — different signatures/behavior |
| `_make_doc` | 3 files | No — different return types |

### Decision: Document, Do Not Refactor

No conftest.py consolidation performed in this PR. Reasoning:
- 28 test files with cross-dependencies would require extensive verification
- "Do not weaken tests" constraint means every moved helper must be verified
- Risk of breaking subtle test assumptions outweighs DRY benefit
- Recommended for a dedicated test-cleanup PR

---

## 11. Rollback Notes

To roll back any audit-related changes:
```bash
git checkout HEAD -- docs/capability_system_overview.md
git checkout HEAD -- docs/capability_acceptance_index.md
git checkout HEAD -- docs/capability_consolidation_acceptance.md
```

No source code was modified. No behavior was changed. This is a documentation-only audit.

---

## 12. Summary

| Audit Category | Result |
|---------------|--------|
| Capability tests | 1,032 passed, 0 failed |
| Cross-cut regression tests | 537 passed |
| Runtime import boundaries | CLEAN — only container.py + capability_tools.py |
| Feature flag defaults | All conservative (false) |
| Tool surface | 8 allowed tools present, 8 forbidden tools absent |
| Mutation paths | All documented, no hidden write paths |
| Data directory safety | Path traversal blocked, tests use tmp_path |
| StateView safety | Compact summaries, no body/procedure/scripts injected |
| Privacy/Secrets | Redaction + CoT stripping + truncation verified |
| Shell execution in capability paths | None |
| Network/LLM in capability paths | None |
| Pre-existing failures | 12 documented, all pre-date capability system |
| New regressions | 0 |
