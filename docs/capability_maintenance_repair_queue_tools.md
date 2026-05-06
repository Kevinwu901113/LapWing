# Capability Maintenance Repair Queue Tools — Design

**Date:** 2026-05-06
**Phase:** Maintenance C
**Status:** Implemented (Re-hardened 2026-05-06)

---

## Overview

Operator-only tools for viewing and managing repair queue item status. The repair queue (Maintenance B) provides an inert data model and store; these tools expose that store through the tool registry under a dedicated capability tag.

No repair is executed. No capability mutation occurs. Tools manage queue items only.

---

## Feature Flag

```
[capabilities]
repair_queue_tools_enabled = false   # default off
```

Requires `capabilities.enabled = true`. Independently gated — not nested inside any other flag.

Module constant: `CAPABILITIES_REPAIR_QUEUE_TOOLS_ENABLED`

---

## Permission Model

**Capability tag:** `capability_repair_operator`

**Runtime profile:** `CAPABILITY_REPAIR_OPERATOR_PROFILE` (name: `capability_repair_operator`)

Not granted to: standard, default, chat, local_execution, browser_operator, identity_operator, capability_import_operator, capability_lifecycle_operator, capability_curator_operator, capability_trust_operator, agent_candidate_operator, or any other profile.

Permission is structural: the tag appears only in `CAPABILITY_REPAIR_OPERATOR_PROFILE.capabilities`. No runtime permission check inside tool executors.

---

## Tools

### 1. list_repair_queue_items
- **Input:** status, severity, capability_id, recommended_action (all optional), limit (default 50, max 200)
- **Behavior:** Read-only, deterministic ordering, compact summaries
- **Return:** item_id, created_at, status, severity, finding_code, capability_id, scope, title, recommended_action, assigned_to (no action_payload expansion)

### 2. view_repair_queue_item
- **Input:** item_id (required)
- **Behavior:** Read-only, returns full item details including inert action_payload
- **Return:** Full item dict; clean `not_found` for missing items

### 3. create_repair_queue_from_health
- **Input:** dedupe (bool, default true)
- **Behavior:** Generates health report, creates queue items for findings. Writes only repair_queue item files.
- **Return:** created, skipped, total_findings, item summaries, recommendations

### 4. acknowledge_repair_queue_item
- **Input:** item_id (required), actor (optional), reason (optional)
- **Behavior:** status -> acknowledged. Writes only item JSON. No repair.

### 5. resolve_repair_queue_item
- **Input:** item_id (required), actor (optional), reason (optional)
- **Behavior:** status -> resolved. Writes only item JSON. No verification repair.

### 6. dismiss_repair_queue_item
- **Input:** item_id (required), actor (optional), reason (optional)
- **Behavior:** status -> dismissed. Writes only item JSON. No deletion. No repair.

---

## Forbidden Tools (NOT implemented)

- repair_capability
- execute_repair
- auto_repair_capability
- apply_repair_queue_item
- rebuild_index_from_health
- promote_from_health
- run_capability

---

## Hard Constraints

- No repair execution.
- No capability mutation (byte-hash verified).
- No index rebuild.
- No lifecycle transition.
- No proposal/candidate/trust-root mutation.
- No artifact deletion.
- No script execution.
- No network.
- No LLM judge.
- No run_capability.
- No Brain/TaskRuntime/StateView import.
- action_payload remains inert metadata only.

---

## Writes

Only `<data_dir>/repair_queue/<item_id>.json` files are written. Atomic writes via `.tmp` → rename. All other artifact types are byte-hash verified unchanged.

---

## Files

| File | Purpose |
|------|---------|
| `src/tools/repair_queue_tools.py` | Tool definitions, schemas, executors, registration |
| `src/core/runtime_profiles.py` | `CAPABILITY_REPAIR_OPERATOR_PROFILE` + `_PROFILES` entry |
| `src/config/settings.py` | `CapabilitiesConfig.repair_queue_tools_enabled` + `_ENV_MAP` entry |
| `config/settings.py` | `CAPABILITIES_REPAIR_QUEUE_TOOLS_ENABLED` module constant |
| `config.toml` | `[capabilities] repair_queue_tools_enabled = false` |
| `src/app/container.py` | Wiring inside `CAPABILITIES_ENABLED` block |
| `tests/capabilities/test_maintenance_repair_queue_tools.py` | Functional tests (34 tests) |
| `tests/capabilities/test_maintenance_repair_queue_operator_profile.py` | Profile/permission tests (26 tests) |
| `tests/capabilities/test_maintenance_repair_queue_tools_safety.py` | Safety tests (21 tests) |
