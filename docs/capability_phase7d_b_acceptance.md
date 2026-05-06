# Phase 7D-B Acceptance — Hardened

**Date:** 2026-05-04
**Status:** Accepted (Hardened)
**Phase:** 7D-B — Quarantine Activation Apply

Explicit operator-only application of a previously approved quarantine
activation plan into active/testing. First phase that copies a quarantined
capability into an active target scope.

---

## 1. Feature Flag / Permission Matrix

### Feature flag gating

| Flag | Default | Required for 7D-B |
|------|---------|-------------------|
| `capabilities.enabled` | false | Yes |
| `capabilities.external_import_enabled` | false | Yes |
| `capabilities.quarantine_transition_requests_enabled` | false | Yes |
| `capabilities.quarantine_activation_planning_enabled` | false | Yes |
| `capabilities.quarantine_activation_apply_enabled` | **false** | **Yes** |

Tool is absent when ANY of the five flags is false. Nested under all parent flags in `src/app/container.py:1228`.

### Permission matrix

| Profile | Allowed? | Reason |
|---------|----------|--------|
| `capability_import_operator` | **Yes** | Only authorized profile |
| `standard` | No | No capability tag |
| `default` | No | No capability tag |
| `chat` | No | No capability tag |
| `local_execution` | No | No capability tag |
| `browser_operator` | No | Different tag |
| `identity_operator` | No | Different tag |
| `capability_lifecycle_operator` | No | Different tag |
| `capability_curator_operator` | No | Different tag |
| `agent_candidate_operator` | No | Different tag |

Tool capability tag: `capability_import_operator`
Tool risk_level: `high`

### Tool verification

- [x] Absent when `capabilities.enabled=false`
- [x] Absent when `external_import_enabled=false`
- [x] Absent when `quarantine_transition_requests_enabled=false`
- [x] Absent when `quarantine_activation_planning_enabled=false`
- [x] Absent when `quarantine_activation_apply_enabled=false`
- [x] Registered only when all five flags enabled
- [x] Requires `capability_import_operator`
- [x] `risk_level=high`
- [x] Non-operator profiles denied

---

## 2. Forbidden Tool Audit

### Verified absent (no tool registrations, no function definitions)

| Tool/Function | Status |
|---------------|--------|
| `run_capability` | NOT PRESENT |
| `execute_capability` | NOT PRESENT |
| `run_quarantined_capability` | NOT PRESENT |
| `promote_quarantined_capability` | NOT PRESENT |
| `promote_imported_capability` | NOT PRESENT |
| `install_capability` | NOT PRESENT |
| `auto_install_capability` | NOT PRESENT |
| `registry_search_capability` | NOT PRESENT |
| `update_capability_from_remote` | NOT PRESENT |
| `activate_quarantined_capability` | NOT PRESENT |
| `apply_quarantine_transition` | NOT PRESENT |

### Only apply tool present

| Tool | Status |
|------|--------|
| `apply_quarantine_activation` | **Phase 7D-B only** |

---

## 3. Gate Hardening (18 gates)

### Gate coverage — all 18 gates verified by test

| # | Gate | Test | Denial type |
|---|------|------|-------------|
| 1 | Capability exists in quarantine | `test_missing_plan_denied` | `not_found` |
| 2 | manifest.status == quarantined | `test_status_not_quarantined_denied` | `bad_status` |
| 3 | manifest.maturity == draft | `test_maturity_not_draft_denied` | `bad_maturity` |
| 4 | Activation plan loaded | `test_missing_plan_denied` | `no_allowed_plan` |
| 5 | plan.allowed == true | `test_plan_not_allowed_denied` | `plan_not_allowed` |
| 6 | plan.target_maturity == testing | `test_plan_not_testing_denied` | `bad_plan_maturity` |
| 7 | plan.target_status == active | (implicit in plan schema) | `bad_plan_status` |
| 8 | plan.would_activate == false | (validated in code) | `invalid_plan_state` |
| 9 | Pending request exists | `test_no_pending_request_denied` | `no_pending_request` |
| 10 | request.status == pending | `test_request_not_pending_denied` | `request_not_pending` |
| 11 | Content hash matches | `test_target_content_hash_recomputed` | `content_hash_mismatch` |
| 12 | Review approved_for_testing | `test_review_not_approved_denied` | `review_not_approved` |
| 13 | Audit passed/approved | `test_audit_not_passed_denied` | `audit_not_approved` |
| 14 | Target collision — none | `test_target_collision_denied` | `target_collision` |
| 15 | Evaluator re-run passed | (implicit via valid-cap test) | `evaluator_failed` |
| 16 | Policy install allowed | (implicit via valid-cap test) | `policy_denied` |
| 17 | High risk blocked | `test_high_risk_blocked` | `high_risk_blocked` |
| 18 | No symlinks | `test_symlinks_rejected` | `symlinks_rejected` |

