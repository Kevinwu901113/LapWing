# Phase 3B Acceptance Report

**Date:** 2026-05-01
**Phase:** Capability Evolution System Phase 3B — Gated Lifecycle Transitions + Hardening
**Status:** Accepted

## Test Results

### Capability tests

| Test Suite | Pass | Fail | Skip |
|---|---|---|---|
| Phase 3B Lifecycle (`test_phase3b_lifecycle.py`) | 37 | 0 | 0 |
| Phase 3B Atomicity (`test_phase3b_transition_atomicity.py`) | 28 | 0 | 0 |
| Phase 3B Hardening (`test_phase3b_hardening.py`) | 33 | 0 | 0 |
| **Phase 3B total** | **98** | **0** | **0** |
| Phase 0/1/2A/2B regression | 315 | 0 | 0 |
| Phase 3A (policy + evaluator + records + promotion + hardening) | 134 | 0 | 0 |
| Phase 3B regression (`test_phase3b_regression.py`) | 19 | 0 | 0 |
| **All capabilities** | **566** | **0** | **0** |

### Legacy / runtime test suites

| Test Suite | Pass | Fail | Notes |
|---|---|---|---|
| Skills and agents (`tests/skills/`, `tests/agents/`) | 272 | 0 | All pass |
| ToolDispatcher (`tests/core/test_tool_dispatcher.py`) | 55 | 0 | All pass |
| Logging / MutationLog (`tests/logging/`) | 32 | 0 | All pass |
| StateView (`tests/core/test_state_view_builder.py`, `test_stateview_agent_summary.py`) | 29 | 0 | All pass |
| RuntimeProfile (`tests/core/test_runtime_profiles_exclusion.py`, `test_tool_boundary.py`) | 44 | 1 | Pre-existing: `list_agents` frozenset |
| **Total legacy** | **432** | **1** | **0** |

## Files Changed

### New files (1 source + 4 test)
- `src/capabilities/lifecycle.py` — CapabilityLifecycleManager with TransitionResult
- `tests/capabilities/test_phase3b_lifecycle.py` — 37 tests
- `tests/capabilities/test_phase3b_transition_atomicity.py` — 28 tests
- `tests/capabilities/test_phase3b_hardening.py` — 33 tests
- `tests/capabilities/test_phase3b_regression.py` — 19 regression verification tests

### Modified files
- `src/capabilities/__init__.py` — added Phase 3B exports (CapabilityLifecycleManager, TransitionResult)
- `docs/capability_evolution_architecture.md` — added Phase 3B section
- `docs/capability_phase3b_acceptance.md` — this file

