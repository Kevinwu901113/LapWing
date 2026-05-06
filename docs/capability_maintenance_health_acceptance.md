# Capability Maintenance Health Report — Acceptance

**Date:** 2026-05-05
**Phase:** Maintenance A (Hardened)
**Status:** Accepted

---

## Tests Run

| Suite | File | Tests | Pass | Fail |
|-------|------|-------|------|------|
| Health Report | `tests/capabilities/test_maintenance_health_report.py` | 66 | 66 | 0 |
| Health Safety | `tests/capabilities/test_maintenance_health_safety.py` | 23 | 23 | 0 |
| **Total** | | **89** | **89** | **0** |

### Test Breakdown

#### Report Tests (66)
| Category | Count | Key Tests |
|----------|-------|-----------|
| Core functionality | 40 | Empty system, status/maturity/scope counts, missing provenance, integrity mismatch, stale eval/trust roots, quarantine/proposal/candidate backlogs, index drift, orphaned artifacts, recommendations, determinism, full report integration |
| Corruption tolerance | 9 | Corrupt manifest/provenance/proposal/trust root JSON, missing capability.md/manifest/index, empty quarantine dir, unreadable file |
| Counting correctness | 6 | Status/maturity/scope sums, quarantine excludes active copies, distinct maturity counts, proposals independent |
| Integrity/provenance | 3 | Volatile dirs excluded, provenance integrity status not mutated, provenance not rewritten |
| Backlog depth | 4 | All quarantine pipeline stages, all trust root states, high-risk proposals, pending candidates |
| Determinism | 4 | Deterministic on corrupt data, finding ordering, recommendation ordering, recommendation serialization |

#### Safety Tests (23)
| Category | Count | Key Tests |
|----------|-------|-----------|
| No-mutation proof | 10 | SHA256 file hashes before/after, no index rebuild, no lifecycle transition, no proposals/candidates/trust roots mutated |
| No-execution proof | 4 | No run_capability, no subprocess/os.system, no network, no LLM |
| Robustness | 2 | check functions never raise, generate_report never raises |
| Hardening | 7 | Comprehensive byte-hash all artifact types, no eval/version/quarantine/provenance mutation, no orphan deletion, no lifecycle log entries, recommendation safety, extended exec audit |

---

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `src/capabilities/health.py` | Created | Core health report module (2 data models, 1 main function, 9 check functions) |
| `tests/capabilities/test_maintenance_health_report.py` | Created → Hardened | 66 functional tests (40 original + 26 hardening) |
| `tests/capabilities/test_maintenance_health_safety.py` | Created → Hardened | 23 safety/proof tests (14 original + 9 hardening) |
| `src/capabilities/__init__.py` | Updated | Added exports for `CapabilityHealthReport`, `CapabilityHealthFinding`, `generate_capability_health_report` |
| `docs/capability_maintenance_health.md` | Created | Purpose, checks, schemas, guarantees |
| `docs/capability_maintenance_health_acceptance.md` | Updated | Hardening results (this file) |
| `docs/capability_system_overview.md` | Updated | Added Maintenance A entry |
| `docs/capability_acceptance_index.md` | Updated | Added Maintenance A entry |

---

## Health Checks Implemented

| # | Check | Finding Codes | Severity |
|---|-------|---------------|----------|
| 1 | Inventory counts | (aggregate counters only) | — |
| 2 | Missing provenance | `missing_provenance_quarantined`, `missing_provenance_imported`, `missing_provenance_legacy`, `missing_provenance_archived` | error/warning/info |
| 3 | Integrity mismatch | `integrity_mismatch` | warning |
| 4 | Stale eval records | `eval_missing`, `eval_stale` | warning/info |
| 5 | Stale trust roots | `trust_root_revoked`, `trust_root_expired`, `trust_root_disabled`, `trust_root_nearing_expiry` | warning/info |
| 6 | Quarantine backlog | `quarantine_no_audit`, `quarantine_audit_pending_review`, `quarantine_review_pending_request`, `quarantine_request_pending_plan`, `quarantine_plan_pending_apply` | warning/info |
| 7 | Proposal backlog | `proposal_pending`, `proposal_stale`, `proposal_high_risk_pending`, `proposal_corrupt` | warning/info |
| 8 | Agent candidate backlog | `candidate_pending`, `candidate_high_risk_no_evidence`, `candidate_high_risk_pending`, `candidate_approved_not_saved`, `candidate_rejected` | warning/info |
| 9 | Index drift | `index_missing_row`, `index_stale_row` | warning |
| 10 | Orphaned artifacts | `orphaned_corrupt_trust_root`, `orphaned_quarantine_artifacts`, `orphaned_empty_quarantine` | warning/info |

