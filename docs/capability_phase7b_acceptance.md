# Phase 7B Acceptance — Quarantine Review & Audit (Hardened)

**Date:** 2026-05-03
**Phase:** 7B — Quarantined Capability Audit / Review
**Status:** Accepted (Hardened)

---

## 1. Test Results

### Phase 7B Tests (including hardening)

| File | Tests | Pass | Fail |
|------|-------|------|------|
| `test_phase7b_quarantine_audit.py` | 22 | 22 | 0 |
| `test_phase7b_quarantine_tools.py` | 42 | 42 | 0 |
| `test_phase7b_quarantine_safety.py` | 34 | 34 | 0 |
| **Total Phase 7B** | **98** | **98** | **0** |

Phase 7B safety test growth: 15 → 34 (+19 hardening tests)

### Full Regression Suite

| Suite | Count |
|-------|-------|
| `tests/capabilities/` | 1,180 passed |
| `tests/agents/` | 544 passed |
| `tests/core/test_tool_dispatcher.py` | passed |
| `tests/core/test_runtime_profiles_exclusion.py` | passed |
| `tests/core/test_state_view*.py` (4 files) | passed |
| `tests/skills/` | 64 passed |
| `tests/logging/` | 32 passed |
| **Total** | **1,952 passed, 0 failed** |

### Regression Verification

- [x] All Phase 7A tests pass (50/50)
- [x] All capability tests pass (1,180/1,180)
- [x] All agent tests pass (544/544)
- [x] ToolDispatcher tests pass
- [x] RuntimeProfile tests pass
- [x] StateView tests pass
- [x] Skills/logging tests pass
- [x] Read-only capability tools unchanged
- [x] Lifecycle tools unchanged
- [x] Curator tools unchanged
- [x] Auto proposal unchanged
- [x] Agent candidate tools unchanged
- [x] No new automatic behavior

---

## 2. Files Changed

### New Files

| File | Purpose |
|------|---------|
| `src/capabilities/quarantine_review.py` | Core audit/review logic with hardened static scanning |
| `tests/capabilities/test_phase7b_quarantine_audit.py` | Audit-specific tests (22 tests) |
| `tests/capabilities/test_phase7b_quarantine_tools.py` | Tool registration + behaviour tests (42 tests) |
| `tests/capabilities/test_phase7b_quarantine_safety.py` | Safety guarantee tests (34 tests, including hardening) |
| `docs/capability_phase7b_quarantine_review.md` | Review semantics, schema, constraints |
| `docs/capability_phase7b_acceptance.md` | This document |

### Modified Files

| File | Change |
|------|--------|
| `src/capabilities/__init__.py` | Export new types and functions |
| `src/tools/capability_tools.py` | Add 4 tool schemas, executors, `register_quarantine_review_tools()` |
| `src/app/container.py` | Wire `register_quarantine_review_tools` behind `external_import_enabled` gate |
| `docs/capability_system_overview.md` | Add Phase 7B row, update component map, module count |
| `docs/capability_acceptance_index.md` | Add Phase 7B row, update test counts |

---

## 3. Feature Flag Matrix

| Flag | Value | Effect |
|------|-------|--------|
| `capabilities.enabled` | `false` | All capability tools disabled |
| `capabilities.enabled` + `external_import_enabled` | `true` + `false` | Phase 7A/7B tools not registered |
| `capabilities.enabled` + `external_import_enabled` | `true` + `true` | Phase 7A + 7B tools registered |

Phase 7B reuses the Phase 7A feature flag. No new flag added.

---

## 4. Permission Matrix

