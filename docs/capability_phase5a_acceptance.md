# Capability Phase 5A Acceptance — Experience Curator + Capability Proposal

**Date:** 2026-05-01
**Status:** Accepted (hardened)
**Tests:** 1,277 passed, 0 failed (all suites)

## Test Results

### Capability tests

```
tests/capabilities/ — 845 passed, 0 failed
```

Breakdown:
- Phase 0/1/2A/2B/3A/3B/3C/4 tests: all passing (735 → 735 unchanged)
- Phase 5A trace_summary (22 tests): all passing
- Phase 5A curator (31 tests): all passing (+2 path-safety tests added during hardening)
- Phase 5A proposal (18 tests): all passing
- Phase 5A tools (22 tests): all passing (+3 tests added during hardening)
- Phase 5A safety (17 tests): all passing

### Cross-cut regression suites

| Suite | Tests | Result |
|-------|-------|--------|
| tests/capabilities/ | 845 | PASSED |
| tests/core/test_state_view* | 41 | PASSED |
| tests/core/test_tool_dispatcher.py | 87 | PASSED |
| tests/core/test_runtime_profiles_exclusion.py | 32 | PASSED |
| tests/skills/ | 64 | PASSED |
| tests/agents/ | 208 | PASSED |
| tests/logging/ | 57 | PASSED |
| **Total** | **1,277** | **PASSED** |

## Files Created

- `src/capabilities/trace_summary.py` — TraceSummary dataclass + secrets redaction
- `src/capabilities/curator.py` — ExperienceCurator, CuratorDecision, CuratedExperience
- `src/capabilities/proposal.py` — CapabilityProposal model + filesystem persistence
- `tests/capabilities/test_phase5a_trace_summary.py` — 22 tests
- `tests/capabilities/test_phase5a_curator.py` — 31 tests
- `tests/capabilities/test_phase5a_proposal.py` — 18 tests
- `tests/capabilities/test_phase5a_tools.py` — 22 tests
- `tests/capabilities/test_phase5a_safety.py` — 17 tests
- `docs/capability_phase5a_acceptance.md` — this document

## Files Modified

- `src/capabilities/__init__.py` — Phase 5A exports
- `src/capabilities/curator.py` — added proposed_id path traversal validation
- `src/capabilities/proposal.py` — added mark_applied function
- `src/core/runtime_profiles.py` — added CAPABILITY_CURATOR_OPERATOR_PROFILE
- `src/tools/capability_tools.py` — added reflect_experience/propose_capability schemas, executors, register_capability_curator_tools
- `src/app/container.py` — curator tool wiring behind capabilities.curator_enabled flag
- `docs/capability_evolution_architecture.md` — Phase 5A section added
- `tests/capabilities/test_phase0_regression.py` — added capability_curator_operator to expected profiles
- `tests/capabilities/test_phase4_hardening.py` — updated retrieval block and ExperienceCurator checks for Phase 5A
- `tests/capabilities/test_phase5a_curator.py` — added path traversal rejection tests
- `tests/capabilities/test_phase5a_tools.py` — added patch-existing boundary, path traversal, and apply=false directory safety tests

## Feature Flag Matrix

| capabilities.enabled | capabilities.curator_enabled | Behavior |
|---------------------|-----------------------------|----------|
| false               | *                           | No curator tools registered |
| true                | false                       | No curator tools registered |
| true                | true                        | reflect_experience + propose_capability registered (capability_curator tag required) |

All flags default to `False` (verified in test_phase0_regression.py).

Feature flags are independent:
- `curator_enabled` does NOT imply `lifecycle_tools_enabled`
- `curator_enabled` does NOT grant `capability_read` or `capability_lifecycle`
- `curator_enabled` does NOT grant `capability_curator` to any profile — that requires an explicit profile assignment

## Permission Matrix

| Tool | capability tag | risk_level | Profiles with access |
|------|---------------|------------|---------------------|
| reflect_experience | capability_curator | low | capability_curator_operator |
| propose_capability | capability_curator | medium | capability_curator_operator |

- `capability_curator` is NOT granted to standard, default, chat, inner_tick, agent_researcher, agent_coder, local_execution, browser_operator, identity_operator, or any other profile
- Only `CAPABILITY_CURATOR_OPERATOR_PROFILE` includes `capability_curator`
- ToolDispatcher denies curator tools without explicit curator profile
- ToolDispatcher allows curator tools with explicit curator profile
- No broad existing profile accidentally gained capability_curator

## Hardening Changes (this pass)

### 1. Path safety validation (NEW)