---

## Read-Only / No-Mutation Proof (Hardened)

### Byte-Hash Verification

`test_comprehensive_byte_hash_no_mutation_all_artifact_types` computes SHA256 hashes of all artifact types before and after `generate_capability_health_report()`:
- Capabilities of all statuses (active, disabled, archived, quarantined)
- Provenance records, eval records, import reports
- Quarantine pipeline artifacts (audit, review, request, plan)
- Proposals, agent candidates, trust roots
- Index database

All hash comparisons: **zero bytes mutated**.

### Individual Safety Tests

| Test | What It Verifies |
|------|-----------------|
| `test_no_files_mutated_by_generate_report` | SHA256 file hashes before and after report generation are identical |
| `test_no_index_rebuilt_by_health_report` | Index count stays 0 after report generation (no rebuild) |
| `test_no_lifecycle_transition_called_by_health` | Manifest maturity/status unchanged after report |
| `test_no_proposals_created_by_health` | No proposals directory created |
| `test_no_agent_candidates_mutated_by_health` | Candidate approval state and evidence unchanged |
| `test_no_trust_roots_mutated_by_health` | Trust root status unchanged, no new metadata |
| `test_no_eval_records_written_by_health` | No new eval files written |
| `test_no_version_snapshots_created_by_health` | No version snapshots created |
| `test_no_quarantine_artifacts_mutated_by_health` | Byte-hash of all quarantine artifacts unchanged |
| `test_no_provenance_integrity_status_updated_by_health` | Provenance files byte-identical after report |
| `test_no_orphaned_artifacts_deleted_by_health` | Corrupt files preserved, not deleted |
| `test_no_lifecycle_mutation_log_entries_from_health` | No unexpected directories or log entries |
| `test_check_functions_do_not_mutate_files` | All individual check functions pass file hash verification |
| `test_check_index_drift_does_not_rebuild_index` | Index count stays 0 after drift check |
| `test_check_functions_never_raise` | All check functions handle edge cases without raising |
| `test_generate_report_never_raises` | Report generation on empty and populated stores never raises |

### No-Execution Proof

| Test | What It Verifies |
|------|-----------------|
| `test_no_run_capability_exists_in_health_module` | No function named `run_capability` in health module |
| `test_health_does_not_import_subprocess_or_os_system` | No dangerous execution imports |
| `test_no_network_in_health_module` | No networking library imports (urllib, socket, requests, etc.) |
| `test_no_llm_judge_in_health_module` | No LLM/AI-related imports |
| `test_no_exec_eval_importlib_runpy_in_health` | Extended audit: no exec(), eval(), importlib, runpy |

---

## Recommendation Safety Proof

`test_recommendation_safety_no_execution_fields` verifies all recommendations are text-only data records. No recommendation object contains fields like `auto_fix`, `apply`, `execute`, `action`, `command`, `script`, or `repair`. Recommendations are human-readable strings suitable for display only.

---

## Corruption Tolerance Proof

