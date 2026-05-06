# Phase 7B: Quarantine Capability Review

**Date:** 2026-05-03
**Status:** Accepted
**Scope:** Read-only audit and review reporting for quarantined capabilities

---

## 1. Purpose

Phase 7B adds explicit operator-only review/audit tooling for quarantined imported capabilities. It is strictly report-only — no activation, no promotion, no execution.

## 2. Quarantine Review Semantics

### Review Statuses

| Status | Meaning |
|--------|---------|
| `needs_changes` | Audit found issues that must be addressed before further consideration |
| `approved_for_testing` | Review report says this quarantined package may be considered later by an explicit operator-only bridge |
| `rejected` | Package is rejected and remains quarantined |

### No Activation Guarantee

`approved_for_testing` is a **review label only**. It does NOT:
- Change `capability.status` from `quarantined`
- Change `capability.maturity` from `draft`
- Call `CapabilityLifecycleManager`
- Update the active capability index
- Create active scope directories
- Make the capability retrievable by default
- Make the capability visible in `StateView`
- Create version snapshots
- Create eval records
- Grant any permissions

### Future Activation Requirements

To move a quarantined capability from quarantine to active testing:
1. Import (Phase 7A) — capability lands in `data/capabilities/quarantine/<id>/`
2. Audit (Phase 7B) — deterministic safety scan, report written
3. Review (Phase 7B) — operator marks `approved_for_testing`
4. Explicit promotion bridge (future Phase) — operator invokes an explicit command to promote

No automated path exists between phases. Each step requires an explicit operator action with a dedicated tool.

## 3. Audit Report Schema

```json
{
  "capability_id": "string",
  "audit_id": "audit_<hex>",
  "created_at": "ISO 8601 timestamp",
  "passed": "boolean — no errors found",
  "risk_level": "low | medium | high",
  "findings": [
    {
      "severity": "info | warning | error",
      "code": "string — finding code",
      "message": "string — human-readable description",
      "location": "string — file/section reference",
      "source": "evaluator | policy | file_scan | metadata",
      "details": {}
    }
  ],
  "recommended_review_status": "needs_changes | approved_for_testing | rejected",
  "remediation_suggestions": ["string"]
}
```

### Sanitization Guarantees

Audit reports never contain:
- Raw absolute import paths (only `source_path_hash` in import report)
- Script file bodies
- Test file bodies
- Trace contents
- Eval record bodies
- Raw logs
- Hidden CoT-like fields

## 4. Tools

| Tool | Input | Behavior | Capability Tag |
|------|-------|----------|---------------|
| `list_quarantined_capabilities` | risk_level?, review_status?, imported_after?, limit? | Lists quarantined capabilities from quarantine dir only | `capability_import_operator` |
| `view_quarantine_report` | capability_id, include_findings?, include_files_summary? | Returns import_report.json and findings | `capability_import_operator` |
| `audit_quarantined_capability` | capability_id, write_report? | Deterministic local audit, writes quarantine_audit_report.json | `capability_import_operator` |
| `mark_quarantine_review` | capability_id, review_status, reason, reviewer?, expires_at? | Writes review decision to quarantine_reviews/ | `capability_import_operator` |

## 5. Storage Layout

```
data/capabilities/quarantine/<id>/
├── import_report.json                    # Phase 7A
├── manifest.json                         # Phase 7A (forced status=quarantined, maturity=draft)
├── CAPABILITY.md                         # Phase 7A
├── scripts/                              # Phase 7A (copied, never executed)
├── tests/                                # Phase 7A
├── examples/                             # Phase 7A
├── quarantine_audit_reports/             # Phase 7B
│   └── <audit_id>.json
└── quarantine_reviews/                   # Phase 7B
    └── <review_id>.json
```

## 6. Feature Flags & Permissions

| Gate | Value |
|------|-------|
| Feature flag | `capabilities.external_import_enabled` (reuses Phase 7A flag) |
| Operator profile | `CAPABILITY_IMPORT_OPERATOR_PROFILE` |
| Default profile access | Denied — standard/default/chat/local_execution/browser/identity excluded |

## 7. Hard Constraints Verified

- [x] No activation — `mark_quarantine_review` never changes status/maturity
- [x] No promotion — no lifecycle transitions
- [x] No `run_capability` — tool not implemented
- [x] No script execution — files read as text only
- [x] No Python import from packages — `CapabilityParser` only
- [x] No test execution from packages — tests/ files flagged but not run
- [x] No network — pure filesystem I/O
- [x] No LLM judge — deterministic heuristics only
- [x] No default retrieval — quarantined excluded from default list/search/StateView
- [x] No Brain/TaskRuntime behavior change
- [x] No dynamic agent changes
- [x] Review decision does not grant permissions
