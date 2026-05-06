# Capability Phase 5C Acceptance — Curator Dry-Run Observer

**Date:** 2026-05-02
**Status:** Accepted
**Tests:** 2,264 passed, 12 failed (all pre-existing), 1 skipped

## Test Results

### Phase 5C-specific tests

```
tests/capabilities/test_phase5c_curator_dry_run.py — 49 passed, 0 failed
```

Breakdown:
- Feature flag tests: 4 passed
- Feature flag behavior matrix tests: 4 passed
- CuratorDryRunResult tests: 4 passed
- CuratorDryRunObserver protocol tests: 2 passed
- CuratorDryRunAdapter tests: 10 passed (simple chat, tool-heavy, failed-then-succeeded, user-requested-reuse, shell-workflow, generalization boundary, deterministic, invalid input, empty dict, no propose_capability)
- TaskRuntime dry-run behavior tests: 5 passed
- No-mutation tests: 7 passed
- Safety tests: 9 passed
- Import hygiene tests: 2 passed
- No tool registration tests: 2 passed

### Full cross-cut regression suites

| Suite | Tests | Result |
|-------|-------|--------|
| tests/capabilities/ | 954 | PASSED |
| tests/core/ | 1,006 | PASSED (9 pre-existing in test_brain_tools, 2 in test_audit_logging, 1 in test_chat_tools_centralized) |
| tests/skills/ | 64 | PASSED |
| tests/agents/ | 208 | PASSED |
| tests/logging/ | 57 | PASSED |
| **Total** | **2,264** | **PASSED (12 pre-existing failures)** |

### Pre-existing failures (NOT Phase 5C regressions)

Same 12 failures from Phase 5B baseline:

| Test file | Failures | Root cause |
|-----------|----------|------------|
| `test_brain_tools.py` | 9 | Pre-existing tool loop / shell policy test issues |
| `test_audit_logging.py` | 2 | Pre-existing browser_guard audit test issues |
| `test_chat_tools_centralized.py` | 1 | `list_agents` added to profile in commit `4a45f46`, test not updated |

All 12 failures are pre-existing — verified by Phase 5B acceptance pass on base commit `aa19a32`.

## Files Created

- `src/capabilities/curator_dry_run_adapter.py` — CuratorDryRunAdapter (concrete observer)
- `tests/capabilities/test_phase5c_curator_dry_run.py` — 49 tests
- `docs/capability_phase5c_acceptance.md` — this document

## Files Modified (Phase 5C)

- `src/config/settings.py` — added `curator_dry_run_enabled: bool = False` to CapabilitiesConfig + env var mapping
- `config/settings.py` — added `CAPABILITIES_CURATOR_DRY_RUN_ENABLED` compat shim export
- `src/core/execution_summary.py` — added CuratorDryRunResult dataclass, CuratorDryRunObserver protocol, updated module docstring
- `src/core/task_runtime.py` — added `_curator_dry_run_observer` and `_last_curator_decision` attributes, `set_curator_dry_run_observer()` setter, curator dry-run call in finally block
- `src/app/container.py` — Phase 5C observer wiring behind `curator_dry_run_enabled` flag
- `docs/capability_evolution_architecture.md` — added Section 15: Phase 5C
- `tests/capabilities/test_phase4_hardening.py` — allowed `curator_dry_run_adapter.py` in ExperienceCurator file check
- `tests/capabilities/test_phase5b_execution_summary.py` — updated `test_execution_summary_module_has_no_curator_import` to check for actual imports rather than docstring mentions

## Feature Flag Matrix

| capabilities.enabled | execution_summary_enabled | curator_dry_run_enabled | Behavior |
|---------------------|--------------------------|------------------------|----------|
| false | * | * | No observers wired. No summary. No decision. |
| true | false | false | No observers wired. No behavior change. |
| true | false | true | Curator observer wired but no summary → fail-closed, no decision. |
| true | true | false | Summary captured. No curator dry-run. |
| true | true | true | Summary + curator dry-run. In-memory decision populated. |

All flags default to `False` (verified in tests).