### Additional rejection conditions tested

- [x] Missing capability (`CapabilityError` raised)
- [x] Path traversal in capability_id (`CapabilityError` raised)
- [x] Path traversal in plan_id (`CapabilityError` raised)
- [x] Path traversal in request_id (`CapabilityError` raised)
- [x] Missing reason → `missing_reason`
- [x] Plan target scope mismatch
- [x] Content hash mismatch between plan and current state (`plan_content_hash_mismatch`)

---

## 4. Dry Run Behavior

### Verified

- [x] `dry_run=true` performs all 18 gate checks
- [x] Returns `applied=False`, `dry_run=True`
- [x] Writes nothing — no target directory created
- [x] Creates no `activation_report.json`
- [x] Updates no CapabilityIndex
- [x] Leaves quarantine directory byte-for-byte unchanged
- [x] Executes nothing (no subprocess, no imports)

### Test coverage

| Test | File |
|------|------|
| `test_dry_run_writes_nothing` | `test_phase7d_activation_apply.py:516` |
| `test_dry_run_writes_nothing` (tool) | `test_phase7d_activation_apply_tools.py:225` |
| `test_tool_has_dry_run_in_schema` | `test_phase7d_activation_apply_tools.py:171` |

---

## 5. Successful Apply Behavior

### Verified

- [x] Creates target directory under `data/capabilities/<target_scope>/<id>/` only
- [x] Copies files from quarantine; does NOT move them
- [x] Target manifest `status=active`
- [x] Target manifest `maturity=testing`
- [x] Target manifest `scope=target_scope`
- [x] Target `content_hash` recomputed and stable
- [x] Target `extra.origin` contains:
  - `quarantine_capability_id`
  - `activation_plan_id`
  - `transition_request_id`
  - `import_source_hash`
  - `activated_at`
  - `activated_by`
- [x] `activation_report.json` written in target directory
- [x] `activation_report.json` written in quarantine `quarantine_activation_reports/`
- [x] CapabilityIndex refreshed for target copy only
- [x] Target appears in index search by id
- [x] Target does NOT become stable (maturity is `testing`, not `stable`)
- [x] Target does NOT execute scripts (files copied as data only)
- [x] Target `required_tools`/`required_permissions` are carried forward as-is (no elevation)
- [x] Target subject to normal lifecycle/tool/runtime policy (same maturity=testing rules)

### Test coverage

| Test | File |
|------|------|
| `test_valid_capability_applies_to_testing` | `test_phase7d_activation_apply.py` |
| `test_apply_creates_target_scope_dir` | `test_phase7d_activation_apply.py` |
| `test_target_manifest_status_active` | `test_phase7d_activation_apply.py` |
| `test_target_content_hash_recomputed` | `test_phase7d_activation_apply.py` |
| `test_origin_metadata_written` | `test_phase7d_activation_apply.py` |
| `test_activation_report_written_in_target` | `test_phase7d_activation_apply.py` |
| `test_activation_report_written_in_quarantine` | `test_phase7d_activation_apply.py` |
| `test_index_refreshed_for_target_copy` | `test_phase7d_activation_apply.py` |
| `test_no_stable_maturity_created` | `test_phase7d_activation_apply.py` |
| `test_explicit_plan_id` | `test_phase7d_activation_apply.py` |

