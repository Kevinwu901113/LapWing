# Capability Maintenance Health Report — Maintenance A

**Date:** 2026-05-05
**Phase:** Maintenance A
**Status:** Implemented

---

## Purpose

The Capability Health Report is a **read-only, deterministic** maintenance audit layer for the entire capability system. It generates findings and recommendations based on filesystem and index state without performing any mutation, execution, or automated repair.

This is an operator-facing diagnostic tool — it identifies issues but never fixes them.

---

## Checks

### 1. Inventory Counts

Counts capabilities by status, maturity, and scope. Populates aggregate counters: `quarantined_count`, `testing_count`, `stable_count`, `broken_count`, `repairing_count`.

### 2. Missing Provenance

Checks every capability directory for `provenance.json`. Severity varies by context:
- **Quarantined** without provenance → error (import should have created it)
- **Imported/activated** without provenance → warning
- **Legacy/manual** without provenance → info

### 3. Integrity Mismatch

For capabilities with provenance records, compares the current tree hash (`compute_capability_tree_hash`) against the stored `source_content_hash`. Reports mismatches as warnings. Does **not** update provenance.

### 4. Stale Eval Records

Checks `testing` and `stable` capabilities for recent evaluation records. Reports missing eval as warning, stale eval (>30 days by default) as info. Does **not** create eval records.

### 5. Stale Trust Roots

Checks trust roots for:
- Revoked status (warning)
- Disabled status (info)
- Expired (warning)
- Nearing expiry within configurable window (info)

Does **not** revoke, disable, or modify any trust root.

### 6. Quarantine Backlog

Traces each quarantined capability through the pipeline:
```
no audit → audit exists → review exists → transition request → activation plan → applied
```

Reports gaps at each stage. Does **not** create any pipeline artifacts.

### 7. Proposal Backlog

Checks proposals for:
- Unapplied proposals (info)
- Stale proposals (>90 days by default) (info)
- High-risk proposals requiring approval (warning)
- Corrupt proposal files (warning)

Does **not** apply or modify any proposals.

### 8. Agent Candidate Backlog

Checks agent candidates for:
- Pending high-risk candidates without evidence (warning)
- Pending candidates (info)
- Approved but not saved (info)
- Rejected candidates (info, suggests archiving)

Does **not** modify any candidates.

### 9. Index Drift

Compares store directories against index entries. Detects:
- Entries in store but missing from index (missing rows)
- Entries in index but no store directory (stale rows)

Does **not** rebuild the index.

### 10. Orphaned Artifacts

Checks for:
- Corrupt trust root JSON files
- Quarantine directories without `CAPABILITY.md` but with pipeline artifacts
- Empty quarantine directories

Does **not** delete anything.

---

## Report Schema

`generate_capability_health_report(store, *, index, trust_root_store, candidate_store, data_dir, ...) -> CapabilityHealthReport`

### CapabilityHealthReport

| Field | Type | Description |
|-------|------|-------------|
| `generated_at` | `str` | ISO 8601 timestamp |
| `total_capabilities` | `int` | Total capabilities in store |
| `by_status` | `dict[str, int]` | Count by status (active, disabled, archived, quarantined) |
| `by_maturity` | `dict[str, int]` | Count by maturity (draft, testing, stable, broken, repairing) |
| `by_scope` | `dict[str, int]` | Count by scope (global, user, workspace, session) |
| `quarantined_count` | `int` | Quarantined capabilities |
| `testing_count` | `int` | Testing maturity capabilities |
| `stable_count` | `int` | Stable maturity capabilities |
| `broken_count` | `int` | Broken maturity capabilities |
| `repairing_count` | `int` | Repairing maturity capabilities |
| `proposals_count` | `int` | Total proposal directories |
| `agent_candidates_count` | `int` | Total agent candidates |
| `trust_roots_count` | `int` | Total trust roots |
| `stale_eval_count` | `int` | Capabilities with stale/missing eval |
| `stale_provenance_count` | `int` | Missing provenance + integrity mismatches |
| `integrity_mismatch_count` | `int` | Provenance hash mismatches |
| `missing_provenance_count` | `int` | Capabilities without provenance |
| `stale_trust_root_count` | `int` | Problematic trust roots |
| `orphaned_artifact_count` | `int` | Orphaned/corrupt artifacts |
| `index_drift_count` | `int` | Store/index inconsistencies |
| `findings` | `list[CapabilityHealthFinding]` | Detailed findings |
| `recommendations` | `list[str]` | Non-executable text recommendations |

---

## Findings Schema

### CapabilityHealthFinding

| Field | Type | Description |
|-------|------|-------------|
| `severity` | `str` | `info`, `warning`, or `error` |
| `code` | `str` | Machine-readable finding code |
| `message` | `str` | Human-readable description |
| `capability_id` | `str \| None` | Related capability ID |
| `scope` | `str \| None` | Related scope |
| `details` | `dict` | Additional structured data |

Finding codes follow the pattern `<domain>_<condition>`, e.g. `index_missing_row`, `integrity_mismatch`, `quarantine_no_audit`.

---

## Recommendations Semantics

Recommendations are **text strings only**. They describe what an operator might consider doing but contain no executable code, no Python imports, no shell commands, and no structured action objects. They are purely informational.

Example: `"Index drift detected (2 entries). Consider running a manual index rebuild (CapabilityIndex.rebuild_from_store) to restore consistency."`

---

## Read-Only Guarantee

Every function in `health.py` is strictly read-only:

- No file writes, moves, or deletes
- No manifest mutation
- No index rebuild (`rebuild_from_store` is never called)
- No lifecycle transitions
- No proposal creation or application
- No agent candidate mutation
- No trust root creation, modification, or revocation
- No script execution
- No subprocess / `os.system`
- No network calls
- No LLM or AI inference
- No `run_capability`

All checks are deterministic given the same filesystem state (except `generated_at` timestamp).

---

## Future Repair Queue

A future Maintenance phase may add a repair queue that consumes health report findings and offers guided remediation actions. The health report itself will remain read-only — the repair queue would be a separate module that reads findings and applies operator-approved fixes.

Potential repair actions (NOT implemented):
- Rebuild index from store
- Create missing provenance for legacy capabilities
- Update provenance integrity status after manual review
- Archive rejected agent candidates
- Clean up orphaned quarantine artifacts
- Transition stale proposals to archived state
