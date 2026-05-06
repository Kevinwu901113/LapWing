# Capability Post-Maintenance Consolidation Audit

**Date:** 2026-05-06
**Purpose:** Consolidate Maintenance A/B/C and verify the end-to-end maintenance flow
**Status:** Accepted

---

## Maintenance A/B/C Lifecycle Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                    MAINTENANCE LIFECYCLE                          │
│                                                                   │
│  Maintenance A (Health Report)                                    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ generate_capability_health_report(store, ...)              │    │
│  │                                                            │    │
│  │  10 check functions:                                       │    │
│  │  - check_index_drift          - check_missing_provenance   │    │
│  │  - check_integrity_mismatch   - check_stale_eval_records   │    │
│  │  - check_stale_trust_roots    - check_quarantine_backlog   │    │
│  │  - check_proposal_backlog     - check_agent_candidate_     │    │
│  │  - check_orphaned_artifacts   - _build_recommendations     │    │
│  │                                                            │    │
│  │  → CapabilityHealthReport                                  │    │
│  │    - findings: list[CapabilityHealthFinding]               │    │
│  │    - recommendations: list[str] (human-readable, inert)    │    │
│  │    - counters: total, by_status, by_maturity, ...          │    │
│  └──────────────────────────┬───────────────────────────────┘    │
│                             │                                     │
│                             ▼                                     │
│  Maintenance B (Repair Queue)                                     │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ RepairQueueStore                                           │    │
│  │                                                            │    │
│  │  create_from_health_report(report, dedupe=True)            │    │
│  │    → list[RepairQueueItem]                                 │    │
│  │                                                            │    │
│  │  Data model: RepairQueueItem                               │    │
│  │    - item_id, status, severity, finding_code               │    │
│  │    - recommended_action (label only, never executed)       │    │
│  │    - action_payload (inert metadata)                       │    │
│  │    - evidence, metadata                                    │    │
│  │                                                            │    │
│  │  Status lifecycle: open → acknowledged → resolved          │    │
│  │                 or: open → dismissed                       │    │
│  │                                                            │    │
│  │  Storage: <data_dir>/repair_queue/<item_id>.json           │    │
│  └──────────────────────────┬───────────────────────────────┘    │
│                             │                                     │
│                             ▼                                     │
│  Maintenance C (Operator Tools)                                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ 6 tools, all gated on:                                     │    │
│  │   - repair_queue_tools_enabled = true (feature flag)       │    │
│  │   - capability_repair_operator tag (profile gate)          │    │
│  │                                                            │    │
│  │  Read-only:                                                │    │
│  │   list_repair_queue_items    — compact summaries           │    │
│  │   view_repair_queue_item     — full detail + action_payload│    │
│  │                                                            │    │
│  │  Write (queue items only):                                 │    │
│  │   create_repair_queue_from_health — health → queue items   │    │
│  │   acknowledge_repair_queue_item  — status → acknowledged   │    │
│  │   resolve_repair_queue_item      — status → resolved       │    │
│  │   dismiss_repair_queue_item      — status → dismissed      │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Report Schema

### CapabilityHealthFinding

| Field | Type | Description |
|-------|------|-------------|
| severity | str | info, warning, error |
| code | str | Finding code (e.g. eval_stale, missing_provenance_legacy) |
| message | str | Human-readable description |
| capability_id | str\|None | Associated capability |
| scope | str\|None | Capability scope |
| details | dict | Context-specific metadata |

### CapabilityHealthReport

| Field | Type | Description |
|-------|------|-------------|
| generated_at | str | ISO 8601 timestamp |
| total_capabilities | int | Total count |
| by_status | dict | Counts by status |
| by_maturity | dict | Counts by maturity |
| findings | list[CapabilityHealthFinding] | All findings |
| recommendations | list[str] | Human-readable recommendations |

---

## Queue Item Schema

### RepairQueueItem

