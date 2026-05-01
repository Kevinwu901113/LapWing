# Phase 3C Acceptance Report

**Date:** 2026-05-01
**Phase:** Capability Evolution System Phase 3C — Lifecycle Management Tools
**Status:** Accepted (Hardened)

## Test Results

### Capability tests

| Test Suite | Pass | Fail | Skip |
|---|---|---|---|
| Phase 3C Lifecycle Tools (`test_phase3c_lifecycle_tools.py`) | 47 | 0 | 0 |
| Phase 3B Lifecycle (`test_phase3b_lifecycle.py`) | 37 | 0 | 0 |
| Phase 3B Atomicity (`test_phase3b_transition_atomicity.py`) | 28 | 0 | 0 |
| Phase 3B Hardening (`test_phase3b_hardening.py`) | 33 | 0 | 0 |
| Phase 0/1/2A/2B/3A regression | 449 | 0 | 0 |
| Phase 3B regression (`test_phase3b_regression.py`) | 20 | 0 | 0 |
| **All capabilities** | **614** | **0** | **0** |

### Legacy / runtime test suites

| Test Suite | Pass | Fail | Notes |
|---|---|---|---|
| Skills and agents (`tests/skills/`, `tests/agents/`) | 272 | 0 | All pass |
| ToolDispatcher (`tests/core/test_tool_dispatcher.py`) | 55 | 0 | All pass |
| Logging / MutationLog (`tests/logging/`) | 32 | 0 | All pass |
| StateView (`tests/core/test_state_view_builder.py`, `test_stateview_agent_summary.py`) | 29 | 0 | All pass |
| RuntimeProfile (`tests/core/test_runtime_profiles_exclusion.py`, `test_tool_boundary.py`) | 45 | 0 | All pass (list_agents frozenset fixed) |
| **Total** | **1047** | **0** | **0** |

## Hardening Pass — RuntimeProfile Cleanup

The pre-existing `list_agents` frozenset mismatch (3 assertions across 2 tests) was fixed:

- `test_local_execution_does_not_expose_dynamic_agent_admin_tools`: removed `assert "list_agents" not in names` — `list_agents` was intentionally added to `LOCAL_EXECUTION_PROFILE` in commit `4a45f46`.
- `test_local_execution_profile_is_frozen`: added `"list_agents"` to both the profile `tool_names` frozenset assertion and the registry-resolved `expected_names` set.
- `test_agent_admin_operator_profile_exposes_agent_admin_tools_only`: added `"list_agents"` to expected set (already in `AGENT_ADMIN_OPERATOR_PROFILE.tool_names`).
- `_make_full_registry()`: registered `list_agents` with `capability="agent_admin"` so it resolves correctly.

No permissions were broadened. No profiles gained `capability_lifecycle`.

## Files Changed

### Modified files
- `src/tools/capability_tools.py` — added 3 lifecycle tool schemas, executors, and `register_capability_lifecycle_tools()`
- `src/app/container.py` — wire `CapabilityLifecycleManager` + lifecycle tools behind `CAPABILITIES_LIFECYCLE_TOOLS_ENABLED`
- `src/config/settings.py` — added `lifecycle_tools_enabled: bool = False` to `CapabilitiesConfig` + env var mapping
- `config/settings.py` — added `CAPABILITIES_LIFECYCLE_TOOLS_ENABLED` flat export
- `config.toml` — added `lifecycle_tools_enabled = false` to `[capabilities]`
- `src/core/runtime_profiles.py` — added `CAPABILITY_LIFECYCLE_OPERATOR_PROFILE`
- `src/capabilities/lifecycle.py` — added disabled/archived status check to `plan_transition()`
- `tests/capabilities/test_phase0_regression.py` — updated `test_all_known_profiles_exist` for new profile
- `tests/core/test_runtime_profiles_exclusion.py` — updated `list_agents` expectations (frozenset, expected_names, agent_admin set, test registry)
- `docs/capability_evolution_architecture.md` — added Phase 3C section

### New files
- `tests/capabilities/test_phase3c_lifecycle_tools.py` — 47 tests
- `docs/capability_phase3c_acceptance.md` — this file

## 1. Feature Flag Registration

| Condition | Tools registered | Test |
|---|---|---|
| Only `register_capability_tools` called (no lifecycle) | 3 read-only, 0 lifecycle | `test_lifecycle_tools_not_registered_when_disabled` PASS |
| `register_capability_lifecycle_tools` called | 3 lifecycle tools present | `test_lifecycle_tools_registered_when_flag_enabled` PASS |
| Lifecycle tools tagged `capability_lifecycle` | All 3 use correct tag | `test_lifecycle_tools_use_capability_lifecycle_tag` PASS |
| Lifecycle tools not tagged `capability_read` | None reuse read tag | `test_lifecycle_tools_dont_use_capability_read` PASS |
| No forbidden tools | No run/create/install/patch/auto_promote | `test_no_forbidden_tools_registered` PASS |