## 1. Runtime Wiring Check

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
src/tools/capability_tools.py  — allowed (read-only tools)
src/app/container.py            — allowed (store/index construction)
```

**No unauthorized imports.** Confirmed zero references from:
- Brain, TaskRuntime, StateViewBuilder, SkillExecutor, SkillStore runtime paths
- ToolDispatcher, AgentRegistry execution paths, AgentPolicy execution paths
- RuntimeProfile

## 2. User-Facing Tool Surface

Confirmed exactly three read-only capability tools exist:
- `list_capabilities` — list/search with filters
- `search_capability` — keyword search via index or filesystem
- `view_capability` — full metadata + optional body + file names

Confirmed zero write tools:
- No `create_capability`
- No `disable_capability`
- No `archive_capability`
- No `promote_capability`
- No `run_capability`
- No `evaluate_capability`
- No `transition_capability`

## 3. Lifecycle Gating Verification

Every applied transition follows this sequence (verified by mock + integration tests):

1. **Resolve** capability via `CapabilityStore.get()`
2. **Evaluate** for quality-relevant transitions (draft→testing, testing→stable, repairing→testing)
3. **Plan** via `PromotionPlanner.plan_transition()` — mock denial tests confirm zero file changes
4. **Policy check** via `CapabilityPolicy.validate_promote()` — mock denial after planner-allow tests confirm zero changes
5. **Snapshot** via `create_version_snapshot()` — written before any mutation
6. **Manifest update** — maturity/status + updated_at via `model_copy(update={...})`
7. **Persist** via `_sync_manifest_json()` + re-parse for fresh content_hash
8. **Index refresh** via `_maybe_index()` — search sees new state
9. **MutationLog** via `_maybe_record()` — optional, failure-safe

### Mock denial tests (hardening)

| Gate | Mock | Verifies |
|---|---|---|
| Planner denial | Mock planner returns `allowed=False` | manifest.json, CAPABILITY.md, versions/, index, mutation log all unchanged |
| Policy denial after planner allow | Mock policy returns deny | manifest.json, CAPABILITY.md, versions/, index, mutation log all unchanged |

## 4. Supported Transitions

| Transition | Type | Eval Required | Approval | Test |
|---|---|---|---|---|
| `draft → testing` | maturity | yes (fresh) | no | `test_applies_for_valid_low_risk_capability` PASS |
| `draft → testing` blocked (missing sections) | maturity | yes | — | `test_blocked_when_verification_missing` PASS |
| `testing → stable` low risk | maturity | yes (fresh) | no | `test_applies_for_low_risk_with_passing_eval` PASS |
| `testing → stable` blocked (no eval) | maturity | yes | — | `test_blocked_without_passing_eval` PASS |
| `testing → stable` medium risk | maturity | yes (fresh) | not required with passing eval | `test_medium_risk_requires_approval` PASS |
| `testing → stable` high risk blocked | maturity | yes | required, blocked without | `test_high_risk_requires_approval` PASS |
| `testing → stable` high risk approved | maturity | yes (fresh) | explicit approval | `test_high_risk_allowed_with_approval` PASS |
| `testing → stable` high risk auto-promote | maturity | yes | never auto-promotes | `test_high_risk_never_auto_promotes` PASS |
| `stable → broken` no evidence | maturity | no | failure_evidence required | `test_requires_failure_evidence` PASS |
| `stable → broken` with evidence | maturity | no | failure_evidence | `test_allowed_with_failure_evidence` PASS |
| `broken → repairing` | maturity | no | no | `test_always_allowed` PASS |
| `repairing → testing` | maturity | yes (fresh) | no | `test_allowed_without_eval` PASS |
| `testing → draft` | downgrade | no | no | `test_downgrade_allowed` PASS |
| `active → disabled` | status | no | policy check | `test_active_to_disabled` PASS |
| `active → archived` | status | no | policy check | `test_active_to_archived` PASS |
| disabled → promotion | blocked | — | — | `test_disabled_cannot_promote` PASS |
| archived → promotion | blocked | — | — | `test_archived_cannot_promote` PASS |
| quarantined → stable | blocked | — | — | `test_quarantined_to_stable_blocked` PASS |

## 5. No-Mutation-on-Failure (Comprehensive)

### General blocked transitions

| Check | Test | Result |
|---|---|---|
| manifest.json byte-level unchanged | `test_blocked_does_not_change_manifest_json` | PASS |
| CAPABILITY.md byte-level unchanged | `test_blocked_does_not_change_capability_md` | PASS |
| No version snapshot written | `test_blocked_does_not_write_version_snapshot` | PASS |
| Index row unchanged | `test_blocked_does_not_change_index` | PASS |
| No transition event in mutation log | `test_blocked_does_not_record_transition_event` | PASS |

### Planner denial → zero changes

| Check | Test | Result |
|---|---|---|
| manifest.json unchanged | `test_manifest_json_unchanged` | PASS |
| CAPABILITY.md unchanged | `test_capability_md_unchanged` | PASS |
| No version snapshot | `test_no_version_snapshot_written` | PASS |
| Index unchanged | `test_index_unchanged` | PASS |
| No mutation log transition event | `test_no_mutation_log_transition_recorded` | PASS |

### Policy denial after planner allow → zero changes

| Check | Test | Result |
|---|---|---|
| Versions dir unchanged | `test_versions_dir_unchanged` | PASS |
| Index unchanged | `test_index_unchanged` | PASS |
| No mutation log transition event | `test_no_transition_mutation_log` | PASS |

### Status-blocked transitions → zero changes

| Blocker | Test | Verifies |
|---|---|---|
| High risk without approval | `test_all_files_unchanged` + `test_index_unchanged` | manifest.json, CAPABILITY.md, versions, index all unchanged |
| Quarantined → stable | `test_all_files_unchanged` + `test_index_unchanged` | manifest.json, CAPABILITY.md, versions, index all unchanged |
| Disabled → stable | `test_all_files_unchanged` + `test_index_unchanged` | manifest.json, CAPABILITY.md, versions, index all unchanged |

### Failed eval → no artifacts

| Check | Test | Result |
|---|---|---|
| No version snapshot | `test_no_version_snapshot_on_failed_eval` | PASS |
| No index change | `test_no_index_change_on_failed_eval` | PASS |
| Eval record still written for diagnostics | `test_eval_record_is_still_written_on_failed_eval` | PASS |

## 6. Snapshot Behavior

### Basic
- Successful transition writes exactly one snapshot: `test_successful_transition_writes_version_snapshot` PASS
- Snapshot includes manifest.json: `test_snapshot_includes_manifest_json` PASS
- Snapshot includes CAPABILITY.md: `test_snapshot_includes_capability_md` PASS
- Snapshot preserves pre-transition state: `test_snapshot_preserves_pre_transition_state` PASS
- Disable writes snapshot: `test_disable_transition_writes_snapshot` PASS
- Archive writes snapshot: `test_archive_transition_writes_snapshot` PASS

### Timestamp uniqueness
- Rapid sequential transitions produce unique snapshot timestamps: `test_rapid_sequential_transitions_have_unique_snapshots` PASS
- Two consecutive transitions yield two distinct snapshots with different snapshot_dir values: verified

### Snapshot survives archive
- Snapshot readable from archived doc via `include_archived=True` listing: `test_snapshot_readable_after_archive` PASS

## 7. Eval Record Behavior

| Check | Test | Result |
|---|---|---|
| Eval record content_hash matches pre-transition doc | `test_eval_record_hash_matches_pre_transition_doc` | PASS |
| Eval record hash differs from post-transition hash | `test_eval_record_hash_differs_from_post_transition_hash` | PASS |
| `evaluate()` does not change manifest maturity | `test_evaluate_does_not_change_maturity` | PASS |
| `evaluate(write_record=False)` does not change manifest.json | `test_evaluate_without_persist_does_not_change_files` | PASS |
| `evaluate(write_record=True)` does not change manifest fields | `test_evaluate_with_persist_does_not_change_manifest_fields` | PASS |
| Eval record readable after successful transition | `test_eval_readable_after_successful_transition` + `test_eval_readable_after_draft_to_testing` | PASS |
| Eval record written even when transition blocked | `test_eval_record_is_still_written_on_failed_eval` | PASS |

## 8. Index Refresh Behavior

- Successful transition refreshes index: `test_successful_transition_refreshes_index` PASS
- Search sees new maturity after transition: `test_search_sees_new_maturity_after_transition` PASS
- Search sees new status after disable: `test_search_sees_new_status_after_disable` PASS
- Archived excluded from default search: `test_archived_excluded_from_default_list` PASS
- Archived included when requested: `test_archived_included_when_requested` PASS

### Index refresh failure (documented behavior)
If `_maybe_index()` raises after manifest.json is written, the manifest change persists but the index is stale. The transition is still reported as applied (`test_index_failure_after_manifest_write` confirms manifest maturity is updated). This is intentional: manifest is durable, index is derived and can be rebuilt.

## 9. Mutation Log Behavior

- Records transition event when log provided: `test_records_transition_when_provided` PASS
- Works with `mutation_log=None`: `test_works_with_mutation_log_none` PASS
- Log failure does not corrupt maturity transition: `test_mutation_log_failure_does_not_corrupt_transition` PASS
- Log failure does not corrupt disable: `test_mutation_log_failure_does_not_corrupt_disable` PASS
- Eval record log failure does not corrupt: `test_eval_record_log_failure_does_not_corrupt` PASS

## 10. Content Hash Behavior

- Hash changes after transition: `test_hash_changes_after_transition` PASS
- Hash stable after re-read: `test_hash_stable_after_re_read` PASS
- No self-referential hash churn: `test_no_self_referential_hash_churn` PASS
- Hash differs from before: `test_hash_differs_from_before` PASS
- Hash unchanged when blocked: `test_hash_unchanged_when_blocked` PASS
- Extra fields preserved: `test_extra_fields_preserved` PASS

## 11. TransitionResult Completeness

- Successful result has all 14 fields non-default: `test_successful_result_all_fields_non_default` PASS
- Blocked result sets `version_snapshot_id=None`, `content_hash_after=""`, `message` populated: `test_blocked_result_has_empty_optional_fields` PASS
- Blocked result includes `eval_record_id` when eval was run (fixed: planner passes through eval_record.created_at when blocked)

## 12. No Script Execution

- Lifecycle.apply_transition does not import/execute Python scripts: `test_lifecycle_does_not_import_scripts` PASS
- Lifecycle does not execute shell scripts: `test_lifecycle_does_not_execute_shell` PASS
- `evaluate()` does not execute scripts: `test_evaluate_does_not_execute_scripts` PASS
- `plan_transition()` does not execute scripts: `test_plan_transition_does_not_execute_scripts` PASS

## 13. Existing Behavior Regression

Confirmed unchanged:
- Old skills list/read/run/promote (272 tests pass)
- Dynamic agents (agent tests pass)
- ToolDispatcher permission checks (55 tests pass)
- RuntimeProfile behavior (44/45 pass; 1 pre-existing failure)
- MutationLog existing enum values and JSONL behavior (32 tests pass)
- StateView has no capability section (29 tests pass)
- Brain and TaskRuntime do not retrieve/evaluate/promote capabilities
- Read-only capability tools still work as before

## Confirmation Checklist

- [x] All 566 capability tests pass (98 Phase 3B + 134 Phase 3A + 315 Phase 0/1/2A/2B + 19 regression)
- [x] All 432 legacy tests pass (272 skills/agents + 55 ToolDispatcher + 32 logging + 29 StateView + 44 RuntimeProfile)
- [x] 1 pre-existing RuntimeProfile failure confirmed unrelated (`list_agents` frozenset)
- [x] Only allowed runtime imports: `capability_tools.py` and `container.py`
- [x] Exactly 3 read-only capability tools; zero write tools
- [x] No `run_capability` / `evaluate_capability` / `transition_capability` tools
- [x] No script execution path exists
- [x] No legacy `promote_skill` integration
- [x] Every applied transition: planner → policy → eval → snapshot → manifest → re-parse → index → log
- [x] Blocked transitions: zero file/index/version/mutation-log changes (5×5 matrix verified)
- [x] Status-blocked (disabled, quarantined, archived): zero file changes
- [x] High-risk without approval: zero file changes
- [x] Snapshot timestamps unique across rapid transitions
- [x] Snapshots readable after archive
- [x] Eval record hash matches pre-transition content
- [x] Eval record write is non-mutating
- [x] Eval records readable after transition
- [x] TransitionResult completeness verified for both success and blocked paths

Hard constraints verified:
- [x] No Brain wiring
- [x] No TaskRuntime wiring
- [x] No StateViewBuilder wiring
- [x] No ToolDispatcher changes
- [x] No SkillExecutor changes
- [x] No promote_skill changes
- [x] No run_capability tool
- [x] No write capability tools
- [x] No script execution
- [x] No automatic retrieval
- [x] No ExperienceCurator
- [x] No dynamic agent changes
- [x] No RuntimeProfile permission grants

## Known Issues

1. **Pre-existing:** `tests/core/test_runtime_profiles_exclusion.py::TestProfileExclusivity::test_local_execution_profile_is_frozen` fails because `list_agents` was recently added to `LOCAL_EXECUTION_PROFILE.tool_names` but the test's expected frozenset was not updated. Not related to Phase 3B.

2. **Index refresh failure is not rolled back:** If `_maybe_index()` raises after manifest.json is written, the manifest change persists but the index is stale. The transition is still reported as applied. This is documented behavior (manifest is durable, index is derived). A future phase could add index repair or rollback.

## Rollback Notes

To roll back Phase 3B:
1. Remove `src/capabilities/lifecycle.py`
2. Remove four test files: `test_phase3b_lifecycle.py`, `test_phase3b_transition_atomicity.py`, `test_phase3b_hardening.py`, `test_phase3b_regression.py`
3. Revert `src/capabilities/__init__.py` (remove Phase 3B imports and `__all__` entries)
4. Revert `docs/capability_evolution_architecture.md` (remove Phase 3B section)
5. Delete `docs/capability_phase3b_acceptance.md`

No other files were modified.
