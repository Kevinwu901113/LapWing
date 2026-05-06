# Capability Maintenance Repair Queue Tools — Acceptance

**Date:** 2026-05-06
**Phase:** Maintenance C
**Status:** Accepted (Re-hardened 2026-05-06)

---

## Re-hardening Pass

Full hardening pass executed 2026-05-06. All 81 Maintenance C tests re-pass. Full regression 3,352 tests (2,583 capabilities + 545 agents + 86 ToolDispatcher + * RuntimeProfiles + 42 StateView + * skills + 96 logging) — 0 failures. No regressions from new tool surface. Import audit confirms only 3 allowed non-capability imports (`capability_tools.py`, `repair_queue_tools.py`, `container.py`). All hard constraints re-verified.

---

## Tests Run

| Suite | File | Tests | Pass | Fail |
|-------|------|-------|------|------|
| Repair Queue Tools (functional) | `tests/capabilities/test_maintenance_repair_queue_tools.py` | 34 | 34 | 0 |
| Repair Queue Operator Profile | `tests/capabilities/test_maintenance_repair_queue_operator_profile.py` | 26 | 26 | 0 |
| Repair Queue Tools Safety | `tests/capabilities/test_maintenance_repair_queue_tools_safety.py` | 21 | 21 | 0 |
| **Subtotal (Maintenance C)** | | **81** | **81** | **0** |
| Full capabilities suite | `tests/capabilities/` | 2,583 | 2,583 | 0 |
| Agent suite | `tests/agents/` | 545 | 545 | 0 |
| ToolDispatcher | `tests/core/test_tool_dispatcher.py` | 86 | 86 | 0 |
| RuntimeProfiles exclusion | `tests/core/test_runtime_profiles_exclusion.py` | * | * | 0 |
| StateView | `tests/core/test_state_view*` | 42 | 42 | 0 |
| Skills | `tests/skills/` | * | * | 0 |
| Logging | `tests/logging/` | 96 | 96 | 0 |

All suites pass. Zero failures. Re-hardened & confirmed.

---

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `src/tools/repair_queue_tools.py` | Created | 6 tool definitions, schemas, executors, registration function |
| `src/core/runtime_profiles.py` | Modified | Added `CAPABILITY_REPAIR_OPERATOR_PROFILE` + `_PROFILES` entry |
| `src/config/settings.py` | Modified | Added `repair_queue_tools_enabled` to `CapabilitiesConfig` + `_ENV_MAP` |
| `config/settings.py` | Modified | Added `CAPABILITIES_REPAIR_QUEUE_TOOLS_ENABLED` module constant |
| `config.toml` | Modified | Added `repair_queue_tools_enabled = false` |
| `src/app/container.py` | Modified | Added wiring block inside `CAPABILITIES_ENABLED` block |
| `tests/capabilities/test_maintenance_repair_queue_tools.py` | Created | 34 functional tests |
| `tests/capabilities/test_maintenance_repair_queue_operator_profile.py` | Created | 26 profile/permission tests |
| `tests/capabilities/test_maintenance_repair_queue_tools_safety.py` | Created | 21 safety tests |
| `docs/capability_maintenance_repair_queue_tools.md` | Created | Design document |
| `docs/capability_maintenance_repair_queue_tools_acceptance.md` | Created | This document |

---

## 1. Feature Flag & Registration

### Verified properties

| Property | Status | Test |
|----------|--------|------|
| Flag defaults to false | Pass | `test_flag_defaults_false` |
| Flag present in config model | Pass | `test_flag_present_in_config` |
| Tools absent when flag off | Pass | Structural — registration only called inside `if CAPABILITIES_REPAIR_QUEUE_TOOLS_ENABLED` |
| 6 tools registered when flag on | Pass | `test_six_tools_registered`, `test_exactly_six_tools` |
| All tools tagged `capability_repair_operator` | Pass | `test_tools_have_correct_capability_tag` |
| All tools low risk | Pass | `test_all_tools_low_risk` |
| None store skips registration | Pass | `test_none_store_skips_registration` |
| 7 forbidden tools absent | Pass | `test_forbidden_tools_absent` |

---

## 2. Profile & Permission Model

### Verified properties

| Property | Status | Test |
|----------|--------|------|
| Profile exists with correct name | Pass | `test_profile_exists` |
| Profile has `capability_repair_operator` tag | Pass | `test_profile_has_capability_repair_operator_tag` |
| Profile has no tool_names | Pass | `test_profile_has_no_tool_names` |
| Profile no shell_policy | Pass | `test_profile_no_shell_policy` |
| Profile no include_internal | Pass | `test_profile_no_internal_tools` |
| All 6 tools accessible via profile | Pass | `test_profile_accesses_all_repair_tools` |
| Only repair tools granted | Pass | `test_profile_does_not_grant_non_repair_tools` |
| Standard profiles denied (6) | Pass | `test_standard_profile_denied[standard/chat_shell/zero_tools/inner_tick/local_execution/compose_proactive]` |
| Other operator profiles denied (9) | Pass | `test_other_operator_profile_denied[agent_admin/lifecycle/curator/identity/browser/skill/candidate/import/trust]` |
| local_execution denied | Pass | `test_local_execution_denied` |
| task_execution alias denied | Pass | `test_task_execution_alias_denied` |

