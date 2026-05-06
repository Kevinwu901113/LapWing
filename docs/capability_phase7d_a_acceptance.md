# Phase 7D-A Acceptance — Hardened

**Date:** 2026-05-03
**Scope:** Quarantine activation planner — computes explicit activation plans only. No activation.
**Status:** Accepted (Hardened) — 85 tests, 12 audit categories, zero findings

---

## 1. What This Phase Is

Phase 7D-A introduces a **planner-only** capability: given a quarantined capability with a pending transition request (from Phase 7C), compute an explicit activation plan. The plan is a data artifact stored under the quarantine directory — it does not perform any activation, file copy, mutation, or execution.

## 2. What This Phase Is NOT

Explicitly and verifiably NOT:
- Activation, lifecycle mutation, file copy/move
- Active index update, default retrieval enablement, StateView exposure
- Script/code execution, capability promotion, run_capability

---

## 3. Test Results

| Test File | Tests | Result |
|-----------|-------|--------|
| test_phase7d_activation_planner.py | 38 | All pass |
| test_phase7d_activation_planner_tools.py | 13 | All pass |
| test_phase7d_activation_planner_safety.py | 9 | All pass |
| test_phase7d_hardening.py | 25 | All pass |
| **Phase 7D-A total** | **85** | **All pass** |

### Regression (full suite)

- **Total:** 2,140 passed, 0 failed
- tests/capabilities/: 1,397 passed (85 new + 1,312 prior)
- tests/agents/: 544 passed
- tests/core/: all pass (ToolDispatcher, RuntimeProfile, StateView)
- tests/skills/: 64 passed
- tests/logging/: 32 passed
- All Phase 7A/7B/7C tests: no regressions

---

## 4. Feature Flag Matrix

| Flag | Value | Effect |
|------|-------|--------|
| `capabilities.enabled` | false | plan_quarantine_activation absent |
| `capabilities.external_import_enabled` | false | plan_quarantine_activation absent (nested under this gate) |
| `capabilities.quarantine_transition_requests_enabled` | false | plan_quarantine_activation absent (nested under this gate) |
| `capabilities.quarantine_activation_planning_enabled` | false | plan_quarantine_activation absent |
| All above flags | true | plan_quarantine_activation registered |

**Dependency chain:** enabled → external_import_enabled → quarantine_activation_planning_enabled

The planning flag itself grants no permissions — only the `capability_import_operator` tag on the tool controls access.

---

## 5. Permission Matrix

| Profile | Has capability_import_operator? | plan_quarantine_activation allowed? |
|---------|-------------------------------|-------------------------------------|
| capability_import_operator | Yes | Yes |
| standard | No | Denied |
| default | No | Denied |
| chat | No | Denied |
| local_execution | No | Denied |
| browser_operator | No | Denied |
| identity_operator | No | Denied |
| capability_lifecycle_operator | No (different tag) | Denied |
| capability_curator_operator | No (different tag) | Denied |
| agent_candidate_operator | No (different tag) | Denied |

Tool uses `capability="capability_import_operator"` tag — same operator profile as Phase 7A/7B/7C tools.

---

## 6. Plan Gate Proof

### Allowed only when (12 gates):

| # | Gate | Test |
|---|------|------|
| 1 | Capability exists in quarantine | `test_missing_capability_blocked` |
| 2 | `manifest.status == "quarantined"` | `test_status_not_quarantined_blocked` |
| 3 | `manifest.maturity == "draft"` | `test_maturity_not_draft_blocked` |
| 4 | Pending transition request exists | `test_missing_request_blocked` |
| 5 | `request.status == "pending"` | `test_cancelled_request_blocked`, `test_rejected_request_blocked`, `test_superseded_request_blocked` |
| 6 | `request.target_maturity == "testing"` | `test_request_target_maturity_not_testing_blocked` |
| 7 | Content hash matches (if available) | `test_content_hash_mismatch_blocked` (safety) |
| 8 | Review `approved_for_testing` exists | `test_no_approved_review_blocked`, `test_review_not_approved_for_testing_blocked`, `test_review_rejected_blocked` |
| 9 | Audit passed/recommended | `test_no_audit_blocked`, `test_failed_audit_blocked` |
| 10 | Evaluator passes (no error findings) | `test_failed_audit_blocked` (eval re-run) |
| 11 | Policy install/transition checks allow | Evaluator + policy gate coverage |
| 12 | No target scope collision | `test_target_scope_collision_blocked` |