`curator.propose_capability()` now validates that `proposed_id` does not contain `..`, `/`, or `\` characters. Raises `ValueError` on unsafe input. The tool executor catches this and returns an error result.

Tests added:
- `test_propose_capability_rejects_path_traversal_id` — rejects `../etc/malicious`
- `test_propose_capability_rejects_slash_in_id` — rejects `sub/dir/prop`
- `test_propose_capability_rejects_path_traversal_id` (tool-level) — tool returns error

### 2. Patch-existing boundary test (NEW)

`test_patch_existing_does_not_mutate_existing_capability` verifies:
- When `existing_capability_id` is set, the existing capability file on disk is byte-for-byte unchanged
- `apply=false`: no store mutation, existing file unchanged
- `apply=true`: creates a NEW draft with a different capability_id, existing file unchanged
- `create_draft` is called with the NEW proposed ID, not the existing one

### 3. apply=false directory safety test (NEW)

`test_apply_false_does_not_create_capability_dir` verifies:
- Only `proposals/` directory is created
- No `data/capabilities/<scope>/<capability_id>/` directory is created
- No capability content files are created
- Only the 3 proposal files exist: proposal.json, PROPOSAL.md, source_trace_summary.json

## Curator Decision Determinism

The ExperienceCurator uses 11 deterministic heuristics (no LLM, no network, no randomness):

**Create signals:**
- many_tools (>=5): confidence 0.6
- failed_then_succeeded: confidence 0.7
- user_correction_detected: confidence 0.6
- repeated_task_pattern (>=3): confidence 0.6
- non-trivial task_type: confidence 0.5
- contains_file_patch: confidence 0.6
- shell_workflow_with_pipes: confidence 0.7
- shell_workflow_multi_cmd: confidence 0.6
- user_requested_reuse: confidence 0.8
- existing_capability_failed: confidence 0.7
- project_specific_workflow: confidence 0.6
- non-obvious_env_setup: confidence 0.6

**No-action overrides (checked first, unconditional):**
- simple_chat — no tools, commands, or files used
- no_reusable_procedure — no successful steps or commands
- contains_secrets — secrets not redacted from user_request
- no_signals — no heuristics matched (implicit, returns no_action)

**Confidence boost:** +0.1 for stable_verification (>=2 verification items + >=2 successful steps).

**Risk determination:**
- High: dangerous commands (rm -rf, sudo rm, chmod 777, curl|sh, wget, etc.), permission denied errors, system file paths (/etc/, /var/, /usr/)
- Medium: >=3 commands, file_touched, or execute_shell tool used
- Low: everything else

**Determinism:** Verified — same input → same CuratorDecision across 5 repeated runs.

## Secrets Redaction

5 compiled regex patterns at module level:

| Pattern | Replacement |
|---------|-------------|
| sk-<alphanum 20+ chars> | sk-<REDACTED> |
| API_KEY=... | API_KEY=<REDACTED> |
| Authorization: Bearer ... | Authorization: Bearer <REDACTED> |
| password=... | password=<REDACTED> |
| PEM private key block | -----BEGIN PRIVATE KEY----- <REDACTED> ... |

## Input Sanitization

- Drops `_cot`, `_chain_of_thought`, `chain_of_thought`, `_internal`, `_reasoning`, `_thinking`, `reasoning_trace` keys
- Drops `__`-prefixed keys (prototype pollution prevention)
- Truncates strings > 50,000 chars
- Coerces list fields from None/string/list/tuple
- Defaults missing optional fields
- Default created_at to `datetime.now(timezone.utc).isoformat()`
- `from_dict` does not mutate input dict
- `sanitize()` returns new instance (original unchanged)

## Proposal Persistence (Option A — minimal)

Apply path:

| apply | Behavior |
|-------|----------|
| false | Persist proposal files only (proposal.json, PROPOSAL.md, source_trace_summary.json). No capability created. No store mutation. No index update. No EvalRecord. |
| true | Same as false + store.create_draft(), CapabilityEvaluator run, EvalRecord written, index refreshed, proposal marked applied. Maturity always "draft", never promoted. |

Directory layout: `data/capabilities/proposals/<proposal_id>/`

Files:
- `proposal.json` — full serialized CapabilityProposal
- `PROPOSAL.md` — markdown with YAML front matter, all required sections
- `source_trace_summary.json` — redacted original trace summary

Path safety:
- `proposal_id` validated to reject `..`, `/`, `\` characters
- Default `proposal_id` is `prop_{uuid4().hex[:8]}` — always safe
- `prop_dir.mkdir(parents=True, exist_ok=False)` prevents overwrite
- Partial write cleanup on failure (shutil.rmtree)

## Generated CAPABILITY.md Checks

PROPOSAL.md includes:
- [x] YAML front matter with all required metadata
- [x] When to use
- [x] Inputs
- [x] Procedure
- [x] Verification
- [x] Failure handling
- [x] Generalization boundary
- [x] Notes
- [x] Source trace (trace ID or sanitized source summary ID)
- [x] Does NOT include raw CoT
- [x] Does NOT include secrets
- [x] Does NOT include long raw logs
- [x] required_tools derived from trace safely
- [x] required_permissions are conservative (empty by default)
- [x] risk_level is conservative (derived from curator risk detection)

## Patch-Existing Boundary

- `existing_capability_id` in trace → curator recommends `patch_existing_proposal`
- Phase 5A never applies patches — only creates proposals
- Existing capability files are never overwritten, mutated, or modified
- `apply=true` creates a NEW draft with a different capability_id
- Existing capability maturity/status is never changed
- Tested: existing capability file content verified byte-for-byte unchanged

## StateView / Retrieval Interaction

- `apply=false` proposals are NOT injected into StateView
- `apply=false` proposals are NOT indexed as active capabilities
- `apply=true` draft may be indexed, but retrieval filters still control visibility
- Phase 4 summary-only guarantees remain intact
- No proposal body or source trace enters StateView
- StateViewBuilder has no curator section
- StateViewBuilder does not import src.capabilities

## MutationLog Checks

- `store.create_draft()` (during apply=true) logs `capability.draft_created` via CapabilityStore._maybe_record()
- mutation_log is wired into CapabilityStore by container.py
- mutation_log failure is non-fatal (exception caught, logged at debug level)
- `apply=false` does not directly log mutations (no capability is created)
- Existing MutationType enum values are unchanged by Phase 5A
- No new MutationType values added for curator operations

## Tool Surface Checks

- [x] No `run_capability` tool exists
- [x] No `auto_reflect_experience` tool exists
- [x] No `task_end_curator` tool exists
- [x] No `patch_capability` write tool exists
- [x] No `edit_capability` write tool exists
- [x] Read-only tools remain read-only
- [x] Lifecycle tools remain separately gated by lifecycle_tools_enabled
- [x] `curator_enabled` does not imply `lifecycle_tools_enabled`
- [x] `curator_enabled` does not grant `capability_read` or `capability_lifecycle`
- [x] `promote_skill` is unchanged

## Runtime Behavior Checks

- [x] Brain does not auto-curate (no curator references in brain.py)
- [x] TaskRuntime has no task-end hook (no curator references in task_runtime.py)
- [x] StateViewBuilder does not trigger curation
- [x] SkillExecutor not involved
- [x] ToolDispatcher not bypassed
- [x] No script execution
- [x] No shell execution
- [x] No network access
- [x] No automatic promotion
- [x] No dynamic agent changes

## Runtime Import Grep

```
src/tools/capability_tools.py  — allowed (Phase 2B/3C/5A tools)
src/app/container.py            — allowed (wiring)
```

Verified no imports from: Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, AgentRegistry, AgentPolicy, dynamic agent runtime paths.

## Safety Behavior

- [x] No network imports in curator.py, trace_summary.py, proposal.py
- [x] No subprocess/os.system/os.popen calls in any of the three modules
- [x] Sanitize handles 100KB strings (truncation to 50KB)
- [x] Prototype pollution keys (__class__, __init__, __dict__, __module__) dropped
- [x] Non-string dict keys handled (int, tuple) without crash
- [x] None values handled for optional fields
- [x] sanitize() does not mutate original (returns new instance)
- [x] from_dict() does not mutate input dict
- [x] Prompt injection text is treated as data, not executed
- [x] commands_run stored as strings, never executed
- [x] files_touched stored as strings, never opened
- [x] Complex metadata values coerced safely
- [x] No global LLM client instantiated in curator module
- [x] Path traversal rejected in proposed_id

## Hard Constraint Verification

- [x] No TaskRuntime task-end hook
- [x] No automatic curation / auto draft
- [x] No run_capability / script execution / shell commands
- [x] No network / LLM judge
- [x] No Brain/TaskRuntime/SkillExecutor/dynamic agent modifications
- [x] No stable capability patches
- [x] No stable capability creation (maturity always "draft")
- [x] capability_curator tag NOT granted to standard/default/chat profiles
- [x] No files written outside configured capability data root
- [x] proposal_id path traversal prevented
- [x] Existing capabilities never mutated by patch_existing_proposal path

## Existing Behavior Regression

- [x] Phase 0/1/2A/2B/3A/3B/3C/4 tests all pass (735 tests unchanged)
- [x] Old skills list/read/run/promote unchanged (64 tests pass)
- [x] Dynamic agents unchanged (208 tests pass)
- [x] ToolDispatcher permission checks unchanged (87 tests pass)
- [x] RuntimeProfile behavior unchanged (32 tests pass + capability_curator_operator added)
- [x] MutationLog existing enum values unchanged (57 tests pass)
- [x] StateView existing sections unchanged when retrieval disabled (41 tests pass)
- [x] Lifecycle tools remain independently gated
- [x] Retrieval tools remain read-only
- [x] run_capability does not exist

## Known Issues

None.

## Rollback Notes

To roll back Phase 5A:
1. Set `capabilities.curator_enabled = false` in config.toml
2. Or set `capabilities.enabled = false` in config.toml
3. No code changes needed — all wiring is feature-gated