| Field | Type | Description |
|-------|------|-------------|
| item_id | str | Unique ID (rq-xxxxxxxxxxxx) |
| created_at | str | ISO 8601 timestamp |
| source | str | health_report, manual, import_audit, lifecycle, unknown |
| finding_code | str | Original health finding code |
| severity | str | info, warning, error |
| status | str | open, acknowledged, resolved, dismissed |
| title | str | Concise title |
| description | str | Full description |
| recommended_action | str | Label only: inspect, reindex, reeval, add_provenance, etc. |
| action_payload | dict | Inert metadata (never executed) |
| evidence | dict | Finding context |
| capability_id | str\|None | Associated capability |
| scope | str\|None | Capability scope |
| assigned_to | str\|None | Optional assignee |
| updated_at | str\|None | Last status change timestamp |
| resolved_at | str\|None | Resolution timestamp |
| dismissed_at | str\|None | Dismissal timestamp |
| metadata | dict | Operator metadata (actor, reason, etc.) |

---

## Tool Surface Matrix

| Tool | Type | Writes | Profile Required | Risk |
|------|------|--------|-----------------|------|
| list_repair_queue_items | Read | None | capability_repair_operator | low |
| view_repair_queue_item | Read | None | capability_repair_operator | low |
| create_repair_queue_from_health | Write | queue items only | capability_repair_operator | low |
| acknowledge_repair_queue_item | Write | queue item only | capability_repair_operator | low |
| resolve_repair_queue_item | Write | queue item only | capability_repair_operator | low |
| dismiss_repair_queue_item | Write | queue item only | capability_repair_operator | low |

### Forbidden Tools (confirmed absent)

| Tool | Status |
|------|--------|
| run_capability | Not present |
| repair_capability | Not present |
| auto_repair_capability | Not present |
| execute_repair | Not present |
| apply_repair_queue_item | Not present |
| rebuild_index_from_health | Not present |
| promote_from_health | Not present |

---

## Permission Matrix

| Profile | capability_repair_operator tag | Tool Access |
|---------|-------------------------------|-------------|
| capability_repair_operator | Yes | All 6 tools |
| standard | No | None |
| chat_shell | No | None |
| zero_tools | No | None |
| inner_tick | No | None |
| local_execution | No | None |
| compose_proactive | No | None |
| agent_admin_operator | No | None |
| capability_lifecycle_operator | No | None |
| capability_curator_operator | No | None |
| identity_operator | No | None |
| browser_operator | No | None |
| skill_operator | No | None |
| agent_candidate_operator | No | None |
| capability_import_operator | No | None |
| capability_trust_operator | No | None |

Permission is structural: the `capability_repair_operator` tag appears only in `CAPABILITY_REPAIR_OPERATOR_PROFILE.capabilities`. No runtime permission check inside tool executors.

---

## Feature Flag Matrix

| Flag | Default | Effect |
|------|---------|--------|
| capabilities.enabled | true | Master capabilities switch |
| capabilities.repair_queue_tools_enabled | false | Gating Maintenance C tool registration |

Layer 1: `container.py` checks `CAPABILITIES_REPAIR_QUEUE_TOOLS_ENABLED` — if false, tools are never registered.
Layer 2: `RuntimeProfile.capabilities` tag matching — even if registered, only `capability_repair_operator` profile can access them.

---

## Mutation Path Matrix

| Operation | Capability files | Index | Lifecycle | Proposals | Candidates | Trust Roots | Queue items |
|-----------|---------|-------|-----------|-----------|------------|-------------|-------------|
| create_from_health | No | No | No | No | No | No | Yes (new only) |
| acknowledge | No | No | No | No | No | No | Yes (status + metadata) |
| resolve | No | No | No | No | No | No | Yes (status + metadata) |
| dismiss | No | No | No | No | No | No | Yes (status + metadata) |
| list | No | No | No | No | No | No | No |
| view | No | No | No | No | No | No | No |

All other artifact types verified unchanged via SHA256 byte-hash before/after every operation.

---

## No-Repair Proof

1. **No repair execution paths exist.** The 6 repair queue tools only read/write queue item JSON files. No function calls `run_capability`, `repair_capability`, `auto_repair_capability`, `execute_repair`, `apply_repair_queue_item`, `rebuild_index_from_health`, or `promote_from_health`.

2. **recommended_action is a string label only.** It maps to values like "inspect", "reindex", "reeval", "add_provenance", "manual_review" — none of these trigger any dispatch logic. They are display-only recommendations for human operators.

