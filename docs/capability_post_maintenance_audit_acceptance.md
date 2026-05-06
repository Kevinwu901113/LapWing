# Capability Post-Maintenance Consolidation Audit — Acceptance

**Date:** 2026-05-06
**Audit:** Post-Maintenance Consolidation
**Status:** Accepted

---

## Tests Run

| Suite | File | Tests | Pass | Fail |
|-------|------|-------|------|------|
| E2E Health to Queue (Flow A) | `tests/capabilities/test_maintenance_e2e_health_to_queue.py::TestFlowAHealthToQueue` | 5 | 5 | 0 |
| E2E Operator Lifecycle (Flow B) | `tests/capabilities/test_maintenance_e2e_health_to_queue.py::TestFlowBOperatorLifecycle` | 6 | 6 | 0 |
| E2E Corruption Tolerance (Flow C) | `tests/capabilities/test_maintenance_e2e_health_to_queue.py::TestFlowCCorruptionTolerance` | 7 | 7 | 0 |
| E2E Permissions/Flags (Flow D) | `tests/capabilities/test_maintenance_e2e_health_to_queue.py::TestFlowDPermissionsAndFlags` | 9 | 9 | 0 |
| E2E Full Maintenance Flow | `tests/capabilities/test_maintenance_e2e_health_to_queue.py::TestE2EFullMaintenanceFlow` | 3 | 3 | 0 |
| **Subtotal (new E2E)** | | **30** | **30** | **0** |
| Maintenance A (Health) | `tests/capabilities/test_maintenance_health_report.py` | 66 | 66 | 0 |
| Maintenance A (Safety) | `tests/capabilities/test_maintenance_health_safety.py` | 23 | 23 | 0 |
| Maintenance B (Repair Queue) | `tests/capabilities/test_maintenance_repair_queue.py` | 88 | 88 | 0 |
| Maintenance B (Safety) | `tests/capabilities/test_maintenance_repair_queue_safety.py` | 22 | 22 | 0 |
| Maintenance C (Tools) | `tests/capabilities/test_maintenance_repair_queue_tools.py` | 34 | 34 | 0 |
| Maintenance C (Profile) | `tests/capabilities/test_maintenance_repair_queue_operator_profile.py` | 26 | 26 | 0 |
| Maintenance C (Safety) | `tests/capabilities/test_maintenance_repair_queue_tools_safety.py` | 21 | 21 | 0 |
| **Subtotal (Maintenance A+B+C)** | | **280** | **280** | **0** |
| **Grand Total (Maintenance)** | | **310** | **310** | **0** |
| Full capabilities suite | `tests/capabilities/` | 2,583 | 2,583 | 0 |
| Agent suite | `tests/agents/` | 545 | 545 | 0 |
| ToolDispatcher | `tests/core/test_tool_dispatcher.py` | 86 | 86 | 0 |
| RuntimeProfiles | `tests/core/test_runtime_profiles_exclusion.py` | * | * | 0 |
| StateView | `tests/core/test_state_view.py` | 42 | 42 | 0 |
| Skills | `tests/skills/` | * | * | 0 |
| Logging | `tests/logging/` | * | * | 0 |
| **Combined Regression Total** | | **3,353** | **3,353** | **0** |

Full regression completed in 412.60s (~7 min). 0 failures.

---

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `tests/capabilities/test_maintenance_e2e_health_to_queue.py` | Created | 30 E2E tests (Flows A-D + combined) |
| `docs/capability_post_maintenance_audit.md` | Created | Consolidation audit design doc |
| `docs/capability_post_maintenance_audit_acceptance.md` | Created | This document |
| `docs/capability_acceptance_index.md` | Modified | Added Post-Maintenance row |
| `docs/capability_system_overview.md` | Modified | Updated with Maintenance E2E status |

No source code changes. No new tools. No new flags. No behavior changes.

---

## E2E Flow Results

### Flow A: Health Finding to Queue (5 tests, 0 failures)
- Finding to queue item creation: Pass
- Recommended action labels correct: Pass
- Dedupe skips existing open items: Pass
- No capability files changed (SHA256): Pass
- Finding codes preserved in queue: Pass