---

## 3. Tool Behavior

### list_repair_queue_items

| Property | Status |
|----------|--------|
| Empty list returns [] | Pass |
| Returns compact summaries (no action_payload expansion) | Pass |
| Filter by status | Pass |
| Filter by severity | Pass |
| Filter by capability_id | Pass |
| Filter by recommended_action | Pass |
| Respects limit | Pass |
| Default limit 50 | Pass |

### view_repair_queue_item

| Property | Status |
|----------|--------|
| Existing item returns full detail with action_payload | Pass |
| Missing item returns clean not_found | Pass |
| Empty item_id returns error | Pass |

### create_repair_queue_from_health

| Property | Status |
|----------|--------|
| Creates items from health report | Pass |
| Dedupes existing open items (default) | Pass |
| dedupe=False creates duplicates | Pass |
| No capability_store returns error | Pass |

### acknowledge_repair_queue_item

| Property | Status |
|----------|--------|
| Changes status to acknowledged | Pass |
| Stores reason and actor in metadata | Pass |
| Missing item returns not_found | Pass |

### resolve_repair_queue_item

| Property | Status |
|----------|--------|
| Changes status to resolved | Pass |
| Sets resolved_at | Pass |
| Stores reason in metadata | Pass |
| Missing item returns not_found | Pass |

### dismiss_repair_queue_item

| Property | Status |
|----------|--------|
| Changes status to dismissed | Pass |
| Sets dismissed_at | Pass |
| Does not delete item | Pass |
| Stores reason in metadata | Pass |
| Missing item returns not_found | Pass |

### Update preservation

| Property | Status |
|----------|--------|
| Acknowledge preserves action_payload | Pass |
| Resolve preserves evidence | Pass |
| Dismiss preserves recommended_action | Pass |

---

## 4. Safety: No Mutation

| Artifact type | Mutated? | Verified |
|---------------|----------|----------|
| Capability files | No | Byte-hash before/after (4 ops) |
| Index | No | Row count invariant |
| Lifecycle state | No | Maturity/status invariant |
| Proposals | No | File hash invariant |
| Agent candidates | No | File hash invariant |
| Trust roots | No | File hash invariant |
| Files deleted | No | Comprehensive hash: zero deletions |
| All artifacts comprehensive | No | Byte-hash all ops combined |

---

## 5. Safety: No Execution

| Check | Result |
|-------|--------|
| `subprocess` import | Not present |
| `os` import | Not present |
| `urllib`/`socket`/`http`/`httpx`/`aiohttp`/`requests` import | Not present |
| `openai`/`anthropic`/`langchain`/`instructor` import | Not present |
| `exec()`/`eval()` calls | Not present |
| `importlib`/`runpy` import | Not present |
| `subprocess`/`pexpect`/`shlex`/`pdb` import | Not present |
| `run_capability` function | Not present |
| `repair_capability`/`auto_repair_capability`/`execute_repair` functions | Not present |
| `apply_repair_queue_item`/`rebuild_index_from_health`/`promote_from_health` functions | Not present |
| Brain/TaskRuntime/StateView import | Not present |
| recommended_action executed | Not executed (string-only, no dispatch) |
| action_payload executed | Not executed (not expanded in list, inert in view) |

All AST-level import verification (not grep-based).

---

## 6. Runtime Import Audit

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Allowed non-capability imports:
- `src/tools/capability_tools.py` — expected (tool registration)
- `src/tools/repair_queue_tools.py` — expected (new Maintenance C tools)
- `src/app/container.py` — expected (DI wiring)

---

## 7. Hard Constraints Verification

| Constraint | Status |
|------------|--------|
| No repair execution | Confirmed — zero repair/capability mutation paths |
| No capability mutation | Confirmed — byte-hash verified |
| No index rebuild | Confirmed — row count invariant |
| No lifecycle transition | Confirmed — maturity/status invariant |
| No proposal/candidate/trust-root mutation | Confirmed — file hashes invariant |
| No artifact deletion | Confirmed — zero deletions |
| No script execution | Confirmed — AST import audit |
| No network | Confirmed — AST import audit |
| No LLM judge | Confirmed — AST import audit |
| No run_capability | Confirmed — no such function |
| No Brain/TaskRuntime/StateView change | Confirmed — no imports from those modules |
| Reason is optional on status updates | Confirmed — consistent with RepairQueueStore.update_status API |

---

## Known Issues

None. All 81 Maintenance C tests pass.

---

## Rollback Notes

To roll back:
1. Remove `src/tools/repair_queue_tools.py`
2. Revert changes in:
   - `src/core/runtime_profiles.py` (profile + _PROFILES entry)
   - `src/config/settings.py` (CapabilitiesConfig field + _ENV_MAP entry)
   - `config/settings.py` (module constant)
   - `config.toml` (feature flag)
   - `src/app/container.py` (wiring block)
3. Delete test files: `test_maintenance_repair_queue_tools.py`, `test_maintenance_repair_queue_operator_profile.py`, `test_maintenance_repair_queue_tools_safety.py`
4. Documentation files may be retained or removed as desired.

No database migrations, no capability store changes, no repair queue store changes required for rollback.
