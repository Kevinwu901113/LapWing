# Phase 3A Acceptance Report

**Date:** 2026-04-30
**Phase:** Capability Evolution System Phase 3A — Policy + Evaluator + Eval Records + Promotion Planner
**Status:** Accepted

## Test Results

| Test Suite | Pass | Fail | Skip |
|---|---|---|---|
| Phase 3A Policy (`test_phase3a_policy.py`) | 37 | 0 | 0 |
| Phase 3A Evaluator (`test_phase3a_evaluator.py`) | 21 | 0 | 0 |
| Phase 3A Eval Records (`test_phase3a_eval_records.py`) | 14 | 0 | 0 |
| Phase 3A Promotion (`test_phase3a_promotion.py`) | 25 | 0 | 0 |
| Phase 3A Hardening (`test_phase3a_hardening.py`) | 37 | 0 | 0 |
| **Phase 3A total** | **134** | **0** | **0** |
| Phase 0/1/2A/2B regression | 315 | 0 | 0 |
| **All capabilities** | **449** | **0** | **0** |

### Legacy / runtime test suites

| Test Suite | Pass | Fail | Notes |
|---|---|---|---|
| Skills and agents (`tests/skills/`, `tests/agents/`) | 272 | 0 | All pass |
| ToolDispatcher (`tests/core/test_tool_dispatcher.py`) | 55 | 0 | All pass |
| Logging / MutationLog (`tests/logging/`) | 32 | 0 | All pass |
| StateView (`tests/core/test_state_view_builder.py`, `test_stateview_agent_summary.py`) | 29 | 0 | All pass |
| RuntimeProfile / tool boundary (`tests/core/test_runtime_profiles_exclusion.py`, `test_tool_boundary.py`) | 44 | 1 | Pre-existing: `test_local_execution_profile_is_frozen` needs `list_agents` added to its expected frozenset |

## Files Changed

### New files (4 source + 5 test)
- `src/capabilities/policy.py` — CapabilityPolicy with PolicyDecision
- `src/capabilities/evaluator.py` — CapabilityEvaluator with EvalRecord / EvalFinding
- `src/capabilities/eval_records.py` — write/read/list/latest eval record persistence
- `src/capabilities/promotion.py` — PromotionPlanner with PromotionPlan
- `tests/capabilities/test_phase3a_policy.py` — 37 tests
- `tests/capabilities/test_phase3a_evaluator.py` — 21 tests
- `tests/capabilities/test_phase3a_eval_records.py` — 14 tests
- `tests/capabilities/test_phase3a_promotion.py` — 25 tests
- `tests/capabilities/test_phase3a_hardening.py` — 37 tests (no-mutation, no-execution, determinism, completeness)

### Modified files
- `src/capabilities/__init__.py` — added Phase 3A exports
- `docs/capability_evolution_architecture.md` — added Phase 3A section
- `docs/capability_phase3a_acceptance.md` — this file

## 1. Runtime Wiring Check

```
$ grep -rn "from src.capabilities\|import src.capabilities" src/ | grep -v 'src/capabilities/'
src/tools/capability_tools.py  — allowed (read-only tools)
src/app/container.py            — allowed (store/index construction)
```

**No unauthorized imports.** Confirmed zero references from:
- Brain
- TaskRuntime
- StateViewBuilder
- SkillExecutor
- SkillStore runtime paths
- ToolDispatcher
- AgentRegistry execution paths
- AgentPolicy execution paths
- RuntimeProfile

In-capability-package grep confirms only docstring mentions of these names (stating they are NOT wired).

## 2. No-Mutation Verification

### Policy
- `validate_create` does not mutate manifest: tested with `model_dump()` before/after
- `validate_promote` does not mutate manifest: tested with `model_dump()` before/after
- `validate_run` does not mutate manifest: tested
- `validate_patch` does not mutate either manifest: tested
- `validate_install` does not mutate manifest: tested
- `validate_risk` does not mutate manifest: tested
- Policy module source contains no `CapabilityStore`, `CapabilityIndex`, `open(`, or `pathlib` references

### Evaluator
- `evaluate` does not mutate manifest: tested with `model_dump()` before/after
- `evaluate` does not write any files: tested by diffing directory contents before/after
- No LLM calls, no script execution, no network access
- Resists binary body, empty body, very large body (100K chars)

### Eval Records
- `write_eval_record` does not mutate `CAPABILITY.md` content: tested byte-for-byte
- `write_eval_record` does not mutate manifest fields (maturity, status, id): tested
- Multiple writes do not cumulatively mutate logical manifest fields: tested
- Write only creates files under `evals/`: tested by directory diff
- `read_eval_record`, `list_eval_records`, `get_latest_eval_record` do not mutate manifest: tested
- Works with `mutation_log=None`; mutation log failure does not break write: tested

### Promotion Planner
- `plan_transition` does not mutate manifest: tested with `model_dump()` before/after
- `plan_transition` does not mutate eval_record: tested (score, content_hash unchanged)
- Planner module has no import of `CapabilityStore`
- Planner does not access the filesystem

## 3. Promotion Planner Behavior

All transitions are **planned only**, never executed:
- `draft -> testing`: planned, not executed (no manifest mutation)
- `testing -> stable`: planned, not executed
- `stable -> broken`: planned, not executed (requires failure_evidence)
- `any -> disabled`: planned, not executed
- `any -> archived`: planned, not executed
- No code path writes updated maturity/status
- No code path calls `CapabilityStore.disable/archive/create_draft`
- No code path calls existing `promote_skill`