| Profile | list | view | audit | mark |
|---------|:---:|:---:|:---:|:---:|
| `CAPABILITY_IMPORT_OPERATOR_PROFILE` | Yes | Yes | Yes | Yes |
| STANDARD_PROFILE | No | No | No | No |
| CHAT_SHELL_PROFILE | No | No | No | No |
| INNER_TICK_PROFILE | No | No | No | No |
| LOCAL_EXECUTION_PROFILE | No | No | No | No |
| BROWSER_OPERATOR_PROFILE | No | No | No | No |
| IDENTITY_OPERATOR_PROFILE | No | No | No | No |
| CAPABILITY_LIFECYCLE_OPERATOR_PROFILE | No | No | No | No |
| CAPABILITY_CURATOR_OPERATOR_PROFILE | No | No | No | No |
| AGENT_CANDIDATE_OPERATOR_PROFILE | No | No | No | No |
| SKILL_OPERATOR_PROFILE | No | No | No | No |
| AGENT_ADMIN_OPERATOR_PROFILE | No | No | No | No |

All 4 tools use `capability="capability_import_operator"`. Only `CAPABILITY_IMPORT_OPERATOR_PROFILE` grants access. No other profile — including other operator profiles — grants these tools.

---

## 5. Tool Surface Audit

All registered Phase 7B tools:
- `list_quarantined_capabilities`
- `view_quarantine_report`
- `audit_quarantined_capability`
- `mark_quarantine_review`

### Prohibited Tools (verified absent)

None of the following exist in any Phase 7B registration:
- [x] `activate_quarantined_capability`
- [x] `promote_quarantined_capability`
- [x] `install_quarantined_capability`
- [x] `run_quarantined_capability`
- [x] `run_capability`
- [x] `execute_capability`
- [x] `run_imported_capability`
- [x] `promote_imported_capability`
- [x] `update_capability_from_remote`
- [x] `registry_search_capability`

---

## 6. Report-Only Proof

### list_quarantined_capabilities
- [x] Reads only `data/capabilities/quarantine/`
- [x] Never calls `CapabilityStore.list()`
- [x] Excludes active/user/workspace/global capabilities
- [x] Compact summaries only — no raw paths, no script contents
- [x] Deterministic ordering (imported_at descending)
- [x] Ignores directories without `import_report.json`
- [x] Corrupt import_report.json → skip, no crash

### view_quarantine_report
- [x] Reads import_report and review/audit metadata only
- [x] `source_path_hash` only — never raw source path
- [x] File names and counts only — never file contents
- [x] No full CAPABILITY.md body in report
- [x] Clean `not_found` for missing capability_id
- [x] No mutation of any file

### audit_quarantined_capability
- [x] `write_report=false` → writes nothing
- [x] `write_report=true` → writes only `quarantine_audit_reports/<audit_id>.json`
- [x] Does not mutate manifest.json
- [x] Does not mutate maturity/status
- [x] Does not create eval records in active eval storage
- [x] Does not create version snapshots
- [x] Does not refresh active index
- [x] Does not call `CapabilityLifecycleManager`
- [x] Does not promote or activate

### mark_quarantine_review
- [x] Writes only `quarantine_reviews/<review_id>.json`
- [x] Leaves `status=quarantined` for all 3 review statuses
- [x] Leaves `maturity=draft` for all 3 review statuses
- [x] Does not move files
- [x] Does not create active scope directory
- [x] Does not call `CapabilityLifecycleManager`
- [x] Does not refresh active index
- [x] Does not make retrieval default-visible
- [x] `approved_for_testing` is review status only

---

## 7. Quarantine Isolation

Verified for all 3 review statuses (`needs_changes`, `approved_for_testing`, `rejected`):

- [x] Status remains `quarantined`
- [x] Maturity remains `draft`
- [x] Excluded from default `CapabilityStore.list`
- [x] Excluded from default `CapabilityIndex.search`
- [x] Excluded from `CapabilityRetriever.retrieve`
- [x] Excluded from `StateView` summaries
- [x] Blocked by lifecycle promotion/run policy (`validate_promote` denies quarantined)
- [x] Non-executable (`validate_run` denies quarantined)

---

## 8. Static Scanning Safety (Hardened)

