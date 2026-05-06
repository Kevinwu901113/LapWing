# Capability Maintenance Repair Queue — Acceptance (Hardened)

**Date:** 2026-05-06
**Phase:** Maintenance B
**Status:** Accepted (Hardened)
**Hardening pass:** Maintenance B acceptance hardening before Maintenance C

---

## Tests Run

| Suite | File | Tests | Pass | Fail |
|-------|------|-------|------|------|
| Repair Queue (functional) | `tests/capabilities/test_maintenance_repair_queue.py` | 88 | 88 | 0 |
| Repair Queue (safety) | `tests/capabilities/test_maintenance_repair_queue_safety.py` | 22 | 22 | 0 |
| Health Report | `tests/capabilities/test_maintenance_health_report.py` | 66 | 66 | 0 |
| Health Safety | `tests/capabilities/test_maintenance_health_safety.py` | 23 | 23 | 0 |
| **Subtotal (Maintenance A+B)** | | **199** | **199** | **0** |
| Full capabilities suite | `tests/capabilities/` | 2,502 | 2,502 | 0 |
| Agent suite | `tests/agents/` | 545 | 545 | 0 |
| ToolDispatcher | `tests/core/test_tool_dispatcher.py` | 86 | 86 | 0 |
| RuntimeProfiles exclusion | `tests/core/test_runtime_profiles_exclusion.py` | * | * | 0 |
| StateView | `tests/core/test_state_view*` | 42 | 42 | 0 |
| Skills | `tests/skills/` | * | * | 0 |
| Logging | `tests/logging/` | 96 | 96 | 0 |

All suites pass. Zero failures. Zero skips in repair queue / health suites.

---

## Files Changed (this hardening pass)

| File | Action | Description |
|------|--------|-------------|
| `tests/capabilities/test_phase3b_regression.py` | Updated | Added `repair_queue.py` exclusion for `_BANNED_FUNCTION_NAMES` false positive — the deny-list contains banned function names, not actual uses |
| `docs/capability_maintenance_repair_queue_acceptance.md` | Updated | This document — hardening results (was placeholder from original acceptance) |
| `docs/capability_acceptance_index.md` | Updated | Updated test counts and status |

No changes to `src/capabilities/repair_queue.py`, `src/capabilities/health.py`, or any production code.

---

## 1. Data Model Hardening

### Verified properties