Feature flags are independent:
- `curator_dry_run_enabled` does NOT imply `curator_enabled`
- `curator_dry_run_enabled` does NOT imply `execution_summary_enabled`
- `curator_dry_run_enabled` does NOT imply `lifecycle_tools_enabled`
- `curator_dry_run_enabled` does NOT imply `retrieval_enabled`
- `curator_dry_run_enabled` does NOT grant any capability tags to any profile
- `curator_dry_run_enabled` does NOT register any tools in the tool registry

## CuratorDryRunResult Schema (`src/core/execution_summary.py`)

@dataclass with 15 fields. No dependency on src.capabilities.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| trace_id | str | (required) | Task iteration ID |
| should_create | bool | False | Whether proposal creation is recommended |
| recommended_action | str | "no_action" | e.g. create_skill_draft, create_workflow_draft, no_action |
| confidence | float | 0.0 | Confidence score 0.0–1.0 |
| reasons | list[str] | [] | Reason strings for the decision |
| risk_level | str | "low" | low / medium / high |
| required_approval | bool | False | Whether human approval is recommended |
| generalization_boundary | str | "" | When should_create: boundary of applicability |
| suggested_capability_type | str | "skill" | skill / workflow / project_playbook |
| suggested_triggers | list[str] | [] | Trigger keywords for capability matching |
| suggested_tags | list[str] | [] | Tags for categorization |
| created_at | str | now() | ISO 8601 timestamp |
| source | str | "dry_run" | Always "dry_run" |
| persisted | bool | False | Always False |

## CuratorDryRunAdapter Behavior

### Adapter (`src/capabilities/curator_dry_run_adapter.py`)

1. Receives sanitized summary dict from Phase 5B observer
2. Converts to TraceSummary via TraceSummary.from_dict() (idempotent second sanitization pass)
3. Calls ExperienceCurator.should_reflect(trace) → CuratorDecision
4. If should_create=True: calls ExperienceCurator.summarize(trace) → CuratedExperience for generalization_boundary, triggers, tags
5. If should_create=False: skips summarize(), uses defaults for generalization fields
6. Builds CuratorDryRunResult, returns result.to_dict()
7. On any exception: logs debug, returns None

Does NOT:
- Call ExperienceCurator.propose_capability()
- Access CapabilityStore
- Access CapabilityIndex
- Access CapabilityLifecycleManager
- Write files
- Execute commands
- Make network calls
- Call any LLM

### TaskRuntime Integration

```python
# Phase 5C: curator dry-run (best-effort, failure-safe).
# Only runs when a sanitized summary was captured.
if self._curator_dry_run_observer is not None and self._last_execution_summary is not None:
    try:
        self._last_curator_decision = await self._curator_dry_run_observer.capture(
            self._last_execution_summary
        )
    except Exception:
        logger.debug("Curator dry-run observer failed", exc_info=True)
```

Key behavioral properties:
- Observer called in `finally` block, after Phase 5B summary observer
- Observer called **at most once** per `complete_chat()` invocation
- Only called when `_last_execution_summary` exists (fail-closed: no summary → no call)
- Observer failure is swallowed/logged; never changes user response
- Observer failure never erases `_last_execution_summary`
- No extra tool calls made by observer
- No additional model calls made by observer
- User response is identical with curator dry-run enabled vs disabled

**Failed task behavior:** When `complete_chat` raises, the finally block still runs both observers (Phase 5B then Phase 5C). If Phase 5B captured a summary, Phase 5C runs on it. If Phase 5B returned None (observer failed), Phase 5C is skipped. The original exception is always re-raised.

## Safety / Sanitization

### Input sanitization (inherited from Phase 5B)
- [x] Input to curator is sanitized TraceSummary dict (already passed through TraceSummary.sanitize())
- [x] Second pass via TraceSummary.from_dict() is idempotent — _DROP_KEYS and _SECRET_PATTERNS won't double-redact
- [x] API keys redacted before reaching curator
- [x] Bearer tokens redacted
- [x] Password values redacted
- [x] PEM private keys redacted
- [x] Hidden CoT fields dropped before reaching curator