### File safety guarantees
- [x] Max scan size: 1 MB (`_MAX_SCAN_FILE_SIZE = 1_048_576`)
- [x] Files exceeding limit: skipped, flagged as `large_file_skipped` (info)
- [x] Binary files: detected via null-byte check (first 8 KiB), skipped, flagged as `binary_file_skipped` (info)
- [x] Invalid UTF-8: gracefully handled, flagged as `unreadable_file` (warning)
- [x] Symlinks inside quarantine: rejected/skipped, flagged as `symlink_in_quarantine` (warning), never followed
- [x] Empty files: handled safely
- [x] Hidden files/directories: flagged as `hidden_file` (warning)
- [x] Unexpected directories: flagged as `unknown_directory` (warning)
- [x] Corrupt quarantine directories: parse failure → `rejected`
- [x] Corrupt import_report.json: skip, no crash

### Content privacy guarantees
- [x] Script contents never returned in tool output
- [x] Script contents never written verbatim into audit report
- [x] Only finding code, message, location, and file_name are written
- [x] No raw absolute paths emitted in any tool output
- [x] No raw source import path emitted
- [x] No full CAPABILITY.md body in report
- [x] Secrets in package text: flagged by pattern code only, never copied
- [x] CoT-like fields in metadata not emitted
- [x] Prompt injection text: treated as data/finding code only, body not emitted

---

## 9. Execution Safety

`quarantine_review.py` contains zero:
- [x] `subprocess` calls
- [x] `os.system` calls
- [x] `os.popen` calls
- [x] `exec` calls
- [x] `eval` calls
- [x] `importlib` usage
- [x] `runpy` usage
- [x] `requests` / `httpx` / `urllib` usage
- [x] `openai` / `anthropic` SDK usage
- [x] Shell execution helper calls

The only regex matches for "subprocess", "os.system", "importlib" are in `_SHELL_DANGER_PATTERNS` — used to *detect* dangerous patterns in scanned text, not to execute them.

Verified by test:
- [x] audit does not execute side-effect scripts
- [x] view does not execute scripts
- [x] mark review does not execute scripts
- [x] list does not execute scripts

---

## 10. Source Privacy / Report Privacy

- [x] `import_report.json` stores `source_path_hash` (SHA256), never raw path
- [x] `view_quarantine_report` exposes only `source_path_hash`
- [x] Audit report contains no raw source path
- [x] Review report contains no raw source path
- [x] File summaries contain filenames only, not absolute paths
- [x] Secrets in package text are flagged by code, not copied
- [x] Prompt injection text appears as finding code only, body not emitted

---

## 11. Path Safety

- [x] `capability_id` with `/` rejected via `_validate_id_token`
- [x] `capability_id` with `..` rejected via `_validate_id_token`
- [x] `capability_id` with `\` rejected
- [x] `audit_id` is generated via `uuid.uuid4().hex[:12]` — path-safe
- [x] `review_id` is generated via `uuid.uuid4().hex[:12]` — path-safe
- [x] All writes stay under `quarantine/<id>/quarantine_audit_reports/` or `quarantine/<id>/quarantine_reviews/`
- [x] No writes outside `data/capabilities/quarantine/`
- [x] Corrupt quarantine directories: clean parse failure, not crash
- [x] Missing manifest/CAPABILITY.md: clean parse failure
- [x] Duplicate audit/review IDs: UUID prevents collision

---

## 12. Runtime Import Audit

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Result: Only `src/tools/capability_tools.py` and `src/app/container.py`.

No imports from:
- [x] Brain
- [x] TaskRuntime
- [x] StateViewBuilder
- [x] SkillExecutor
- [x] ToolDispatcher
- [x] Agent modules
- [x] Dynamic agent runtime paths

---

## 13. Known Issues

None.

---

## 14. Rollback Notes

To roll back Phase 7B:
1. Remove the `register_quarantine_review_tools()` call in `container.py`
2. Remove the 4 tool schemas/executors/registration from `capability_tools.py`
3. Remove exports from `src/capabilities/__init__.py`
4. `src/capabilities/quarantine_review.py` can remain or be removed — no runtime effect unless called

No data migration needed. Audit reports and reviews are additive files in quarantine directories.