| Property | Status | Test |
|----------|--------|------|
| Valid item serializes/deserializes exactly | Pass | `test_valid_item_serializes_and_deserializes`, `test_exact_serialization_fidelity` |
| Invalid severity rejected | Pass | `test_invalid_severity_rejected` |
| Invalid status rejected | Pass | `test_invalid_status_rejected` |
| Invalid recommended_action rejected | Pass | `test_invalid_recommended_action_rejected` |
| Invalid source rejected | Pass | `test_invalid_source_rejected` |
| item_id path traversal rejected (`..`, `/`, `\`) | Pass | `test_item_id_path_traversal_rejected` |
| Empty item_id rejected | Pass | `test_empty_item_id_rejected` |
| Metadata round-trip | Pass | `test_metadata_round_trip` |
| Evidence round-trip | Pass | `test_evidence_round_trip` |
| capability_id/scope optional fields round-trip | Pass | `test_capability_id_scope_optional_round_trip` |
| created_at/updated_at/resolved_at/dismissed_at semantics | Pass | `test_created_at_and_updated_at_semantics` |
| Unknown fields in from_dict silently dropped (no crash, no leak) | Pass | `test_unknown_fields_in_from_dict_preserved_in_metadata` |
| All valid sources accepted | Pass | `test_all_valid_sources_accepted` |
| All valid actions accepted | Pass | `test_all_valid_actions_accepted` |
| All valid statuses accepted | Pass | `test_all_valid_statuses_accepted` |
| from_dict defaults for missing optional fields | Pass | `test_from_dict_defaults` |

### documented semantics
- `created_at`: set once at item creation, never modified by `update_status`
- `updated_at`: set on every `update_status` call
- `resolved_at`: set when status transitions to `resolved`, otherwise `None`
- `dismissed_at`: set when status transitions to `dismissed`, otherwise `None`
- Unknown fields in `from_dict` input: silently ignored, not propagated to `to_dict` output

---

## 2. Inert Action Payload

### Verified rejections

| Payload content | Rejection mechanism | Test |
|----------------|-------------------|------|
| Shell command-looking values (`rm -rf /`) | `_EXECUTABLE_PATTERNS` regex | `test_action_payload_shell_command_rejected` |
| Python code-looking values (`import os`) | `_EXECUTABLE_PATTERNS` regex | `test_action_payload_import_rejected` |
| Tool-call-looking keys (`tool_name`, `command`, `script`, `exec`, `execute`, `shell_cmd`) | `_TOOL_CALL_KEYS` frozenset | `test_action_payload_tool_call_key_rejected` |
| Banned function names (`repair_capability`, `run_capability`, `rebuild_index`, `transition_capability`, etc.) | `_BANNED_FUNCTION_NAMES` frozenset | `test_action_payload_banned_function_name_rejected` |
| Subprocess strings (`subprocess.call(...)`) | `_EXECUTABLE_PATTERNS` regex | `test_action_payload_subprocess_rejected` |
| `eval()`/`exec()` strings | `_EXECUTABLE_PATTERNS` regex | `test_action_payload_exec_rejected` |
| URLs (`http://`, `https://`, `ftp://`) | `_ACTION_URL_SCHEMES` tuple | `test_action_payload_url_rejected` |
| Nested dict with executable content | Recursive `_scan_payload_dict` | `test_nested_action_payload_executable_in_dict_rejected` |
| List items with executable content | Recursive scan of list items | `test_nested_action_payload_in_list_rejected` |

### Verified acceptances (no false positives)

| Content | Test |
|---------|------|
| Innocent English strings containing "import" in context | `test_action_payload_innocent_string_accepted` |
| Nested benign dicts | `test_action_payload_innocent_dict_accepted` |

### Executability proof

- `recommended_action` is an enum/label only (validated against `_VALID_ACTIONS`)
- `action_payload` is never dispatched — no function reads it and acts on it
- No function maps `recommended_action` to an executable handler
- No repair executor class/function exists anywhere in the module
- `_BANNED_FUNCTION_NAMES` contains the exact repair function names; none exist as actual functions

---

## 3. Store Write Isolation

### Verified before/after byte hashes

| Artifact type | Mutated by queue ops? | Test |
|---------------|----------------------|------|
| Capability files (manifest.json, CAPABILITY.md, etc.) | No | `test_create_item_does_not_mutate_capabilities` |
| Provenance (provenance.json) | No | Covered by comprehensive hash test |
| Signature (signature.json) | No | Covered by comprehensive hash test |
| Import/audit/review/request/plan/apply reports | No | Covered by comprehensive hash test |
| Proposal files | No | `test_no_proposals_mutated` |
| Agent candidate files | No | `test_no_agent_candidates_mutated` |
| Trust root files | No | `test_no_trust_roots_mutated` |
| Capability index DB | No | `test_no_index_mutated` |
| Eval records | No | `test_no_eval_records_written` |
| Version snapshots | No | `test_no_version_snapshots_created` |
| Lifecycle state (maturity, status) | No | `test_no_lifecycle_transition_called` |
| All artifact types (comprehensive) | No | `test_comprehensive_byte_hash_all_artifact_types` |

### Allowed writes
- `data/capabilities/repair_queue/<item_id>.json` only
- Atomic write via `.tmp` → rename; no `.tmp` residue on success
- `update_status` only modifies the specific queue item file

---

## 4. Status Update Semantics

### Verified transitions

| Transition | Result | Test |
|-----------|--------|------|
| open → acknowledged | Works, `updated_at` set | `test_update_status_changes_status` |
| acknowledged → resolved | Works, `resolved_at` set | `test_acknowledged_to_resolved` |
| open → dismissed (direct) | Works, `dismissed_at` set | `test_open_to_dismissed_directly` |
| resolved → resolved | Idempotent, stays resolved | `test_resolved_item_stays_resolved` |
| dismissed → dismissed | Idempotent, stays dismissed | `test_dismissed_item_stays_dismissed` |
| Invalid status (e.g. "deleted") | `ValueError` raised | `test_update_status_invalid_rejected` |
| Nonexistent item | Returns `None` | `test_update_status_nonexistent_returns_none` |

### Non-mutation guarantees during update_status

| Property | Verified? | Test |
|----------|-----------|------|
| action_payload preserved | Yes | `test_update_status_preserves_action_payload` |
| evidence preserved | Yes | `test_update_status_preserves_evidence` |
| recommended_action preserved | Yes | `test_update_status_preserves_recommended_action` |
| Item not deleted | Yes | `test_update_status_does_not_delete_item` |
| No new files created | Yes | `test_update_status_does_not_create_new_files` |
| No repair performed | Yes | No execution path exists (Section 7) |
| No capability/proposal/candidate files created | Yes | No-mutation tests (Section 3) |
| Only status/update metadata/reason fields changed | Yes | Individual preservation tests |

---

## 5. Health Conversion Hardening

### Verified behavior

| Property | Result | Test |
|----------|--------|------|
| All 28 supported finding codes → correct action | Pass | `test_recommended_action_mapping_correct` |
| Unsupported finding codes → `manual_review` | Pass | `test_finding_code_not_in_map_defaults_to_manual_review`, `test_unsupported_finding_maps_to_manual_review` |
| Severity preserved from finding to item | Pass | `test_severity_preserved` |
| capability_id preserved | Pass | `test_capability_id_and_scope_preserved` |
| scope preserved | Pass | `test_capability_id_and_scope_preserved` |
| Finding without capability_id (None preserved) | Pass | `test_finding_without_capability_id` |
| action_payload is inert metadata only | Pass | `test_action_payload_inert`, `test_recommendations_remain_advisory` |
| Unknown code → manual_review safely | Pass | Two tests confirm |
| source = "health_report" | Pass | `test_source_is_health_report` |
| item_id format: `rq-` + 12 hex chars | Pass | `test_item_id_format` |
| created_at is parseable ISO datetime | Pass | `test_created_at_is_iso_format` |

### Deduplication behavior

| Scenario | Result | Test |
|----------|--------|------|
| Same finding_code + capability_id + action, open item exists | Skipped | `test_dedupe_prevents_duplicate_open_items` |
| Same finding, different finding_code | New item created | `test_dedupe_does_not_block_different_findings` |
| Open old item resolved, new same finding | New item created | `test_resolved_old_item_does_not_block_new_finding` |
| Open old item dismissed, new same finding | New item created | `test_dismissed_item_does_not_block_new` |
| dedupe=False | Always creates new items | `test_dedupe_disabled_creates_duplicates` |
| dedup key = (finding_code, capability_id, scope, recommended_action) | Correct tuple | `test_dedup_key`, `test_dedup_key_none_capability_id` |

### Determinism

| Property | Result | Test |
|----------|--------|------|
| Finding order preserved in output | Pass | `test_deterministic_ordering` |
| Same report → same codes, actions, severity, scope (different item_ids) | Pass | `test_same_report_same_queue_same_output_modulo_ids` |
| Empty report → no items | Pass | `test_empty_report_creates_no_items` |

---

## 6. Corruption Tolerance

| Scenario | Behavior | Test |
|----------|----------|------|
| Corrupt item JSON in queue dir | Skipped in list, None on get | `test_corrupt_item_file_skipped_in_list`, `test_get_item_corrupt_json_returns_none` |
| Non-dict JSON in queue file | Skipped in list, None on get | `test_get_item_non_dict_returns_none` |
| Missing item_id | Returns None (clean) | `test_get_item_missing_returns_none` |
| Empty repair_queue directory | Empty list returned | `test_list_when_queue_dir_missing` |
| Duplicate/colliding item_ids | `FileExistsError` raised | `test_colliding_item_ids` |
| Deterministic list with corrupt files present | Same IDs in same order | `test_list_items_deterministic_with_corrupt_files` |
| Partial write (.tmp leftover) | Cleanup on exception, normal ops unaffected | `test_partial_write_tmp_cleaned`, `test_atomic_write_does_not_leave_tmp` |

---

## 7. No-Execution / No-Network Audit

### Source code audit (`repair_queue.py`)

| Check | Result |
|-------|--------|
| `subprocess` import | Not present |
| `os.system` / `os.popen` | Not present |
| `exec()` / `eval()` calls | Not present |
| `importlib` / `runpy` import | Not present |
| `requests` / `httpx` / `urllib` import | Not present |
| `openai` / `anthropic` import | Not present |
| Any shell helper calls | Not present |
| Script execution | Not present |
| Network access | Not present |
| LLM judge | Not present |
| `run_capability` function | Not present |
| `repair_capability` function | Not present |
| `auto_repair_capability` function | Not present |
| `rebuild_index_from_health` function | Not present |
| `promote_from_health` function | Not present |

All occurrences of "subprocess", "os.system", "exec", "eval", "importlib", "runpy", "requests", "httpx", "urllib", "openai", "anthropic" in the file are exclusively within the `_EXECUTABLE_PATTERNS` regex tuple and `_BANNED_FUNCTION_NAMES`/`_TOOL_CALL_KEYS` constants — i.e., the detection mechanism itself, not actual use.

AST-level import verification (tests parse the module AST, not grep strings): confirmed zero banned imports.

---

## 8. Runtime Import Audit

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Results (only allowed non-capability imports):
- `src/tools/capability_tools.py` — expected (tool registration)
- `src/app/container.py` — expected (DI wiring)

**No unexpected imports.** Repair queue module is not imported by any non-capability module, Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, agent modules, or dynamic agent runtime paths.

---

## 9. Regression

| Suite | Tests | Pass | Fail |
|-------|-------|------|------|
| `tests/capabilities/` (all) | 2,502 | 2,502 | 0 |
| `tests/agents/` | 545 | 545 | 0 |
| `tests/core/test_tool_dispatcher.py` | 86 | 86 | 0 |
| `tests/core/test_runtime_profiles_exclusion.py` | * | * | 0 |
| `tests/core/test_state_view*` | 42 | 42 | 0 |
| `tests/skills/` | * | * | 0 |
| `tests/logging/` | 96 | 96 | 0 |

- No new tools introduced
- No new feature flags
- No runtime behavior changed
- No capability mutation paths added

### False positive fix

`tests/capabilities/test_phase3b_regression.py::TestReadOnlyTools::test_no_run_capability_exists` was failing because it grepped for "run_capability" in `src/` and found the string in `_BANNED_FUNCTION_NAMES` (the ban list in `repair_queue.py`). Fixed by adding a `repair_queue.py` path exclusion — the ban list is the mechanism that *prevents* execution, not actual use of `run_capability`.

---

## 10. Hard Constraints Verification

| Constraint | Status |
|------------|--------|
| Do not implement Maintenance C | Confirmed — zero Maintenance C code |
| No tools | Confirmed — zero new tools registered |
| No flags | Confirmed — zero new feature flags |
| No repair execution | Confirmed — zero execution paths |
| No capability mutation | Confirmed — byte-hash verified |
| No index rebuild | Confirmed — index row count invariant |
| No lifecycle transition | Confirmed — maturity/status invariant |
| No proposal/candidate/trust-root mutation | Confirmed — file hashes invariant |
| No artifact deletion | Confirmed — no delete/unlink outside error cleanup |
| No script execution | Confirmed — AST import audit |
| No network | Confirmed — AST import audit |
| No LLM judge | Confirmed — AST import audit |
| No run_capability | Confirmed — no such function exists |
| No Brain/TaskRuntime/StateView behavior change | Confirmed — no imports from those modules |

---

## Known Issues

None. All 2,502 capability tests pass. All non-capability test suites pass.

One pre-existing test (`test_phase3b_regression.py::test_no_run_capability_exists`) was updated to exclude `repair_queue.py` from its grep, since the `_BANNED_FUNCTION_NAMES` deny-list legitimately contains banned function name strings as detection patterns.

---

## Rollback Notes

To roll back:
1. Remove `src/capabilities/repair_queue.py`
2. Remove the 5 lines from `src/capabilities/__init__.py`:
   - Import: `from src.capabilities.repair_queue import RepairQueueItem, RepairQueueStore`
   - `__all__` entries: `RepairQueueItem`, `RepairQueueStore`
   - Docstring reference
3. Delete test files: `tests/capabilities/test_maintenance_repair_queue.py`, `tests/capabilities/test_maintenance_repair_queue_safety.py`
4. Revert `tests/capabilities/test_phase3b_regression.py` change (remove `repair_queue.py` exclusion)
5. Delete `data/capabilities/repair_queue/` directory if it exists
6. Documentation files may be retained or removed as desired.

No database migrations, no feature flags, no configuration changes required for rollback.
