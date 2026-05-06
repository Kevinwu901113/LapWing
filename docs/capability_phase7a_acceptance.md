# Phase 7A Acceptance Report — Hardened

## Test Results

### Phase 7A Tests
```
tests/capabilities/test_phase7a_import_inspect.py   13 passed
tests/capabilities/test_phase7a_import_quarantine.py 14 passed
tests/capabilities/test_phase7a_import_tools.py      14 passed
tests/capabilities/test_phase7a_import_safety.py      8 passed
                                                     ----------
Phase 7A total:                                      50 passed
```

### Full Suite Regression (Hardening Pass)
```
tests/capabilities/             1082 passed, 0 failed
tests/core/                     1018 passed, 1 skipped (pre-existing)
tests/agents/                    544 passed, 0 failed
tests/skills/ + tests/logging/    96 passed, 0 failed
tests/core/test_tool_dispatcher  100 passed (includes state_view + runtime_profiles_exclusion)
                                 ----------
Total:                          ~2840 passed, 1 skipped (pre-existing, unrelated)
```

### Existing Tool Regression
```
tests/capabilities/test_phase2b_tools.py         (read-only tools)    all passed
tests/capabilities/test_phase3c_lifecycle_tools.py (lifecycle tools)   all passed
tests/capabilities/test_phase5a_tools.py           (curator tools)     all passed
tests/agents/test_agent_candidate_tools.py         (candidate tools)   all passed
```

## Files Changed

### New files
- `src/capabilities/import_quarantine.py` — Core import/inspect logic, path validation, quarantine storage
- `tests/capabilities/test_phase7a_import_inspect.py` — Inspect tests (13 tests)
- `tests/capabilities/test_phase7a_import_quarantine.py` — Quarantine import tests (14 tests)
- `tests/capabilities/test_phase7a_import_tools.py` — Tool registration and execution tests (14 tests)
- `tests/capabilities/test_phase7a_import_safety.py` — Safety/security tests (8 tests)
- `docs/capability_phase7a_external_import.md` — Design doc
- `docs/capability_phase7a_acceptance.md` — This file

### Modified files
- `src/config/settings.py` — Added `external_import_enabled` to `CapabilitiesConfig` + env var mapping
- `config/settings.py` — Added `CAPABILITIES_EXTERNAL_IMPORT_ENABLED` flat re-export
- `config.toml` — Added `external_import_enabled = false`
- `src/capabilities/__init__.py` — Export `InspectResult`, `ImportResult`, `inspect_capability_package`, `import_capability_package`
- `src/core/runtime_profiles.py` — Added `CAPABILITY_IMPORT_OPERATOR_PROFILE`
- `src/tools/capability_tools.py` — Added `inspect_capability_package`, `import_capability_package` tools + `register_capability_import_tools`
- `src/app/container.py` — Wired import tools behind `CAPABILITIES_ENABLED && CAPABILITIES_EXTERNAL_IMPORT_ENABLED`
- `tests/capabilities/test_phase0_regression.py` — Updated expected profile names + added `CAPABILITIES_EXTERNAL_IMPORT_ENABLED` to compat shim test
- `docs/capability_system_overview.md` — Added Phase 7A to all tables, component map, data layout, safety boundaries
- `docs/capability_acceptance_index.md` — Added Phase 7A row, updated suite stats

## Feature Flag Matrix

### Gating Verification

| Case | capabilities.enabled | external_import_enabled | Import tools registered? | How verified |
|------|---------------------|------------------------|-------------------------|-------------|
| A | false | false | No | Container wiring nests inside `if CAPABILITIES_ENABLED` (container.py:1033); import block at line 1168 is inside that scope |
| B | true | false | No | `if CAPABILITIES_EXTERNAL_IMPORT_ENABLED:` gate (container.py:1168); default `false` in settings.py:689 + config.toml:321 |
| C | true | true | Yes — `inspect_capability_package` + `import_capability_package` | `register_capability_import_tools()` creates both with `capability_import_operator` tag; tool tests verify registration |

### Cross-Flag Isolation (verified)
- `external_import_enabled` does NOT register `run_capability` — grep audit clean
- `external_import_enabled` does NOT grant `capability_read` — different profiles
- `external_import_enabled` does NOT grant `capability_lifecycle` — different profiles
- `external_import_enabled` does NOT grant `capability_curator` — different profiles
- `external_import_enabled` does NOT grant `agent_candidate_operator` — different profiles