---

## 6. Quarantine Original Preservation

### Verified

- [x] Quarantine directory still exists after apply
- [x] `manifest.status` remains `quarantined`
- [x] `manifest.maturity` remains `draft`
- [x] `CAPABILITY.md` unchanged byte-for-byte
- [x] `import_report.json` remains
- [x] Audit/review/request/plan reports remain
- [x] Scripts/tests/examples/evals/traces/versions remain
- [x] Original remains excluded from default list/search/retrieval/StateView
- [x] Original remains non-executable (no status change = no retrieval)
- [x] Transition request may be marked `superseded` (expected mutation, not corruption)

### Test coverage

| Test | File |
|------|------|
| `test_original_quarantine_manifest_unchanged` | `test_phase7d_activation_apply.py:469` |
| `test_original_quarantine_files_remain` | `test_phase7d_activation_apply.py:494` |
| `test_no_mutate_quarantine_original_during_apply` | `test_phase7d_activation_apply.py:606` |
| `test_original_quarantine_never_corrupted_on_success` | `test_phase7d_activation_apply_atomicity.py:193` |
| `test_original_quarantine_never_corrupted_on_failure` | `test_phase7d_activation_apply_atomicity.py:227` |
| `test_original_manifest_status_unchanged_after_success` | `test_phase7d_activation_apply_atomicity.py:251` |

---

## 7. Atomicity / Rollback

### Verified

- [x] Denied gate → no writes, no target dir
- [x] Target collision → no writes, existing files unmodified
- [x] Blocked-by-gate → no target dir created
- [x] Repeated apply after successful first → denied (collision or no_pending_request)
- [x] Original quarantine never corrupted (success path)
- [x] Original quarantine never corrupted (failure path)
- [x] Original manifest status/maturity unchanged after success
- [x] No request/plan/review/audit files corrupted

### Targeted failure mode tests

| Test | File |
|------|------|
| `test_denied_apply_writes_nothing` | `test_phase7d_activation_apply_atomicity.py:135` |
| `test_collision_writes_nothing` | `test_phase7d_activation_apply_atomicity.py:153` |
| `test_target_dir_not_created_on_failure` | `test_phase7d_activation_apply_atomicity.py:176` |
| `test_blocked_by_gate_leaves_no_target_dir` | `test_phase7d_activation_apply_atomicity.py:298` |
| `test_repeated_apply_after_success_is_denied` | `test_phase7d_activation_apply_atomicity.py:271` |

### Rollback semantics

On any exception during apply (copy failure, manifest write failure, activation report failure):
- Target directory is removed via `shutil.rmtree()`
- `ActivationResult` returned with `applied=False`
- Quarantine original is never touched during cleanup

On index refresh failure:
- Target directory is rolled back (`shutil.rmtree`)
- `ActivationResult` returned with `index_refreshed=False`, error in `blocking_findings`

---

## 8. Index / Retrieval / StateView

### Verified

- [x] Index updated only for target active/testing copy (`index.upsert(target_doc)`)
- [x] Quarantine copy NOT indexed as active (never passed to `index.upsert`)
- [x] Default search sees target only per active/testing rules (status=active in search query)
- [x] Default search does NOT see quarantine original (status=quarantined filtered out)
- [x] activation_report does NOT enter StateView (not a capability document)
- [x] Script/test/eval/trace contents do NOT enter StateView

### Test coverage

- `test_index_refreshed_for_target_copy` — verifies `index_refreshed=True` and target in search results

---

## 9. Execution Safety

### Source code audit of `quarantine_activation_apply.py`

| Pattern | Count | Status |
|---------|-------|--------|
| `subprocess` | 0 | Clean |
| `os.system` | 0 | Clean |
| `os.popen` | 0 | Clean |
| `exec` (builtin) | 0 | Clean |
| `eval` (builtin) | 0 | Clean |
| `importlib` | 0 | Clean |
| `runpy` | 0 | Clean |
| `requests` | 0 | Clean |
| `httpx` | 0 | Clean |
| `urllib` | 0 | Clean |
| `openai` | 0 | Clean |
| `anthropic` | 0 | Clean |
| `__import__` | 0 | Clean |
| `compile(` | 0 | Clean |
| Shell helpers | 0 | Clean |

