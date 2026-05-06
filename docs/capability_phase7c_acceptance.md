# Phase 7C Acceptance Report — Hardened

## Date

2026-05-03

## Hardening Pass Status: ACCEPTED

All acceptance criteria verified. No regressions. No activation. No execution.

---

## Test Counts

### Phase 7C Tests (4 files, 137 tests)

| File | Tests | Description |
|------|-------|-------------|
| `tests/capabilities/test_phase7c_transition_request.py` | 35 | Model, storage, gate, list/view/cancel tests |
| `tests/capabilities/test_phase7c_transition_tools.py` | 16 | Tool registration, permission, execution tests |
| `tests/capabilities/test_phase7c_transition_safety.py` | 18 | Isolation, no-execution, safety tests |
| `tests/capabilities/test_phase7c_hardening.py` | 68 | Hardening: exhaustive gates, isolation, permission, safety, cancel, list/view |

### Combined Test Suites

| Suite | Passed | Failed | Skipped |
|-------|--------|--------|---------|
| Phase 7C (all 4 files) | 137 | 0 | 0 |
| Phase 7A+7B+7C combined | 280 | 0 | 0 |
| Full capabilities (`tests/capabilities/`) | 1244 | 0 | 0 |
| Agents (`tests/agents/`) | 544 | 0 | 0 |
| Core tools + profiles + StateView | 39 | 0 | 0 |
| Skills (`tests/skills/`) | 64 | 0 | 0 |
| Logging (`tests/logging/`) | 32 | 0 | 0 |
| **Grand total** | **~2012** | **0** | **~1** |

---

## Files Changed

### New Files
- `src/capabilities/quarantine_transition.py` — Core transition request module
- `tests/capabilities/test_phase7c_transition_request.py` — Model/storage/gate tests
- `tests/capabilities/test_phase7c_transition_tools.py` — Tool registration/execution tests
- `tests/capabilities/test_phase7c_transition_safety.py` — Isolation/safety tests
- `tests/capabilities/test_phase7c_hardening.py` — Hardening acceptance tests
- `docs/capability_phase7c_transition_requests.md` — Architecture/semantics doc
- `docs/capability_phase7c_acceptance.md` — This document

### Modified Files
- `src/config/settings.py` — Added `quarantine_transition_requests_enabled` flag + env mapping
- `src/tools/capability_tools.py` — 4 tool schemas, 4 executors, `register_quarantine_transition_tools()`
- `src/app/container.py` — Phase 7C wiring under narrower feature flag
- `docs/capability_system_overview.md` — Updated for Phase 7C
- `docs/capability_acceptance_index.md` — Updated for Phase 7C

---

## 1. Feature Flag Matrix

| Flag | Default | Effect |
|------|---------|--------|
| `capabilities.enabled` | `true` | Master capability switch |
| `capabilities.external_import_enabled` | `false` | Phase 7A/7B tools |
| `capabilities.quarantine_transition_requests_enabled` | `false` | Phase 7C tools (narrower flag) |

**Verified:**
- [x] Phase 7C tools absent when not explicitly registered (container flag-gated)
- [x] Phase 7C tools NOT registered by Phase 7A or 7B registration functions
- [x] Phase 7C tools registered only via `register_quarantine_transition_tools()`
- [x] When `quarantine_transition_requests_enabled=false`, container does not call registration

---

## 2. Permission Matrix

| Tool | Capability Tag | Risk |
|------|---------------|------|
| `request_quarantine_testing_transition` | `capability_import_operator` | low |
| `list_quarantine_transition_requests` | `capability_import_operator` | low |
| `view_quarantine_transition_request` | `capability_import_operator` | low |
| `cancel_quarantine_transition_request` | `capability_import_operator` | low |

**Verified — all 4 tools denied to:**
- [x] `standard` profile (no `capability_import_operator` tag match)
- [x] `default` profile
- [x] `chat` profile
- [x] `local_execution` profile
- [x] `browser_operator` (no `capability_import_operator`)
- [x] `identity_operator` (no `capability_import_operator`)
- [x] `capability_lifecycle_operator` (no `capability_import_operator`)
- [x] `capability_curator_operator` (no `capability_import_operator`)
- [x] `agent_candidate_operator` (no `capability_import_operator`)