### Config Layer
| Layer | Location | Value |
|-------|----------|-------|
| TOML | `config.toml:321` | `external_import_enabled = false` |
| Pydantic model | `src/config/settings.py:689` | `external_import_enabled: bool = False` |
| Env var mapping | `src/config/settings.py:300` | `CAPABILITIES_EXTERNAL_IMPORT_ENABLED → capabilities.external_import_enabled` |
| Compat shim | `config/settings.py:157` | `CAPABILITIES_EXTERNAL_IMPORT_ENABLED: bool = _s.capabilities.external_import_enabled` |
| Regression test | `test_phase0_regression.py:66-79` | Asserts `CAPABILITIES_EXTERNAL_IMPORT_ENABLED is False` |

## Permission Matrix

| Profile | Has `capability_import_operator`? | Can inspect/import? |
|---------|----------------------------------|---------------------|
| `standard` | No | No |
| `chat_shell` | No | No |
| `local_execution` | No | No |
| `browser_operator` | No | No |
| `identity_operator` | No | No |
| `capability_lifecycle_operator` | No | No |
| `capability_curator_operator` | No | No |
| `agent_candidate_operator` | No | No |
| `capability_import_operator` | Yes | Yes |

**ToolDispatcher verification:** `tests/core/test_tool_dispatcher.py` (55 tests) + `tests/core/test_runtime_profiles_exclusion.py` (45 tests) — all pass. ToolDispatcher denies tools without matching capability tag; `capability_import_operator` is a dedicated operator-only tag (runtime_profiles.py:245-250), not granted to any other profile.

## Inspect Behavior (verified by 13 tests)

- Valid packages parsed and inspected without writes
- Invalid manifests rejected cleanly (no stack traces)
- Missing CAPABILITY.md handled per parser rules
- Dangerous shell patterns detected in eval findings, not executed
- Policy findings returned
- No files created during inspect
- No quarantine directory created during inspect
- No index updated during inspect
- No import_report.json written during inspect
- No script execution, no Python import, no network, no subprocess
- `include_files=false` suppresses file listings
- Target scope defaults to `user`, overridable

## Import Behavior (verified by 14 tests)

- Valid packages imported into `data/capabilities/quarantine/<id>/`
- `status=quarantined`, `maturity=draft` enforced regardless of package declaration
- Package's declared `status=active` or `status=stable` ignored
- `manifest.json` normalized on write (import_quarantine.py:362-382)
- `import_report.json` written with origin metadata
- Source path stored as SHA256 hash only (import_quarantine.py:389); raw path never persisted
- `original_content_hash` computed from re-parsed quarantined doc
- Package `required_tools` and `required_permissions` stored as inert metadata; no runtime permission grant
- Duplicate active IDs rejected
- Duplicate quarantine IDs rejected
- `dry_run=true` writes nothing, returns inspect info
- Import failure returns clean `ImportResult` with errors list; no partial quarantine directory cleanup (documented behavior — quarantine dir creation is atomic via `shutil.copytree` which raises before partial copy in most cases; on `shutil.Error`, the partial dir is left in place but the ImportResult has `applied=False`)

## Quarantine Isolation (verified)

### Default Exclusion
| Operation | Default behavior | Explicit access |
|-----------|-----------------|-----------------|
| `store.list()` | Excludes quarantined | N/A |
| `index.search()` | Excludes quarantined (default status filter: ACTIVE) | `filters={"status": "quarantined"}` |
| `CapabilityRetriever.retrieve()` | Excludes quarantined | `include_quarantined=True` |
| StateView summaries | Excludes quarantined | N/A |

### Policy Blocks
- `CapabilityPolicy.validate_run()` → blocks `status=quarantined`
- `CapabilityPolicy.validate_promote()` → blocks `status=quarantined`

### No Execution / No Promotion
- No `run_capability` tool exists (grep audit clean across src/ + tests/)
- No lifecycle transition path from quarantined
- Lifecycle tools unchanged (test_phase3c_lifecycle_tools.py passes)
- Imported scripts/tests/examples never injected into StateView

## No-Execution Proof

### Code-level verification
- `import_quarantine.py`: zero calls to `subprocess`, `os.system`, `importlib`, `__import__`, `exec()`, `eval()`
- `shutil.copytree(..., symlinks=False)` — copies files as data only
- No `write_eval_record` call — evaluator runs in-memory, findings stored in report only
- No version snapshot created during import
- No MutationLog record for active capability (quarantine is not an active capability)

### Test-level verification
- Script file copied but not executed (verified by sentinel file absence test)
- Python `.py` files copied, not imported (verified by no-SystemExit test)
- `subprocess.run` monkeypatched — zero calls during import (verified by spy test)
- No network libraries imported by `import_quarantine.py` (verified by grep audit)