## 2. Permission / Profile Behavior

| Check | Test | Result |
|---|---|---|
| Lifecycle profile has `capability_lifecycle` | `test_lifecycle_profile_has_capability_lifecycle` | PASS |
| Standard profile excludes `capability_lifecycle` | `test_standard_profile_excludes_capability_lifecycle` | PASS |
| Chat shell excludes | `test_chat_shell_profile_excludes_capability_lifecycle` | PASS |
| Inner tick excludes | `test_inner_tick_profile_excludes_capability_lifecycle` | PASS |
| Local execution excludes | `test_local_execution_profile_excludes_capability_lifecycle` | PASS |
| Agent admin excludes | `test_agent_admin_profile_excludes_capability_lifecycle` | PASS |
| Browser operator excludes | `test_browser_operator_profile_excludes_capability_lifecycle` | PASS |
| Skill operator excludes | `test_skill_operator_profile_excludes_capability_lifecycle` | PASS |
| No standard profile has lifecycle tool names | `test_lifecycle_profile_not_in_standard_profiles` | PASS |

Hardening verification (runtime check):
- Only `capability_lifecycle_operator` has `capability_lifecycle` in its capabilities set.
- No standard/default/chat/inner_tick/local_execution/browser/identity/skill profile has `capability_lifecycle`.

## 3. evaluate_capability Behavior

| Check | Test | Result |
|---|---|---|
| Evaluates valid capability | `test_evaluates_valid_capability` | PASS |
| Writes eval record when `write_record=true` | `test_writes_eval_record_when_write_record_true` | PASS |
| Does not write when `write_record=false` | `test_does_not_write_eval_record_when_false` | PASS |
| Does not change manifest maturity | `test_does_not_change_manifest_maturity` | PASS |
| Does not change manifest status | `test_does_not_change_manifest_status` | PASS |
| Does not write version snapshot | `test_does_not_write_version_snapshot` | PASS |
| Returns not_found for missing id | `test_returns_not_found_for_missing_id` | PASS |
| Includes findings when requested | `test_includes_findings_when_requested` | PASS |
| Excludes findings when requested | `test_excludes_findings_when_requested` | PASS |
| Does not execute scripts | `test_does_not_execute_scripts` | PASS |

## 4. plan_capability_transition Behavior

| Check | Test | Result |
|---|---|---|
| draft→testing allowed for valid low-risk | `test_draft_to_testing_allowed` | PASS |
| testing→stable blocked without eval | `test_testing_to_stable_blocked_without_eval` | PASS |
| High risk requires approval | `test_high_risk_requires_approval` | PASS |
| stable→broken requires failure_evidence | `test_stable_to_broken_requires_evidence` | PASS |
| Disabled blocked from promotion | `test_disabled_blocked_from_promotion` | PASS |
| Plan does not mutate manifest | `test_plan_does_not_mutate_manifest` | PASS |
| Plan does not write snapshot | `test_plan_does_not_write_snapshot` | PASS |
| Rejects invalid target | `test_plan_rejects_invalid_target` | PASS |

## 5. transition_capability Behavior

| Check | Test | Result |
|---|---|---|
| dry_run=true makes no changes | `test_dry_run_makes_no_changes` | PASS |
| draft→testing applies | `test_draft_to_testing_applies` | PASS |
| High-risk stable blocked without approval | `test_high_risk_stable_blocked_without_approval` | PASS |
| Successful transition writes snapshot | `test_successful_transition_writes_snapshot` | PASS |
| Successful transition records mutation log | `test_successful_transition_records_mutation_log` | PASS |
| Mutation log failure does not corrupt | `test_mutation_log_failure_does_not_corrupt` | PASS |
| Disabled target applies | `test_disabled_target_applies` | PASS |
| Archived target applies | `test_archived_target_applies` | PASS |
| Disabled cannot promote | `test_disabled_cannot_promote` | PASS |
| Returns not_found for missing id | `test_returns_not_found_for_missing_id` | PASS |
| Rejects invalid target | `test_rejects_invalid_target` | PASS |

## 6. TransitionResult Payload Completeness

| Check | Test | Result |
|---|---|---|
| Successful result has all required fields | `test_successful_result_has_required_fields` | PASS |
| Blocked result has message | `test_blocked_result_has_message` | PASS |

## 7. Dry-Run Completeness

| Check | Test | Result |
|---|---|---|
| dry_run returns allowed | `test_dry_run_returns_allowed` | PASS |
| dry_run no file changes | `test_dry_run_no_file_changes` | PASS |