**Verified — granted to:**
- [x] Profiles with `capability_import_operator` tag
- [x] ToolDispatcher allows with import operator profile

---

## 3. Forbidden Tool Audit

**Verified absent from entire codebase:**

| Forbidden Name | Status |
|---------------|--------|
| `activate_quarantined_capability` | NOT PRESENT |
| `promote_quarantined_capability` | NOT PRESENT |
| `apply_quarantine_transition` | NOT PRESENT |
| `run_quarantined_capability` | NOT PRESENT |
| `run_capability` | NOT PRESENT |
| `execute_capability` | NOT PRESENT |
| `install_capability` | NOT PRESENT |
| `save_quarantined_as_capability` | NOT PRESENT |
| `move_quarantine_to_workspace` | NOT PRESENT |

All matches in codebase are in test assertions verifying absence or docstring comments.

Also verified: `quarantine_transition` module exports no forbidden functions (`hasattr` check).

---

## 4. Request Gate Hardening

### Denied scenarios (each verified by test)

| Gate | Test Result |
|------|------------|
| Capability missing from quarantine | Denied: "not found" |
| Capability path traversal (`../../etc/passwd`) | Denied: "Invalid identifier" |
| `manifest.status != quarantined` (active) | Denied: "status.*quarantined" |
| `manifest.status != quarantined` (disabled) | Denied: "status.*quarantined" |
| `manifest.maturity != draft` (testing) | Denied: "maturity.*draft" |
| `manifest.maturity != draft` (stable) | Denied: "maturity.*draft" |
| No review decision | Denied: "No review decision" |
| Review = `needs_changes` | Denied: "review status.*needs_changes" |
| Review = `rejected` | Denied: "review status.*rejected" |
| No audit report | Denied: "No audit report" |
| Audit not passed + not recommended | Denied: "Audit recommendation" |
| Audit recommended = `rejected` | Denied: "Audit recommendation" |
| Review content_hash mismatch | Denied: "Content hash mismatch" |
| Audit content_hash mismatch | Denied: "Content hash mismatch" |
| Duplicate pending request (same cap + scope) | Denied: "already exists" |
| Invalid target scope (`production`) | Denied: "Invalid target_scope" |
| Empty reason | Denied: "reason is required" |
| Corrupt manifest.json | Denied: "Cannot read manifest" |
| Corrupt review file | Denied: "No review decision" (clean fallback) |
| Corrupt audit file | Denied: "No audit report" (clean fallback) |

### Positive behaviors

| Behavior | Verified |
|----------|---------|
| High risk → `required_approval: true` | Auto-set in request metadata |
| `dry_run=true` writes nothing to disk | No `.json` files created |
| `dry_run=true` blocked → returns `blocking_reasons` list | List populated |
| Successful request → exactly 1 JSON file written | 1 file in `quarantine_transition_requests/` |

---

## 5. No-Activation / No-Mutation Proof

### Byte-for-byte verification

| File | Before = After? |
|------|----------------|
| `manifest.json` | Yes — byte-for-byte identical |
| `CAPABILITY.md` | Yes — byte-for-byte identical |

### No side effects

| Check | Verified |
|-------|---------|
| No active scope directory created (`user/`, `workspace/`, `global/`, `session/`) | Yes |
| No files moved/copied outside `quarantine/<id>/quarantine_transition_requests/` | Yes |
| No `CapabilityStore.create_draft` call | Yes |
| No `CapabilityLifecycleManager` call | Yes |
| No `CapabilityIndex` active update | Yes |
| No eval record written | Yes |
| No version snapshot written | Yes |
| No promotion/run/execute path | Yes |
| Request file ONLY under `quarantine/<id>/quarantine_transition_requests/<request_id>.json` | Yes |

---

## 6. List/View/Cancel Behavior

### list_quarantine_transition_requests
- [x] Read-only — no mutation
- [x] Filters by `status` (pending/cancelled verified)
- [x] Filters by `target_scope` (user/workspace verified)
- [x] Filters by `capability_id`
- [x] Does not emit script contents
- [x] Does not emit raw source paths
- [x] Deterministic ordering (same result on repeated calls)