## 4. Evaluator Safety Behavior

Deterministic findings verified for all required checks:
- Missing Verification section → ERROR
- Missing Failure handling section → ERROR
- Missing When to use section → ERROR
- Missing Procedure section → ERROR
- Dangerous shell pattern (rm -rf /) → ERROR
- curl pipe bash → ERROR
- chmod 777 → ERROR
- Prompt-injection-like phrase → WARNING
- Absolute path reference (/etc/) → WARNING
- Overbroad trigger (*) → WARNING
- Stable maturity without eval evidence → INFO
- High risk without approval → INFO
- Clean body → no dangerous patterns found

Confirmed:
- No scripts are executed (tested with scripts containing `raise SystemExit`)
- No scripts are imported
- No shell commands are run
- No network access is attempted
- Capability body is treated as untrusted document content

## 5. Policy Behavior

Deterministic decisions verified:
- High risk promotion requires owner approval → denied without it, allowed with it
- Medium risk promotion requires approval or sufficient eval evidence
- Low risk promotion allowed only with valid evaluator pass or no eval
- Quarantined cannot promote or run
- Archived cannot promote or run
- Disabled cannot run
- Required tools checked against provided available_tools; allowed when omitted
- Policy never grants new permissions
- Policy never modifies RuntimeProfile

## 6. Eval Records

Persistence verified:
- `write_eval_record` writes JSON to `evals/`
- `read_eval_record` round-trips all fields
- `list_eval_records` sorts by `created_at` descending
- `get_latest_eval_record` returns latest
- Records include all required fields: capability_id, scope, content_hash, evaluator_version, created_at, passed, score, findings, required_approval, recommended_maturity
- Works with `mutation_log=None`
- Optional mutation log recording does not break if `mutation_log.record` fails

## 7. Read-Only Tool Regression

Phase 2B tool regression verified:
- [x] `list_capabilities` remains read-only
- [x] `search_capability` remains read-only
- [x] `view_capability` remains read-only
- [x] No `run_capability` tool exists
- [x] No `create_capability` tool exists
- [x] No `disable_capability` tool exists
- [x] No `archive_capability` tool exists
- [x] No `promote_capability` tool exists
- [x] Capability tools not registered when `capabilities.enabled=false`
- [x] Only three read-only tools registered when `capabilities.enabled=true`

## 8. Existing Behavior Regression

Confirmed unchanged:
- Old skills list/read/run/promote (272 tests pass)
- Dynamic agents (agent tests pass)
- ToolDispatcher permission checks (55 tests pass)
- RuntimeProfile behavior (44/45 pass; 1 pre-existing failure)
- MutationLog existing enum values and JSONL behavior (32 tests pass)
- StateView has no capability section (29 tests pass)
- Brain and TaskRuntime do not retrieve, evaluate, or promote capabilities

## 9. Determinism

Determinism verified:
- Policy: 10 calls with same input → identical output (allowed, code, severity)
- Evaluator: 2 parses of same directory → identical output (passed, score, findings, required_approval, recommended_maturity)
- Planner: 10 calls with same input → identical output (allowed, explanation)

## Confirmation Checklist

- [x] All 449 capability tests pass (97 Phase 3A + 37 hardening + 315 regression)
- [x] All relevant legacy tests pass (skills, agents, ToolDispatcher, MutationLog, StateView)
- [x] 1 pre-existing RuntimeProfile failure confirmed unrelated (`list_agents` frozenset)
- [x] Only allowed runtime imports: `capability_tools.py` and `container.py`
- [x] Policy/evaluator/planner are deterministic and non-mutating
- [x] Eval records persist without changing manifest maturity/status
- [x] No script execution path exists
- [x] Existing read-only capability tools remain read-only

Hard constraints verified:
- [x] No Brain wiring
- [x] No TaskRuntime wiring
- [x] No StateViewBuilder wiring
- [x] No ToolDispatcher changes
- [x] No SkillExecutor changes
- [x] No promote_skill changes
- [x] No run_capability tool
- [x] No write capability tools
- [x] No script execution
- [x] No automatic retrieval
- [x] No ExperienceCurator
- [x] No dynamic agent changes
- [x] No mutation of capability maturity/status through promotion.py
- [x] Feature flags remain default false

## Known Issues

1. **Pre-existing:** `tests/core/test_runtime_profiles_exclusion.py::TestProfileExclusivity::test_local_execution_profile_is_frozen` fails because `list_agents` was recently added to `LOCAL_EXECUTION_PROFILE.tool_names` but the test's expected frozenset was not updated. Not related to Phase 3A.

## Rollback Notes

To roll back Phase 3A:
1. Remove five new source files: `policy.py`, `evaluator.py`, `eval_records.py`, `promotion.py`
2. Remove five new test files: `test_phase3a_policy.py`, `test_phase3a_evaluator.py`, `test_phase3a_eval_records.py`, `test_phase3a_promotion.py`, `test_phase3a_hardening.py`
3. Revert `src/capabilities/__init__.py` (remove Phase 3A imports and __all__ entries)
4. Revert `docs/capability_evolution_architecture.md` (remove Phase 3A section)
5. Delete `docs/capability_phase3a_acceptance.md`

No other files were modified.