### Flow B: Operator Lifecycle (6 tests, 0 failures)
- Full lifecycle (list → view → ack → resolve → dismiss): Pass
- Only queue item files change (SHA256): Pass
- Capability/provenance/index/trust roots unchanged: Pass
- View missing item returns not_found: Pass
- Status update missing item returns not_found: Pass
- Actor and reason preserved in metadata: Pass

### Flow C: Corruption Tolerance (7 tests, 0 failures)
- Corrupt JSON skipped in list: Pass
- Corrupt item view returns None: Pass
- Empty store health report succeeds: Pass
- Missing data dir no crash: Pass
- No non-queue mutation on corrupt operations: Pass
- Empty queue list returns []: Pass
- Status update preserves other fields: Pass

### Flow D: Permissions and Flags (9 tests, 0 failures)
- Tools absent when not registered: Pass
- All 6 tools registered when called: Pass
- All tools have capability_repair_operator tag: Pass
- Forbidden tools absent: Pass
- None store skips registration: Pass
- Profile grants tools via capability tag: Pass
- Standard profiles denied (6 profiles): Pass
- Other operator profiles denied (9 profiles): Pass
- create_from_health fails without capability_store: Pass

### Combined E2E (3 tests, 0 failures)
- Full flow health → dismiss: Pass
- Filtered list after status changes: Pass
- Dedupe after resolve allows new items: Pass

---

## No-Mutation Proof

SHA256 byte-hash verification confirms:
- Capability files: unchanged across all operations
- Index: row count invariant
- Lifecycle state: maturity/status unchanged
- Proposals: file hashes invariant
- Agent candidates: file hashes invariant
- Trust roots: file hashes invariant
- No files deleted: comprehensive hash check
- Only `repair_queue/*.json` files are created or modified

---

## No-Execution Proof

### AST Import Audit — `src/capabilities/repair_queue.py`
| Check | Result |
|-------|--------|
| subprocess/os import | Not present |
| urllib/socket/http/httpx/aiohttp/requests import | Not present |
| openai/anthropic/langchain/instructor import | Not present |
| exec()/eval() calls | Not present |
| importlib/runpy import | Not present |
| pexpect/shlex/pdb import | Not present |
| run_capability function | Not present |
| repair/auto_repair/execute functions | Not present |

### AST Import Audit — `src/tools/repair_queue_tools.py`
| Check | Result |
|-------|--------|
| subprocess/os import | Not present |
| urllib/socket/http/httpx/aiohttp/requests import | Not present |
| openai/anthropic/langchain/instructor import | Not present |
| exec()/eval() calls | Not present |
| importlib/runpy import | Not present |
| Brain/TaskRuntime/StateView import | Not present |

---

## Runtime Import Audit

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Only 3 allowed non-capability files:
1. `src/tools/capability_tools.py` — capability operator tools
2. `src/tools/repair_queue_tools.py` — Maintenance C repair queue tools
3. `src/app/container.py` — dependency injection wiring

No other non-capability files import from `src.capabilities/`.

---

## Forbidden Tool Audit

```
grep -rn "def run_capability\|def repair_capability\|def auto_repair_capability\|def execute_repair\|def apply_repair_queue_item\|def rebuild_index_from_health\|def promote_from_health" src/
```

Result: No matches. Zero forbidden functions exist anywhere in `src/`.

---

## Full Regression

```
3353 passed in 412.60s (0:06:52)
```

All 3,353 tests pass. 0 failures. No regressions from existing suites.

---

## Known Issues

None. All 310 maintenance tests (30 new E2E + 280 existing A/B/C) pass with 0 failures.

---

## Rollback Notes

To roll back the Post-Maintenance Consolidation Audit changes:
1. Remove `tests/capabilities/test_maintenance_e2e_health_to_queue.py`
2. Remove `docs/capability_post_maintenance_audit.md`
3. Remove `docs/capability_post_maintenance_audit_acceptance.md`
4. Revert updates to `docs/capability_acceptance_index.md`
5. Revert updates to `docs/capability_system_overview.md`

No source code changes to roll back. No database migrations. No capability store changes.