### Phase 5C additional safety
- [x] Curator handles "contains sensitive secrets" by returning no_action (double-check after sanitization)
- [x] commands_run stored as inert strings, never executed
- [x] files_touched stored as inert strings, never opened
- [x] Prompt injection text treated as data, not executed
- [x] CuratorDryRunAdapter.capture() has no network/shell/file access
- [x] Adapter has no subprocess, urllib, httpx, requests, aiohttp, socket imports
- [x] Adapter has no LLM call capability (no anthropic, openai, llm_router references)
- [x] Observer returns None on any exception (failure-safe)
- [x] Decision is in-memory only (`_last_curator_decision` dict)

## No-Auto-Curation Guarantees

Verified by test and source inspection:
- [x] `src/core/execution_summary.py` does not import from src.capabilities
- [x] `src/capabilities/curator_dry_run_adapter.py` does not call propose_capability
- [x] `src/capabilities/curator_dry_run_adapter.py` does not access CapabilityStore
- [x] `src/capabilities/curator_dry_run_adapter.py` does not access CapabilityIndex
- [x] `src/capabilities/curator_dry_run_adapter.py` does not access CapabilityLifecycleManager
- [x] `src/capabilities/curator_dry_run_adapter.py` does not call create_draft
- [x] `src/core/task_runtime.py` does not import from src.capabilities
- [x] TaskRuntime does not call ExperienceCurator
- [x] TaskRuntime does not call reflect_experience
- [x] TaskRuntime does not call propose_capability
- [x] TaskRuntime does not call CapabilityStore.create_draft
- [x] TaskRuntime does not call CapabilityLifecycleManager
- [x] TaskRuntime does not write proposal files
- [x] TaskRuntime does not write capability directories
- [x] TaskRuntime does not update CapabilityIndex
- [x] TaskRuntime does not create EvalRecords
- [x] TaskRuntime does not write version snapshots
- [x] No proposal directory created by observer
- [x] No draft capability directory created by observer
- [x] No CapabilityStore.create_draft called by observer
- [x] No CapabilityIndex update by observer
- [x] No promotion/lifecycle transition by observer
- [x] No run_capability anywhere in src/
- [x] No automatic memory write introduced
- [x] No automatic promotion introduced

## Runtime Import Check

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Results:
- `src/tools/capability_tools.py` — allowed (Phase 2B/3C/5A tools)
- `src/app/container.py` — allowed (wiring, including TraceSummaryObserver and CuratorDryRunAdapter imports)
- **No other files** import from src.capabilities

Additional checks:
- `src/core/execution_summary.py` — zero capability imports (generic core interface with CuratorDryRunResult + CuratorDryRunObserver)
- `src/core/task_runtime.py` — only imports from `src.core.execution_summary` (not src.capabilities)
- `src/core/brain.py` — zero capability references (clean)
- `src/core/state_view_builder.py` — zero capability references (clean)
- SkillExecutor — zero capability references
- ToolDispatcher — zero capability references
- AgentRegistry execution paths — zero capability references
- AgentPolicy execution paths — zero capability references

## Storage Behavior

Phase 5C does NOT persist decisions:
- [x] No files written to `data/capabilities/proposals/`
- [x] No files written to `data/capabilities/<scope>/<capability_id>/`
- [x] No eval records created
- [x] No version snapshots created
- [x] Decision stored in-memory only (`task_runtime._last_curator_decision`)
- [x] Decision is a plain dict (CuratorDryRunResult.to_dict())
- [x] Decision available for debug inspection, not user-facing
- [x] No MutationLog entries for curator dry-run (no new MutationType)
- [x] No debug/trace sink configured
- [x] `source` always "dry_run"
- [x] `persisted` always false

## Capability Tool Regression