### Additional denial conditions verified:

- `test_invalid_target_scope_rejected`
- `test_specified_request_id_not_found_blocked`
- `test_path_traversal_capability_id_rejected`
- `test_slash_in_capability_id_rejected`
- `test_backslash_in_capability_id_rejected`

---

## 7. Plan-Only / No-Mutation Proof

### Byte-for-byte immutability verified:

| Check | Test |
|-------|------|
| manifest.json unchanged | `test_manifest_unchanged_byte_for_byte`, `test_all_non_plan_files_unchanged_byte_for_byte` |
| CAPABILITY.md unchanged | `test_capability_md_unchanged_byte_for_byte` |
| import_report.json unchanged | `test_import_report_unchanged` |
| Review files unchanged | `test_review_files_unchanged` |
| Audit files unchanged | `test_audit_files_unchanged` |
| Request files unchanged | `test_request_files_unchanged` |
| No active scope dir created | `test_no_active_scope_directory_created` |
| No index file created/modified | `test_no_index_file_created_or_modified` |
| No eval record created | `test_no_eval_record_created` |
| No version snapshot created | `test_no_version_snapshot_created` |
| dry_run writes nothing | `test_dry_run_writes_nothing` |
| persist writes only plan JSON | `test_persist_writes_only_plan_json`, `test_only_plan_dir_created_under_quarantine`, `test_no_files_written_outside_quarantine` |

### Code-level proof:
- No `CapabilityLifecycleManager` calls
- No `CapabilityStore.create_draft` calls
- No `CapabilityIndex.upsert` calls
- No `PromotionPlanner` / promotion calls
- No shutil copy/move to active scope
- File writes only via `_write_plan()` → `quarantine_activation_plans/<plan_id>.json`

---

## 8. Plan-Not-Authority Proof

| Assertion | Test |
|-----------|------|
| `would_activate` always False (3 return paths, all False) | All tests check `result["would_activate"] is False` |
| Allowed plan sets no review_status | `test_allowed_plan_does_not_set_any_mutation_fields` |
| Allowed plan does not mark request approved/applied | `test_allowed_plan_does_not_modify_request_status` |
| Allowed plan does not grant permissions | `test_allowed_plan_does_not_set_any_mutation_fields` |
| `required_approval=true` does not equal approval | `test_required_approval_does_not_equal_approval` |
| plan_id cannot be lifecycle authority | `test_plan_id_cannot_be_used_as_lifecycle_authority` |
| No code path consumes plan to activate anything | Code audit: no `activate`, `apply_plan`, `execute_plan` in module |

---

## 9. Output/Privacy Proof

| Guarantee | Verification |
|-----------|-------------|
| No raw absolute quarantine paths in tool output | `test_tool_output_strips_internal_paths`, `test_view_strips_internal_paths` |
| No raw original source paths | `test_tool_output_has_no_raw_absolute_source_paths` |
| copy_plan uses sanitized fields only | `test_tool_output_strips_internal_paths`, `test_persisted_plan_strips_internal_paths_from_output` |
| `_source_quarantine_dir` stripped from output | `tool_output()` method + view strips internal keys |
| No script contents in output | Planner never reads script file contents |
| No CAPABILITY.md body in output | Planner reads for parsing only, doesn't embed body |
| No raw logs/CoT fields | Plain dataclass, no log embedding |
| Prompt injection treated as data | `test_prompt_injection_in_reason_treated_as_data` |

---

## 10. No-Execution Proof

| Check | Result |
|-------|--------|
| `subprocess` | Not imported |
| `os.system` | Not called |
| `os.popen` | Not called |
| `exec` | Not called |
| `eval` | Not called |
| `importlib` | Not imported |
| `runpy` | Not imported |
| `requests` | Not imported |
| `httpx` | Not imported |
| `urllib` | Not imported |
| `openai` | Not imported |
| `anthropic` | Not imported |
| Script execution | `test_script_not_executed`, `test_no_python_import_of_script` |

---