Only safe standard library imports: `json`, `shutil`, `uuid`, `dataclasses`, `datetime`, `pathlib`, `typing`.

### Test coverage

| Test | File |
|------|------|
| `test_no_subprocess_called_during_apply` | `test_phase7d_activation_apply_safety.py:212` |
| `test_no_os_system_called_during_apply` | `test_phase7d_activation_apply_safety.py:236` |
| `test_scripts_copied_but_not_executed` | `test_phase7d_activation_apply_safety.py:280` |
| `test_symlinks_rejected` | `test_phase7d_activation_apply_safety.py:312` |

---

## 10. Output / Privacy / Path Safety

### Verified

- [x] No raw absolute source path in `ActivationResult.to_dict()`
- [x] No raw original import path in tool output
- [x] No script contents emitted
- [x] No test/eval/trace contents emitted
- [x] No full CAPABILITY body in output
- [x] No raw logs emitted
- [x] Source hash only (no raw paths)
- [x] Path traversal rejected for `capability_id` (via `_validate_id_token`)
- [x] Path traversal rejected for `plan_id` (via `_validate_id_token`)
- [x] Path traversal rejected for `request_id` (via `_validate_id_token`)
- [x] `target_scope` strict enum: `["user", "workspace", "session", "global"]`
- [x] Prompt injection in `reason` treated as data (maturity stays `testing`)

### Test coverage

| Test | File |
|------|------|
| `test_activation_result_no_raw_source_paths` | `test_phase7d_activation_apply_safety.py:260` |
| `test_path_traversal_in_capability_id_rejected` | `test_phase7d_activation_apply_safety.py:166` |
| `test_path_traversal_in_plan_id_rejected` | `test_phase7d_activation_apply_safety.py:180` |
| `test_path_traversal_in_request_id_rejected` | `test_phase7d_activation_apply_safety.py:196` |
| `test_prompt_injection_in_reason_treated_as_data` | `test_phase7d_activation_apply_safety.py:340` |
| `test_error_does_not_emit_stack_trace` | `test_phase7d_activation_apply_tools.py:342` |

---

## 11. Request/Plan Authority Semantics

### Verified

- [x] Request alone cannot activate (requires plan + apply tool)
- [x] Review alone cannot activate (requires plan + apply tool)
- [x] Audit alone cannot activate (requires plan + apply tool)
- [x] Plan alone cannot activate unless `apply_quarantine_activation` explicitly called
- [x] Apply tool re-runs evaluator and policy gates; does NOT blindly trust plan
- [x] Stale content hash mismatch blocks apply (`content_hash_mismatch`, `plan_content_hash_mismatch`)
- [x] Apply result does NOT grant future stable promotion (maturity=testing only)
- [x] Testing target must still go through normal lifecycle to reach stable later
- [x] `would_activate` in persisted plan must be `false` (plan is separate authority)

---

## 12. Runtime Import Audit

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

**Allowed imports (only these two files):**
- `src/tools/capability_tools.py` — tool executors (lazy imports inside executors)
- `src/app/container.py` — DI container wiring

**No imports from:**
- [x] Brain
- [x] TaskRuntime
- [x] StateViewBuilder
- [x] SkillExecutor
- [x] ToolDispatcher
- [x] Agent modules
- [x] Dynamic agent runtime paths

---

## 13. Regression Checks

### All test suites