- [x] read-only tools remain read-only (capability_read unchanged)
- [x] Lifecycle tools remain gated by `lifecycle_tools_enabled`
- [x] Curator tools remain gated by `curator_enabled`
- [x] `curator_dry_run_enabled` does NOT register any tools
- [x] `curator_dry_run_enabled` does NOT grant `capability_read`
- [x] `curator_dry_run_enabled` does NOT grant `capability_lifecycle`
- [x] `curator_dry_run_enabled` does NOT grant `capability_curator`
- [x] No `run_capability` exists
- [x] No `execution_summary` capability tag on any tool
- [x] No `curator_dry_run` capability tag on any tool
- [x] `capability_tools.py` has zero references to `curator_dry_run`
- [x] `runtime_profiles.py` has zero references to `curator_dry_run`

## Hard Constraint Verification

- [x] Do not create proposals automatically
- [x] Do not create draft capabilities automatically
- [x] Do not persist CuratedExperience by default
- [x] Do not call propose_capability from TaskRuntime
- [x] Do not call CapabilityStore from TaskRuntime
- [x] Do not update CapabilityIndex
- [x] Do not write EvalRecords
- [x] Do not write version snapshots
- [x] Do not write memories
- [x] Do not implement run_capability
- [x] Do not execute scripts
- [x] Do not run shell
- [x] Do not use network
- [x] Do not use LLM judge
- [x] Do not modify existing promote_skill
- [x] Do not modify dynamic agents
- [x] Do not change user-facing response semantics
- [x] TaskRuntime must not import from src.capabilities

## Existing Behavior Regression

- [x] Phase 0/1 tests pass
- [x] Phase 2A tests pass
- [x] Phase 2B tests pass
- [x] Phase 3A/B/C tests pass
- [x] Phase 4 tests pass
- [x] Phase 5A tests pass
- [x] Phase 5B tests pass
- [x] Old skills list/read/run/promote unchanged
- [x] Dynamic agents unchanged
- [x] ToolDispatcher permission checks unchanged
- [x] RuntimeProfile behavior unchanged
- [x] MutationLog existing enum values unchanged (no new MutationType values)
- [x] StateView capability summaries unchanged
- [x] Manual curator tools unchanged
- [x] Proposal apply=false/apply=true behavior unchanged

## Curator Decision Correctness

Verified by end-to-end adapter tests:
- [x] Simple chat (no tools, no commands) → `should_create=False`, `no_action`
- [x] Tool-heavy task (5+ tools) → `should_create=True`, `draft` in action
- [x] Failed-then-succeeded pattern → `should_create=True`
- [x] User explicitly requested reuse → `should_create=True`, confidence ≥ 0.7
- [x] Shell/file workflow → `risk_level` is `medium` or `high`
- [x] `generalization_boundary` populated when `should_create=True`
- [x] Same sanitized summary → same decision (deterministic, except `created_at` timestamp)
- [x] Invalid input → returns `None`
- [x] Empty dict → returns `None`
- [x] Contains secrets → `should_create=False` (sensitive secrets gate)

## Phase 5C Hardening Pass (2026-05-02)

### Hardening Scope

Second verification pass before Phase 5D. All Phase 5C code unchanged since original acceptance — this pass verifies properties, not fixes bugs.

### Test Results (Hardening Re-run)

```
tests/capabilities/test_phase5c_curator_dry_run.py — 49 passed, 0 failed
```

All 954 capability tests pass. Full cross-cut regression:

| Suite | Tests | Result |
|-------|-------|--------|
| tests/capabilities/ | 954 | PASSED |
| tests/core/test_task_runtime.py | 15 | PASSED |
| tests/core/test_state_view.py | 25 | PASSED |
| tests/skills/ | 64 | PASSED |
| tests/agents/ | 208 | PASSED |
| tests/logging/ | 57 | PASSED |
| Full `tests/` | 3,249 | PASSED (13 failed, 11 skipped) |

### Failures After Hardening

13 failures total — all verified pre-existing:

| Test file | Failures | Root cause |
|-----------|----------|------------|
| `test_brain_tools.py` | 9 | Pre-existing tool loop / shell policy test issues |
| `test_audit_logging.py` | 2 | Pre-existing browser_guard audit test issues |
| `test_chat_tools_centralized.py` | 1 | `list_agents` added to profile in commit `4a45f46`, test not updated |
| `test_import_smoke.py` | 1 | `UnboundLocalError` in `llm_router.py:1075` — pre-existing, reproduces on `aa19a32` |