3. **action_payload is inert metadata.** It contains only context from the health finding (source_finding_code, issue, stage, etc.). No executable keys, no command strings, no script references. Validation in `RepairQueueItem.__post_init__` rejects any payload value matching executable patterns, tool-call keys, banned function names, or URL schemes.

4. **Status changes are metadata-only.** Acknowledging, resolving, or dismissing an item changes only the `status` field and timestamps in the queue item JSON. No capability is modified, no index is rebuilt, no lifecycle is transitioned.

5. **Byte-hash verification confirms zero non-queue mutation.** SHA256 hashes of all files under the data directory are computed before and after every operation. Only files under `repair_queue/` may change.

---

## No-Execution Proof

AST-based import verification on `src/capabilities/repair_queue.py`:
- No `subprocess`, `os`, `pexpect`, `shlex`, `pdb` imports
- No `urllib`, `socket`, `http`, `httpx`, `aiohttp`, `requests` imports
- No `openai`, `anthropic`, `langchain`, `instructor` imports
- No `importlib`, `runpy` imports
- No `exec()` or `eval()` calls
- No `run_capability` function

AST-based import verification on `src/tools/repair_queue_tools.py`:
- Same zero-network/zero-execution/zero-LLM profile
- No Brain, TaskRuntime, or StateView imports

---

## Runtime Import Boundary

Only 3 non-capability files import from `src.capabilities/`:

| File | Purpose |
|------|---------|
| `src/tools/capability_tools.py` | Capability operator tools (all phases) |
| `src/tools/repair_queue_tools.py` | Maintenance C repair queue tools |
| `src/app/container.py` | Dependency injection wiring |

---

## E2E Flows Verified

### Flow A: Health Finding to Queue
- Capability created → health report generated → findings produced
- create_repair_queue_from_health → queue items created with correct recommended_action labels
- Dedupe prevents duplicate open items for the same (finding_code, capability_id, scope, action)
- No capability files changed (SHA256 verified)

### Flow B: Operator Lifecycle
- list → view → acknowledge → resolve → dismiss
- Only queue item files change (SHA256 verified)
- Capability maturity/status unchanged
- Index row count invariant
- Trust root files unchanged

### Flow C: Corruption Tolerance
- Corrupt JSON in queue items: skipped in list, None from get_item, no crash
- Empty store: health report succeeds with 0 findings
- Missing data dir: no crash, empty result
- Operations on corrupt items: no non-queue mutation

### Flow D: Permissions and Flags
- Tools absent when not registered
- All 6 tools registered when called
- All have `capability_repair_operator` tag
- Forbidden tools absent
- None store skips registration
- Repair operator profile grants all 6 tools
- All 6 standard profiles denied
- All 9 other operator profiles denied

---

## Remaining Risks

1. **No automated repair.** The maintenance system identifies issues and recommends actions but never executes them. Operators must manually address findings. This is by design but means issue resolution depends on human action.

2. **No scheduling.** Health reports are generated on-demand (via tool or direct call). There is no cron-like scheduler for periodic health checks. This could be addressed in a future phase.

3. **No notification.** When new findings are detected, there is no alerting mechanism (email, Slack, etc.). Operators must proactively check the repair queue.

4. **Queue growth without bound.** Dismissed and resolved items remain on disk indefinitely. No archival or pruning mechanism exists. Over time, the repair_queue directory could grow large.

5. **No cross-capability correlation.** Each finding is treated independently. Patterns across multiple capabilities (e.g., "all imported caps have stale evals") are not surfaced.

---

## Future Maintenance D Guidance

Maintenance D (if pursued) could introduce:

- **Repair execution** for well-defined, low-risk repair actions (e.g., reindex from store, rebuild provenance). These must be:
  - Explicitly approved per-action
  - Feature-gated independently
  - Byte-hash verified before/after
  - Operator-only (capability_repair_operator profile)
  - Never automatic — always operator-initiated

- **Scheduled health checks** with configurable intervals

- **Notification hooks** for new critical/error findings

- **Queue archival/pruning** for items older than N days

- **Health trend tracking** across multiple report generations

Key principle: Repair execution must never be automatic. Every repair action must be operator-initiated, explicitly approved, and verified.