| Test | Corruption Type | Behavior Verified |
|------|----------------|-------------------|
| `test_corrupt_manifest_json_handled` | Binary garbage in manifest.json | Capability skipped with warning finding, report still generated |
| `test_corrupt_provenance_json_handled` | Binary garbage in provenance.json | Treated as missing provenance, no crash |
| `test_corrupt_proposal_json_handled` | Binary garbage in proposal.json | Corrupt proposal finding emitted, report continues |
| `test_corrupt_trust_root_json_handled` | Binary garbage in trust_root.json | Corrupt trust root finding emitted, report continues |
| `test_missing_capability_md_handled` | No capability.md in directory | Directory still enumerated, findings emitted |
| `test_missing_manifest_json_handled` | No manifest.json in directory | Graceful skip, no crash |
| `test_missing_index_db_handled` | No index database file | Treated as empty index, drift check emits findings |
| `test_empty_directory_in_quarantine_handled` | Empty subdirectory in quarantine | No crash, orphaned finding emitted |
| `test_unreadable_file_handled` | File with restrictive permissions | Graceful skip, no crash |

All corruption cases: report generation completes successfully. No exception propagates to caller.

---

## Counting Correctness Proof

| Test | Invariant |
|------|----------|
| `test_status_counts_sum_to_total` | Sum of active + disabled + archived + quarantined = total_capabilities |
| `test_maturity_counts_sum_to_total` | Sum of draft + testing + stable + broken + repairing = total_capabilities |
| `test_scope_counts_sum_to_total` | Sum of all scope buckets = total_capabilities |
| `test_quarantine_count_excludes_active_copies` | Quarantined count only counts quarantined caps, not active copies of same id |
| `test_distinct_maturity_counts` | Each capability counted in exactly one maturity bucket |
| `test_proposals_counted_independently` | Proposal count independent of capability count (proposals are separate artifacts) |

---

## Integrity / Provenance Checks

| Test | Guarantee |
|------|----------|
| `test_volatile_dirs_excluded_from_integrity` | Evals, traces, versions, quarantine artifact dirs excluded from integrity hash comparison |
| `test_provenance_integrity_status_not_updated_by_check` | `check_integrity_mismatch` reports mismatch but does NOT rewrite provenance |
| `test_provenance_not_rewritten_by_health` | No provenance file is written or updated by any health operation |

---

## Backlog Depth Checks

| Test | Coverage |
|------|----------|
| `test_all_quarantine_pipeline_stages_detected` | All 5 quarantine stages: no-audit, audit-pending-review, review-pending-request, request-pending-plan, plan-pending-apply |
| `test_all_trust_root_states_detected_but_not_mutated` | Revoked, expired, disabled, nearing-expiry trust roots all detected |
| `test_high_risk_proposal_detected` | High-risk pending proposals surfaced with appropriate severity |
| `test_pending_candidate_detected` | Pending agent candidates with missing evidence surfaced |

---

## Determinism Proof

| Test | Guarantee |
|------|----------|
| `test_deterministic_same_inputs_same_findings` | Same store state → identical finding set (excluding generated_at) |
| `test_deterministic_on_corrupt_data` | Determinism holds even with corrupt artifacts present |
| `test_finding_ordering_deterministic` | Findings sorted deterministically (by severity, code, capability_id) |
| `test_recommendation_ordering_deterministic` | Recommendations sorted deterministically |
| `test_recommendations_serialize_deterministically` | Recommendation dict representation stable across calls |

---

## Runtime Import Audit

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Results:
- `src/app/container.py` — expected (DI wiring)
- `src/tools/capability_tools.py` — expected (tool registration)

**No unexpected imports.** Health module is not imported by any non-capability module.

---

## Known Issues

None. All 89 tests pass. No regressions in any existing test suite.

---

## Rollback Notes

To roll back:
1. Remove `src/capabilities/health.py`
2. Remove the 3 export lines from `src/capabilities/__init__.py`:
   - Import: `from src.capabilities.health import (...)`
   - `__all__` entries: `CapabilityHealthReport`, `CapabilityHealthFinding`, `generate_capability_health_report`
   - Docstring reference
3. Delete test files: `tests/capabilities/test_maintenance_health_report.py`, `tests/capabilities/test_maintenance_health_safety.py`
4. Documentation files may be retained or removed as desired.

No database migrations, no feature flags, no configuration changes required for rollback.