All 13 failures reproduce identically on base commit `aa19a32` (verified via `git stash` + test run).

### Hardening Verification Results

#### 1. In-Memory-Only (VERIFIED)

- [x] `_last_curator_decision` is a plain dict on TaskRuntime, never persisted
- [x] No files written to `data/capabilities/` (directory does not exist)
- [x] No files written to `data/capabilities/proposals/`
- [x] Decision replaced per `complete_chat()` call (direct assignment, not accumulated)
- [x] Decision never enters StateView
- [x] Decision never enters final user response (reply returned before finally block)
- [x] Decision never enters capability index
- [x] Decision never enters proposal store
- [x] No unbounded history — one slot, overwritten each turn

#### 2. No-Mutation (VERIFIED)

- [x] `data/capabilities/` directory does not exist
- [x] No CapabilityStore.create_draft called (adapter grep: only comment matches)
- [x] No CapabilityIndex update (adapter has no index import)
- [x] No EvalRecord written (no eval_records import in adapter)
- [x] No version snapshot written (no file write operations)
- [x] No MutationLog capability mutation (no new MutationType values, grep clean)
- [x] No memory write (TaskRuntime has no memory access path for curator)
- [x] No lifecycle transition (adapter has no lifecycle import)
- [x] No proposal persistence (adapter does not call propose_capability)
- [x] No draft capability creation (adapter does not call create_draft)
- [x] No promotion (no PromotionPlanner import in adapter)
- [x] No run_capability (grep: zero references in entire src/)

#### 3. Import Hygiene (VERIFIED)