## 8. Runtime Import Check

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
src/tools/capability_tools.py  — allowed (read + lifecycle tools)
src/app/container.py            — allowed (store/index/lifecycle construction)
```

**No unauthorized imports.** Confirmed zero references from:
- Brain, TaskRuntime, StateViewBuilder, SkillExecutor, SkillStore runtime paths
- ToolDispatcher, AgentRegistry execution paths, AgentPolicy execution paths
- RuntimeProfile (profile definitions import only constants; no capability import)

## 9. No Script Execution

- `evaluate_capability` calls `CapabilityLifecycleManager.evaluate()` — tested in Phase 3B.
- `plan_capability_transition` calls `plan_transition()` — read-only, no execution.
- `transition_capability` calls `apply_transition()` — tested in Phase 3B hardening.
- No tool executor imports or executes Python scripts.
- No tool executor runs shell commands.
- `grep` for `exec(`, `subprocess`, `os.system`, `eval(`, `execfile` in `capability_tools.py` returns no results.

## 10. Confirmation Checklist

- [x] All 1047 tests pass (614 capability + 272 skills/agents + 55 ToolDispatcher + 32 logging + 29 StateView + 45 RuntimeProfile)
- [x] 0 pre-existing failures remain (RuntimeProfile `list_agents` frozenset fixed)
- [x] Only allowed runtime imports: `capability_tools.py` and `container.py`
- [x] Lifecycle tools absent by default (`lifecycle_tools_enabled=false`)
- [x] Read-only tools unchanged when only `capabilities.enabled=true`
- [x] Lifecycle tools appear only when `lifecycle_tools_enabled=true`
- [x] Lifecycle tools use `capability_lifecycle` tag (not `capability_read`)
- [x] Only `CAPABILITY_LIFECYCLE_OPERATOR_PROFILE` grants lifecycle access
- [x] No standard/default/broad profile has lifecycle permissions
- [x] `evaluate_capability` persists EvalRecords without changing maturity/status
- [x] `plan_capability_transition` is read-only
- [x] `transition_capability` applies only policy/evaluator/planner-approved transitions
- [x] Blocked transitions make zero file/index/mutation changes
- [x] Successful transitions snapshot, mutate, hash, refresh index, and optionally log
- [x] `dry_run=true` makes zero changes
- [x] No script execution path exists
- [x] No `run_capability` / `create_capability` / `install_capability` / `patch_capability` tools
- [x] No Brain / TaskRuntime / StateViewBuilder / SkillExecutor modifications
- [x] No legacy promote_skill integration
- [x] No ExperienceCurator
- [x] No automatic retrieval
- [x] No dynamic agent changes

Hard constraints verified:
- [x] No Brain wiring
- [x] No TaskRuntime wiring
- [x] No StateViewBuilder wiring
- [x] No ToolDispatcher changes
- [x] No SkillExecutor changes
- [x] No promote_skill changes
- [x] No run_capability tool
- [x] No create/install/patch capability tools
- [x] No script execution
- [x] No automatic retrieval
- [x] No ExperienceCurator
- [x] No dynamic agent changes
- [x] No RuntimeProfile permission grants to existing profiles
- [x] Read-only tools still function with only capabilities.enabled=true

## Known Issues

None. The pre-existing `list_agents` frozenset mismatch in `test_runtime_profiles_exclusion.py` (3 assertions across 2 tests) has been fixed:
- `LOCAL_EXECUTION_PROFILE.tool_names` frozenset: added `"list_agents"`
- `AGENT_ADMIN_OPERATOR_PROFILE` expected set: added `"list_agents"`
- `_make_full_registry()`: registered `list_agents` with `capability="agent_admin"`
- `test_local_execution_does_not_expose_dynamic_agent_admin_tools`: removed stale `assert "list_agents" not in names`

## Rollback Notes

To roll back Phase 3C:
1. Revert `src/tools/capability_tools.py` (remove lifecycle tool schemas, executors, and registration function)
2. Revert `src/app/container.py` (remove lifecycle manager construction and lifecycle tool registration block)
3. Revert `src/config/settings.py` (remove `lifecycle_tools_enabled` from `CapabilitiesConfig`)
4. Revert `config/settings.py` (remove `CAPABILITIES_LIFECYCLE_TOOLS_ENABLED`)
5. Revert `config.toml` (remove `lifecycle_tools_enabled`)
6. Revert `src/core/runtime_profiles.py` (remove `CAPABILITY_LIFECYCLE_OPERATOR_PROFILE`)
7. Revert `src/capabilities/lifecycle.py` (remove disabled/archived check from `plan_transition()`)
8. Revert `tests/capabilities/test_phase0_regression.py` (remove `capability_lifecycle_operator` from expected profiles)
9. Revert `tests/core/test_runtime_profiles_exclusion.py` (undo list_agents frozenset updates)
10. Delete `tests/capabilities/test_phase3c_lifecycle_tools.py`
11. Revert `docs/capability_evolution_architecture.md` (remove Phase 3C section)
12. Delete `docs/capability_phase3c_acceptance.md`

No other files were modified.