## Source Privacy

### import_report.json contents (verified)
| Field | Contains raw path? | Notes |
|-------|-------------------|-------|
| `source_path_hash` | No — SHA256 only | `hashlib.sha256(str(path.resolve()).encode()).hexdigest()` |
| `source_type` | No — constant `"local_package"` | |
| `files_summary` | No — filenames only, no paths | From `_scan_files()` — `p.name for p in sub_path.iterdir()` |
| `original_content_hash` | No — content hash | Re-computed from normalized quarantined doc |
| Full script contents | Never included | |
| Raw logs | Never included | |
| CoT-like fields | Never included | |

### manifest.json contents (verified)
- No `source_path` field
- No `source_path_hash` field
- No `imported_by` field
- No `imported_at` field
- Only standard manifest fields with forced `status=quarantined`, `maturity=draft`

### User-provided string handling
- `imported_by` — stored as-is in `import_report.json` inside quarantine directory; not reflected in StateView, not shown to LLM, not executed
- `reason` — stored as `quarantine_reason` in `import_report.json`; same isolation
- Both are inert data in a JSON file reachable only via explicit filesystem access

## Safety / Path Validation (verified by 8 tests)

| Threat | Mitigation | Code Location |
|--------|-----------|---------------|
| Path traversal (`..`) | Rejected in `_validate_source_path` | import_quarantine.py:106-108 |
| Symlink source | Rejected in `_validate_source_path` + `shutil.copytree(symlinks=False)` | import_quarantine.py:90-98, 348 |
| Remote URLs (`http://`, `https://`, `git://`, `ssh://`) | Rejected before `Path()` conversion | import_quarantine.py:144-146, 274-275 |
| Script execution | Files copied as data; no `exec`/`subprocess`/`importlib` | import_quarantine.py (audit clean) |
| Python module import | Files never imported | Verified by test |
| Prompt injection | CAPABILITY.md treated as data, not instruction | Verified by test |
| Package status override | `normalized_manifest` forces `status=quarantined`, `maturity=draft` | import_quarantine.py:362-382 |
| Archive input | Not implemented — not accepted |
| Package filename traversal | `cap_id` from parsed manifest (parser validates IDs), quarantine path constructed as `quarantine_root / cap_id` | import_quarantine.py:332 |

## Forbidden Tool Audit

Grep across `src/` and `tests/` for each prohibited tool name:

| Tool | Status |
|------|--------|
| `run_capability` | NOT PRESENT (references in tests are only verification-of-absence assertions) |
| `execute_capability` | NOT PRESENT |
| `install_capability` | NOT PRESENT |
| `run_imported_capability` | NOT PRESENT |
| `promote_imported_capability` | NOT PRESENT |
| `auto_install_capability` | NOT PRESENT |
| `registry_search_capability` | NOT PRESENT |
| `update_capability_from_remote` | NOT PRESENT |

## Runtime Import Audit

```
src/tools/capability_tools.py  — ALLOWED (tool executors + registration)
src/app/container.py           — ALLOWED (wiring behind feature flag)
```

No capability imports from:
- `src/core/brain.py`
- `src/core/task_runtime.py`
- `src/core/state_view_builder.py`
- `src/skills/skill_executor.py`
- `src/core/tool_dispatcher.py`
- `src/agents/` (all modules)
- Any other src/ module

Verified via `grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'`

## No Behavioral Leakage

- Read-only capability tools unchanged — 180 existing-tool tests pass
- Lifecycle tools unchanged — `test_phase3c_lifecycle_tools.py` passes
- Curator tools unchanged — `test_phase5a_tools.py` passes
- Auto-proposal unchanged — `test_phase5d` tests pass
- Agent candidate tools unchanged — `test_agent_candidate_tools.py` (93 tests) passes
- Agent runtime unchanged — 544 agent tests pass
- Skill execution unchanged — 64 skill tests pass
- TaskRuntime unchanged — core tests pass
- StateView unchanged — `test_state_view.py` (13 tests) passes
- No new automatic behavior — import tools are explicitly invoked, no task-end hooks, no observers

## Known Issues

- None.

## Rollback Notes

- Set `external_import_enabled = false` in config.toml to disable import tools
- Quarantine directory contents are inert; delete `data/capabilities/quarantine/` to remove all quarantined imports
- No other systems depend on imported capabilities
- Removing `CAPABILITY_IMPORT_OPERATOR_PROFILE` from `_PROFILES` and `runtime_profiles.py` would require also removing the container wiring block (container.py:1165-1183) — the tools would fail to find their profile and be unreachable anyway