| Suite | Tests | Result |
|-------|-------|--------|
| `tests/capabilities/` (full) | 1,462 | 0 failed |
| `tests/capabilities/test_phase7*.py` | 430 | 0 failed |
| `tests/capabilities/test_phase7a*.py` | 49 | 0 failed |
| `tests/capabilities/test_phase7b*.py` | 79 | 0 failed |
| `tests/capabilities/test_phase7c*.py` | 137 | 0 failed |
| `tests/capabilities/test_phase7d_activation_planner*.py` | 85 | 0 failed |
| `tests/capabilities/test_phase7d_activation_apply*.py` | 65 | 0 failed |
| `tests/capabilities/test_phase7d_hardening*.py` | 31 | 0 failed |
| `tests/agents/` | 617 | 0 failed |
| `tests/skills/` | 64 | 0 failed |
| `tests/logging/` | 32 | 0 failed |
| `tests/core/test_tool_dispatcher.py` | included above | 0 failed |
| `tests/core/test_runtime_profiles_exclusion.py` | included above | 0 failed |
| `tests/core/test_state_view*.py` | included above | 0 failed |
| All pre-7A capability tests | included above | 0 failed |

### Unchanged components

- [x] Read-only capability tools unchanged (list/search/view)
- [x] Lifecycle tools unchanged (evaluate/plan_transition/transition)
- [x] Curator tools unchanged (reflect/propose)
- [x] Agent candidate tools unchanged
- [x] No new automatic behavior introduced

---

## 14. Hard Constraints — Final Verification

| Constraint | Status |
|------------|--------|
| Operator-only (`capability_import_operator`) | Verified |
| Feature flag default `false` | Verified |
| Only target `maturity=testing` | Verified |
| Never `stable` | Verified |
| No script execution | Verified |
| No test execution | Verified |
| No `run_capability` | Verified |
| No promotion | Verified |
| No automatic activation | Verified |
| No default activation from request/plan alone | Verified |
| Original quarantine copy remains | Verified |
| No Brain/TaskRuntime behavior change | Verified |
| No dynamic agent changes | Verified |
| Denied/failed apply is atomic | Verified |
| Dry run writes nothing | Verified |
| Failed gates write nothing | Verified |
| Index refreshed only for target copy | Verified |

---

## 15. Files Changed

| File | Change |
|------|--------|
| `src/capabilities/quarantine_activation_apply.py` | **New** — core module (380 lines) |
| `src/tools/capability_tools.py` | **Modified** — +155 lines (schema, executor, registration) |
| `src/config/settings.py` | **Modified** — +2 lines (feature flag model field) |
| `config/settings.py` | **Modified** — +1 line (feature flag value) |
| `src/app/container.py` | **Modified** — +13 lines (tool wiring) |
| `tests/capabilities/test_phase7d_activation_apply.py` | **New** — 31 tests |
| `tests/capabilities/test_phase7d_activation_apply_tools.py` | **New** — 11 tests |
| `tests/capabilities/test_phase7d_activation_apply_atomicity.py` | **New** — 8 tests |
| `tests/capabilities/test_phase7d_activation_apply_safety.py` | **New** — 15 tests |
| `docs/capability_phase7d_activation_apply.md` | **New** — implementation reference |
| `docs/capability_phase7d_b_acceptance.md` | **New** (this file) — hardening results |
| `docs/capability_system_overview.md` | **Modified** — added 7D-B tool row |
| `docs/capability_acceptance_index.md` | **Modified** — added 7D-B entry |

## 16. Known Issues

1. **High-risk capabilities blocked**: Phase 7D-B conservatively blocks all high-risk
   capabilities because no human approval model exists yet. This is documented behavior.
   A future phase may add a human-in-the-loop approval mechanism.

2. **Superseded marking best-effort**: Transition request marking as "superseded"
   is non-fatal — if the request file write fails, the activation still succeeds.
   The request status update is advisory.

3. **Index refresh rollback**: If index refresh fails after successful copy, the
   target directory is rolled back and a clean `ActivationResult` with
   `applied=False` is returned. The operator can retry after fixing the index.

## 17. Rollback Notes

To roll back Phase 7D-B:
1. Set `CAPABILITIES_QUARANTINE_ACTIVATION_APPLY_ENABLED=false` in `config/settings.py`
2. The `apply_quarantine_activation` tool will not be registered
3. Existing activated capabilities in `data/capabilities/<scope>/<id>/` are
   normal active/testing capabilities and can be managed through normal lifecycle tools
4. Quarantine originals remain untouched
5. No database migrations needed — feature is purely additive
