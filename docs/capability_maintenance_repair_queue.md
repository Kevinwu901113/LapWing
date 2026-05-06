# Capability Maintenance Repair Queue — Design

**Date:** 2026-05-05
**Phase:** Maintenance B
**Status:** Implemented

---

## Purpose

The repair queue converts health report findings into explicit, trackable repair queue items for operator review. It bridges read-only health diagnosis (Maintenance A) with structured issue tracking.

No automatic repair. No mutation of capabilities. Queue items are inert data records.

---

## Data Model

### RepairQueueItem

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `item_id` | `str` | Yes | Unique identifier, format `rq-<12 hex chars>` |
| `created_at` | `str` | Yes | ISO 8601 creation timestamp |
| `source` | `str` | Yes | Origin: `health_report`, `manual`, `import_audit`, `lifecycle`, `unknown` |
| `finding_code` | `str` | Yes | Health finding code that generated this item |
| `severity` | `str` | Yes | `info`, `warning`, or `error` |
| `status` | `str` | Yes | `open`, `acknowledged`, `resolved`, or `dismissed` |
| `title` | `str` | Yes | Human-readable summary |
| `description` | `str` | Yes | Detailed description from the finding |
| `recommended_action` | `str` | Yes | Label only: `inspect`, `reindex`, `reeval`, `repair_metadata`, `add_provenance`, `quarantine_review`, `archive`, `manual_review`, `unknown` |
| `action_payload` | `dict` | No | Inert metadata — never executable |
| `evidence` | `dict` | No | Supporting details from the health finding |
| `capability_id` | `str` | No | Associated capability, if any |
| `scope` | `str` | No | Capability scope, if any |
| `assigned_to` | `str` | No | Operator assigned to the item |
| `updated_at` | `str` | No | Last modification timestamp |
| `resolved_at` | `str` | No | When resolved |
| `dismissed_at` | `str` | No | When dismissed |
| `metadata` | `dict` | No | Arbitrary metadata |

### Validation

- `severity` must be one of: `info`, `warning`, `error`
- `status` must be one of: `open`, `acknowledged`, `resolved`, `dismissed`
- `source` must be one of: `health_report`, `manual`, `import_audit`, `lifecycle`, `unknown`
- `recommended_action` must be one of the 9 valid actions
- `item_id` must not contain path traversal (`..`, `/`, `\`)
- `action_payload` values are scanned for executable-like content (shell commands, `import`, `subprocess`, `exec()`, `eval()`) — these are rejected

---

## Status Semantics

| Status | Meaning |
|--------|---------|
| `open` | New item, not yet reviewed by an operator |
| `acknowledged` | Operator has seen and accepted the item |
| `resolved` | Underlying issue has been addressed |
| `dismissed` | Item was reviewed and intentionally not acted upon |

Status transitions:
- `open` → `acknowledged`, `resolved`, `dismissed`
- `acknowledged` → `resolved`, `dismissed`
- `resolved` and `dismissed` are terminal (but can be re-opened by creating a new item)

---

## Action Semantics

Recommended actions are **labels only**. They describe the category of work an operator might perform. They never trigger any automated behavior.

| Action | Description |
|--------|-------------|
| `inspect` | Operator should inspect the capability manually |
| `reindex` | Operator should rebuild the capability index |
| `reeval` | Operator should re-run the capability evaluator |
| `repair_metadata` | Operator should repair corrupt metadata files |
| `add_provenance` | Operator should create a provenance record |
| `quarantine_review` | Operator should review a quarantined capability |
| `archive` | Operator should consider archiving the capability |
| `manual_review` | Operator should manually review the finding |
| `unknown` | No specific action determined |

---

## Health Finding Mapping

When `create_from_health_report()` is called, finding codes map to recommended actions:

| Finding Code Pattern | Recommended Action |
|---------------------|-------------------|
| `missing_provenance_*` | `add_provenance` |
| `integrity_mismatch` | `manual_review` |
| `eval_missing`, `eval_stale` | `reeval` |
| `trust_root_*` | `manual_review` |
| `quarantine_*` | `quarantine_review` |
| `proposal_pending`, `proposal_stale`, `proposal_high_risk_pending` | `manual_review` |
| `proposal_corrupt` | `repair_metadata` |
| `candidate_*` | `manual_review` |
| `index_missing_row`, `index_stale_row` | `reindex` |
| `orphaned_*` | `manual_review` |
| (unknown code) | `manual_review` |

The `action_payload` carries forward the finding code and relevant details (`stage`, `status`, `proposal_id`, `candidate_id`, `trust_root_id`, `file`, `directory`, `issue`) from the health finding.

---

## Deduplication Rules

When `create_from_health_report(report, dedupe=True)` is called:

1. For each finding, a dedup key is computed: `(finding_code, capability_id, scope, recommended_action)`
2. If an **open** queue item exists with the same dedup key (matching finding_code + capability_id + action), the finding is skipped
3. Resolved or dismissed items do NOT block new items — the same finding can recur
4. If `dedupe=False`, every finding creates a new item regardless

---

## Storage Layout

```
data/capabilities/repair_queue/
    rq-<12 hex chars>.json
```

Each file is a JSON object with all fields from `RepairQueueItem.to_dict()`.

---

## Read/Write Scope

**What the repair queue can write:**
- `data/capabilities/repair_queue/*.json` — queue item files only

**What the repair queue can read:**
- `CapabilityHealthReport` findings (in-memory, no filesystem read)
- `data/capabilities/repair_queue/*.json` — existing queue items for dedup and listing

**What the repair queue must NEVER touch:**
- Capability directories, manifests, CAPABILITY.md
- Provenance records, signature records, import reports
- Index database
- Eval records, version snapshots
- Proposals, agent candidates, trust roots
- Lifecycle state, mutation logs

---

## Non-Execution Guarantee

1. **No `run_capability`.** No function by that name exists in the module.
2. **No subprocess.** `subprocess`, `os.system`, `pexpect` are not imported.
3. **No network.** `urllib`, `socket`, `requests`, `http.client`, `httpx` are not imported.
4. **No LLM.** `openai`, `anthropic`, `langchain`, `instructor` are not imported.
5. **No eval/exec.** `exec()`, `eval()`, `importlib`, `runpy` are not used.
6. **No shell.** No shell or command execution libraries imported.
7. **action_payload is inert.** Values containing shell commands, Python code imports, subprocess calls, or exec/eval are rejected at construction time.
8. **Recommended actions are labels.** They are strings, never callables, never dispatched.
9. **Status updates only touch queue item files.** No capability, index, lifecycle, proposal, candidate, or trust root mutation.

---

## Future Operator Tool Notes

No tools are added in Maintenance B. Future phases may add:
- `list_repair_queue` — list queue items with filters
- `view_repair_queue_item` — view a single item
- `acknowledge_repair_queue_item` — mark as acknowledged
- `resolve_repair_queue_item` — mark as resolved
- `dismiss_repair_queue_item` — mark as dismissed

These would be operator-only, feature-gated, and would only call `RepairQueueStore` methods. They would never execute repairs.