## 11. Path/Corruption Safety

| Check | Test |
|-------|------|
| capability_id traversal rejected | `test_path_traversal_capability_id_rejected` |
| request_id traversal rejected | (handled by shared `_validate_id_token`) |
| plan_id path-safe | `test_plan_id_is_path_safe` |
| target_scope validation strict | `test_invalid_target_scope_rejected` |
| Corrupt manifest handled | `test_corrupt_manifest_handled` |
| Corrupt request handled | `test_corrupt_request_file_handled` |
| Corrupt review handled | `test_corrupt_review_file_gate_handled` |
| Corrupt audit handled | `test_corrupt_audit_file_gate_handled` |
| Duplicate plan IDs unique | `test_duplicate_plan_ids_unique` |
| Writes stay under quarantine dir | `test_only_plan_dir_created_under_quarantine`, `test_no_files_written_outside_quarantine` |

---

## 12. Quarantine Isolation Proof

| Assertion | Test |
|-----------|------|
| Not in active store list after plan | `test_capability_not_in_active_store_list_after_plan` |
| Not in active index after plan | `test_capability_not_in_active_index_after_plan` |
| Stays in quarantine directory | `test_capability_stays_in_quarantine_directory` |
| Allowed plan doesn't make retrievable | `test_allowed_plan_does_not_make_capability_retrievable` |
| No scope directories created | `test_no_active_scope_directory_created`, `test_capability_not_in_active_index_after_plan` |

---

## 13. Forbidden Tool Audit

Verified absent across entire src/:

| Forbidden Tool | Status |
|---------------|--------|
| `apply_quarantine_activation` | Not found |
| `activate_quarantined_capability` | Not found |
| `promote_quarantined_capability` | Not found |
| `run_quarantined_capability` | Not found |
| `run_capability` | Not found |
| `execute_capability` | Not found |
| `install_capability` | Not found |
| `save_quarantined_as_capability` | Not found |
| `move_quarantine_to_workspace` | Not found |

Only `plan_quarantine_activation` registered in Phase 7D-A registration function.

---

## 14. Runtime Import Audit

Only allowed non-capability imports from `src.capabilities`:
- `src/tools/capability_tools.py`
- `src/app/container.py`

No imports from: Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, agent modules, dynamic agent runtime paths.

---

## 15. Files Changed

| File | Change |
|------|--------|
| `config.toml` | Added `quarantine_activation_planning_enabled = false` |
| `src/config/settings.py` | Pydantic field + env mapping |
| `config/settings.py` | Module-level exports (Phase 7C + 7D-A) |
| `src/capabilities/__init__.py` | Exports for Phase 7C/7D-A classes |
| `src/capabilities/quarantine_activation_planner.py` | **New** — core planner module |
| `src/tools/capability_tools.py` | Tool schema, executor, registration |
| `src/app/container.py` | Phase 7D-A wiring behind flag |
| `tests/capabilities/test_phase7d_activation_planner.py` | **New** — 38 tests |
| `tests/capabilities/test_phase7d_activation_planner_tools.py` | **New** — 13 tests |
| `tests/capabilities/test_phase7d_activation_planner_safety.py` | **New** — 9 tests |
| `tests/capabilities/test_phase7d_hardening.py` | **New** — 25 tests |
| `docs/capability_phase7d_activation_planning.md` | **New** — feature doc |
| `docs/capability_phase7d_a_acceptance.md` | **New** — this document |
| `docs/capability_acceptance_index.md` | Updated |
| `docs/capability_system_overview.md` | Updated |

---

## 16. Known Issues

None.

## 17. Rollback Notes

To disable Phase 7D-A: set `capabilities.quarantine_activation_planning_enabled = false` in config.toml.
Tool disappears from registry. Persisted plans remain as audit artifacts under `quarantine/<id>/quarantine_activation_plans/` — no cleanup needed.
No capability state to roll back — planning never mutates capabilities.

## 18. Phase 7D-B Readiness

Phase 7D-A is ready for Phase 7D-B (actual activation). Deliverables:
- 85 passing tests
- 12 hardened audit categories, all verified
- Zero code paths that activate, move, copy, promote, execute, or mutate
- Auditable plan artifacts waiting in quarantine storage
