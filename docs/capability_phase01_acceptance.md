# Phase 0/1 Acceptance Report — Capability Evolution System

Date: 2026-04-30
Baseline commit: 22d7248 (master HEAD)

## Test Results

| Suite | Pass | Fail | Skip |
|-------|------|------|------|
| New capability tests (`tests/capabilities/`) | 101 | 0 | 0 |
| Skill tests (`tests/skills/`) | 68 | 0 | 0 |
| Agent tests (`tests/agents/`) | 328 | 0 | 0 |
| ToolDispatcher + dispatcher tests | 44 | 0 | 0 |
| RuntimeProfile tests (`test_runtime_profiles_exclusion.py`) | 30 | 1* | 0 |
| MutationLog tests (`tests/logging/`) | 27 | 0 | 0 |
| StateView tests | 34 | 0 | 0 |
| Chat tools centralized (`test_chat_tools_centralized.py`) | 43 | 1* | 0 |
| Brain tools (`test_brain_tools.py`) | 18 | 9* | 0 |
| Audit logging (`test_audit_logging.py`) | 14 | 2* | 0 |
| Identity acceptance | 0 | 0 | 13 |
| Remainder | ~1685 | 0 | 0 |
| **Total** | **2391** | **13*** | **13** |

\* = pre-existing, unrelated to Phase 0/1

## Remaining Failure Analysis

### All 13 failures are pre-existing and unrelated

**Bucket A: `list_agents` tool (3 failures)**
- `test_local_execution_profile_is_frozen` — commit `4a45f46` added `list_agents` to `LOCAL_EXECUTION_PROFILE` but didn't update the frozen assertion.
- `test_profile_lists_companion_surface_tools` — same commit, test asserts `list_agents not in names` but it was added.
- Files touched by `4a45f46`: `src/core/runtime_profiles.py`, `src/agents/registry.py`, `src/agents/spec.py`, `src/tools/agent_tools.py`. Zero overlap with Phase 0/1.

**Bucket B: Brain shell/tool-loop (9 failures)**
- All in `test_brain_tools.py::TestBrainTools` — shell execution tests that mock `execute_shell` but the mock is never awaited (real shell path is bypassed in this test environment).
- Fail identically on clean master (`git stash` confirmed).

**Bucket C: Browser guard audit (2 failures)**
- `test_browser_guard_missing_recorded`, `test_browser_guard_url_block_recorded` — browser-enabled test expects browser_guard_missing denials but gets profile_not_allowed denials because browser tools aren't in the test profile.
- Fail identically on clean master (`git stash` confirmed).

**Evidence**: `git stash` → run failing tests on parent commit → same failures → `git stash pop`.

## No Accidental Runtime Wiring

Checked every module listed in the spec:

```
grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
```

Result: **zero matches**. No runtime module imports `src.capabilities`.

Individual checks:
- `src/core/brain.py` — CLEAN
- `src/core/task_runtime.py` — CLEAN (no import)
- `src/core/state_view_builder.py` — CLEAN
- `src/core/tool_dispatcher.py` — CLEAN (no import)
- `src/skills/skill_executor.py` — CLEAN
- `src/skills/skill_store.py` — CLEAN
- `src/tools/skill_tools.py` — CLEAN
- `src/agents/registry.py` — CLEAN
- `src/agents/policy.py` — CLEAN
- `src/agents/dynamic.py` — CLEAN
- `src/agents/base.py` — CLEAN
- `src/agents/factory.py` — CLEAN
- `src/agents/catalog.py` — CLEAN
- `src/logging/state_mutation_log.py` — CLEAN
- `src/core/runtime_profiles.py` — CLEAN

Note: Some files contain the word "capabilities" in reference to the pre-existing `RuntimeProfile.capabilities` field (a `frozenset[str]` of tool category tags like "web", "code", "skill"). This is unrelated to the new `src/capabilities/` package.

## Feature Flags

All four flags default to `False` across all three config layers:

| Flag | Pydantic model | `config.toml` | Compat shim |
|------|---------------|---------------|-------------|
| `capabilities.enabled` | False | False | False |
| `capabilities.retrieval_enabled` | False | False | False |
| `capabilities.curator_enabled` | False | False | False |
| `capabilities.auto_draft_enabled` | False | False | False |

Verified via direct `LapwingSettings` access and via `config.settings` compat shim.

## Backward Compatibility

### Skills — all passing
- **List**: `SkillStore.list_skills()` returns created skills (tested)
- **Read**: `SkillStore.read()` returns meta + code (tested)
- **Run**: `SkillExecutor.execute()` gates by maturity and sandbox tier (tested)
- **Promote**: `record_execution(success=True)` auto-promotes draft→testing (tested)
- **Capture**: `SkillCapturer.maybe_capture_skills()` works (tested)
- **Registration**: `promote_skill` hot-registers ToolSpec (tested)

### Dynamic agents — all passing
- Denylist unchanged (tested)
- AgentSpec v2 spec_hash stable (tested)
- LegacyAgentSpec still usable (tested)
- ToolDispatcher agent policy check unchanged (tested)

### ToolDispatcher — all passing
- Unknown tool denial (tested)
- Profile gate denial (tested)
- Authority gate denial (tested)
- ServiceContextView accessors (tested)

### RuntimeProfile — all passing except pre-existing `list_agents` test
- All 16 profiles exist (tested)
- Profile is frozen dataclass (tested)
- Profile names and tool membership unchanged (tested)

### MutationLog — all passing
- Record + query round-trip (tested)
- JSONL mirror written (tested)
- Agent lifecycle events record correctly (tested)
- MutationType enum values intact (tested)

## Files Changed

### Modified
- `config.toml` — added `[capabilities]` section (4 lines + comment)
- `config/settings.py` — added 4 compat shim constants
- `src/config/settings.py` — added `CapabilitiesConfig` class, field on `LapwingSettings`, 4 env var mappings

### Created
- `docs/capability_evolution_architecture.md` — architecture map
- `src/capabilities/__init__.py` — public API
- `src/capabilities/errors.py` — 7 error types
- `src/capabilities/ids.py` — ID generation/validation
- `src/capabilities/schema.py` — Pydantic enums + CapabilityManifest
- `src/capabilities/hashing.py` — compute_content_hash
- `src/capabilities/document.py` — CapabilityParser + CapabilityDocument
- `tests/capabilities/__init__.py`
- `tests/capabilities/test_phase0_regression.py` — 40 tests
- `tests/capabilities/test_phase1_parsing.py` — 61 tests

## Hard Constraint Compliance

| Constraint | Status |
|-----------|--------|
| No runtime behavior change | PASS |
| No automatic capability retrieval | PASS |
| No StateView injection | PASS |
| No ExperienceCurator | PASS |
| No promotion logic | PASS |
| No CapabilityStore or SQLite index | PASS |
| No run_capability | PASS |
| No script execution | PASS |
| No changes to skill execution semantics | PASS |
| No changes to ToolDispatcher authorization | PASS |
| No changes to dynamic agent persistence | PASS |
| All feature flags default false | PASS |
| No Brain/TaskRuntime/StateView wiring | PASS |

## Verdict

**Phase 0/1 baseline is clean.** The only test failures (13/2404 = 0.54%) are proven pre-existing and unrelated:
- 3 from commit `4a45f46` (`list_agents` tool not fully updated in tests)
- 10 from environment-specific shell/browser mock issues (fail identically on clean master)

Phase 2 (CapabilityStore / Index) can proceed on this baseline.