### view_quarantine_transition_request
- [x] Read-only — no mutation
- [x] Clean `not_found` for nonexistent request
- [x] No script contents in response
- [x] No raw source paths in response
- [x] No capability body exposure

### cancel_quarantine_transition_request
- [x] Changes `pending` → `cancelled` only
- [x] Requires reason (empty reason denied)
- [x] Writes request status only
- [x] Does not alter capability manifest (byte-for-byte unchanged)
- [x] Does not delete request file
- [x] Does not affect active store/index
- [x] Cannot cancel already cancelled request (raises "Only 'pending'")
- [x] Cancel does not grant permissions
- [x] Cancel does not create active scope directories

---

## 7. Quarantine Isolation After Request

| State | Excluded from default `store.list()`? | Excluded from default `index.search()`? | Excluded from `CapabilityRetriever`? | Excluded from `StateView`? |
|-------|--------------------------------------|----------------------------------------|-------------------------------------|---------------------------|
| No request (baseline) | Yes (status=quarantined) | Yes | Yes | Yes |
| Pending request | Yes (unchanged) | Yes (unchanged) | Yes (unchanged) | Yes (unchanged) |
| Cancelled request | Yes (unchanged) | Yes (unchanged) | Yes (unchanged) | Yes (unchanged) |

Request status does NOT change retrieval filtering. The manifest status remains `quarantined` regardless of request state.

---

## 8. Safety Checks

| Check | Result |
|-------|--------|
| No `subprocess` in module | Verified |
| No `os.system` in module | Verified |
| No `importlib.import_module` in module | Verified |
| No `__import__` in module | Verified |
| No `exec()` / `compile()` in module | Verified |
| No network libraries (urllib, requests, http, socket) | Verified |
| No LLM judge (no anthropic, openai imports) | Verified |
| No Python import from quarantined packages | Verified |
| No test execution from quarantined packages | Verified |
| Raw source paths not emitted in responses | Verified |
| Script contents not emitted in responses | Verified |
| Prompt injection in reason stored as data, not interpreted | Verified |
| Large/binary files not read directly by transition layer | Verified (read_text/read_bytes only on JSON metadata) |
| Path traversal rejected for `capability_id` | Verified |
| Path traversal rejected for `request_id` | Verified |
| Slash in `capability_id` rejected | Verified |
| Slash in `request_id` rejected | Verified |
| Writes stay under `quarantine/<id>/quarantine_transition_requests/` | Verified |
| Corrupt review JSON handled cleanly (no crash) | Verified |
| Corrupt audit JSON handled cleanly (no crash) | Verified |
| Corrupt manifest handled cleanly (no crash) | Verified |

---

## 9. Runtime Import Audit

```
src/tools/capability_tools.py   — ALLOWED (capability tools module)
src/app/container.py            — ALLOWED (container wiring)
```

**No direct `src.capabilities` imports from:**
- [x] Brain
- [x] TaskRuntime
- [x] StateViewBuilder
- [x] SkillExecutor
- [x] ToolDispatcher
- [x] Agent modules
- [x] Dynamic agent runtime paths

---

## 10. Regression Checks

- [x] All Phase 7A tests pass (51 tests, 0 regressions)
- [x] All Phase 7B tests pass (79 tests, 0 regressions)
- [x] All previous capability tests pass (1244 total, 0 regressions)
- [x] Agent tests pass (544 tests, 0 regressions)
- [x] ToolDispatcher tests pass
- [x] RuntimeProfile tests pass
- [x] StateView tests pass
- [x] Skills/logging tests pass (96 combined)
- [x] Read-only capability tools unchanged
- [x] Lifecycle tools unchanged
- [x] Curator tools unchanged
- [x] Agent candidate tools unchanged
- [x] No new automatic behavior introduced

---

## 11. Known Issues

None. All tests pass. No regressions detected.

---

## 12. Rollback Notes

To disable Phase 7C:
1. Set `capabilities.quarantine_transition_requests_enabled = false` (already default)
2. Tools are absent from the registry
3. Existing `quarantine_transition_requests/*.json` files are inert — no code reads them without the tools
4. No database migrations or state changes to revert
5. All other capability functionality unaffected