Runtime import grep result:
```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Only allowed imports:
- `src/tools/capability_tools.py` — Phase 2B/3C/5A tools (allowed)
- `src/app/container.py` — Phase 5B TraceSummaryObserver + Phase 5C CuratorDryRunAdapter wiring (allowed)

Clean files (zero capabilities imports):
- `src/core/task_runtime.py`
- `src/core/brain.py`
- `src/core/execution_summary.py`
- `src/core/state_view_builder.py`
- SkillExecutor
- ToolDispatcher
- AgentRegistry execution paths
- AgentPolicy execution paths
- Dynamic agent runtime paths

#### 4. Feature Flag Independence (VERIFIED)

- [x] `curator_dry_run_enabled` defaults to `False`
- [x] `curator_dry_run_enabled` does NOT imply `curator_enabled`
- [x] `curator_dry_run_enabled` does NOT imply `execution_summary_enabled`
- [x] `curator_dry_run_enabled` does NOT imply `lifecycle_tools_enabled`
- [x] `curator_dry_run_enabled` does NOT imply `retrieval_enabled`
- [x] Compat shim exported at `config/settings.py:143`

#### 5. Tool / Permission Isolation (VERIFIED)

- [x] `curator_dry_run_enabled` registers zero tools
- [x] `curator_dry_run_enabled` grants no permissions
- [x] `curator_dry_run_enabled` does NOT grant `capability_read`
- [x] `curator_dry_run_enabled` does NOT grant `capability_lifecycle`
- [x] `curator_dry_run_enabled` does NOT grant `capability_curator`
- [x] `capability_tools.py` has zero references to `curator_dry_run`
- [x] `runtime_profiles.py` has zero references to `curator_dry_run`
- [x] No `run_capability` exists anywhere
- [x] No `auto_propose_capability` tool exists
- [x] No `task_end_curator` tool exists

#### 6. Safety / Sanitization (VERIFIED)

- [x] API keys redacted via `_SECRET_PATTERNS` (sk-*, API_KEY=, Bearer, password=, PEM)
- [x] CoT/hidden reasoning fields dropped via `_DROP_KEYS` (9 fields: _cot, chain_of_thought, reasoning, scratchpad, etc.)
- [x] Second sanitization pass via `TraceSummary.from_dict()` is idempotent
- [x] Prompt injection text treated as data, not executed
- [x] commands_run stored as inert strings, never executed
- [x] files_touched stored as inert strings, never opened
- [x] Long tool outputs truncated via `_truncate()`
- [x] Adapter has no network imports (no subprocess, urllib, httpx, requests, aiohttp, socket)
- [x] Adapter has no LLM imports (no anthropic, openai, llm_router)
- [x] Adapter returns None on any exception (failure-safe)

#### 7. TaskRuntime Behavior (VERIFIED)

- [x] Observer called only when both `_curator_dry_run_observer is not None` AND `_last_execution_summary is not None`
- [x] Observer called at most once per `complete_chat()` invocation
- [x] Observer receives sanitized execution summary (from Phase 5B observer output)
- [x] Observer does not receive hidden reasoning / CoT (dropped by TraceSummary.sanitize())
- [x] Observer failure does not affect user response (finally block, reply already computed)
- [x] Observer failure does not erase `_last_execution_summary` (separate try/except blocks)
- [x] User-facing response identical with dry-run disabled vs enabled (reply returned before observer runs)
- [x] Original task exception behavior unchanged (finally block runs, exception re-raised)
- [x] No extra tool calls caused by dry-run observer
- [x] No extra model calls caused by dry-run observer
- [x] No StateView modification caused by dry-run observer

#### 8. Adapter Behavior (VERIFIED)

- [x] Adapter imports `src.capabilities` (allowed — lives in src/capabilities/)
- [x] TaskRuntime does NOT import `src.capabilities`
- [x] Adapter calls `ExperienceCurator.should_reflect()` and `summarize()` only
- [x] Adapter does NOT call `propose_capability()`
- [x] Adapter does NOT access `CapabilityStore`
- [x] Adapter does NOT access `CapabilityIndex`
- [x] Adapter does NOT access `CapabilityLifecycleManager`
- [x] Adapter does NOT write files
- [x] Adapter returns `None` on exception
- [x] Adapter uses sanitized TraceSummary-derived data only
- [x] `CuratorDryRunResult.source` always `"dry_run"`
- [x] `CuratorDryRunResult.persisted` always `False`

#### 9. Failed Task Behavior (VERIFIED)

When `complete_chat()` raises:
- [x] `finally` block still runs both observers (Phase 5B then Phase 5C)
- [x] If Phase 5B captured a summary, Phase 5C runs on it
- [x] If Phase 5B returned None (observer failed), Phase 5C is skipped (fail-closed)
- [x] Original exception is always re-raised

### Regression Verification

- [x] Phase 0/1 tests pass
- [x] Phase 2A tests pass
- [x] Phase 2B tests pass
- [x] Phase 3A/B/C tests pass
- [x] Phase 4 tests pass
- [x] Phase 5A tests pass
- [x] Phase 5B tests pass
- [x] Old skills list/read/run/promote unchanged
- [x] Dynamic agents unchanged
- [x] ToolDispatcher permission checks unchanged
- [x] RuntimeProfile behavior unchanged
- [x] MutationLog existing enum values unchanged
- [x] StateView capability summaries unchanged
- [x] Manual curator tools unchanged
- [x] Lifecycle tools unchanged
- [x] Retrieval behavior unchanged
- [x] Proposal apply=false/apply=true behavior unchanged

### Hardening Conclusion

All Phase 5C properties verified. No code changes were needed — the original implementation was correct. All 13 test failures are pre-existing and unrelated to Phase 5C. Ready for Phase 5D.

## Known Issues

None specific to Phase 5C.

Pre-existing test failures (13 across 4 test files) are unrelated to Phase 5C — all verified to reproduce identically on the base commit `aa19a32` before any Phase 5 changes. These are tracked separately in the project backlog.

## Rollback Notes

To roll back Phase 5C:
1. Set `capabilities.curator_dry_run_enabled = false` in config.toml (default)
2. Or set `capabilities.enabled = false` in config.toml
3. No code changes needed — all wiring is feature-gated
4. No data to clean up — decisions are in-memory only
5. Curator observer is never wired when flag is false
6. Phase 5B execution summary observer is unaffected
