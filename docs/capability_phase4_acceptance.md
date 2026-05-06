# Capability Phase 4 Acceptance — CapabilityRetriever + Progressive Disclosure

**Date:** 2026-05-01
**Status:** Accepted (Hardened)

## Test Results

```
tests/capabilities/ — 735 passed, 0 failed
```

Breakdown:
- Phase 0/1 regression: all passing
- Phase 2A/B store/index/search/tools: all passing
- Phase 3A policy/evaluator/records/promotion: all passing
- Phase 3B lifecycle/hardening/regression: all passing
- Phase 3C lifecycle tools: all passing
- Phase 4 retriever (63 tests): all passing
- Phase 4 state_view (14 tests): all passing
- Phase 4 hardening (44 tests): all passing

### Cross-cut regression suites

| Suite | Tests | Result |
|-------|-------|--------|
| tests/capabilities/ | 735 | PASSED |
| tests/core/test_state_view* | 84 | PASSED |
| tests/core/test_tool_dispatcher.py | 87 (combined) | PASSED |
| tests/core/test_runtime_profiles_exclusion.py | 32 | PASSED |
| tests/skills/ | 64 | PASSED |
| tests/agents/ | 208 | PASSED |
| tests/logging/ (MutationLog) | 57 | PASSED |

## Files Created

- `src/capabilities/ranking.py` — deterministic scoring functions
- `src/capabilities/retriever.py` — CapabilityRetriever, CapabilitySummary, RetrievalContext
- `tests/capabilities/test_phase4_retriever.py` — 63 tests
- `tests/capabilities/test_phase4_state_view.py` — 14 tests
- `tests/capabilities/test_phase4_hardening.py` — 44 tests
- `docs/capability_phase4_acceptance.md` — this document

## Files Modified

- `src/capabilities/__init__.py` — Phase 4 exports (CapabilityRetriever, CapabilitySummary, RetrievalContext)
- `src/core/state_view.py` — CapabilitySummary dataclass + capability_summaries field on StateView
- `src/core/state_view_builder.py` — duck-typed CapabilityRetriever integration, _build_capability_summaries method
- `src/app/container.py` — CapabilityRetriever wiring behind capabilities.retrieval_enabled flag
- `docs/capability_evolution_architecture.md` — Phase 4 section added

## Feature Flag Matrix

| capabilities.enabled | capabilities.retrieval_enabled | Behavior |
|---------------------|-------------------------------|----------|
| false               | *                             | No capability section in StateView. Existing behavior unchanged. |
| true                | false                         | Capability tools may exist via other flags. No automatic retrieval. No StateView section. |
| true                | true                          | CapabilityRetriever wired. StateView may include compact capability summaries. No execution. |

Programmatically verified by `TestFeatureFlagMatrix` (hardening suite).

## Retrieval Filtering Behavior

Verified by tests (retriever + hardening suites):

- Excludes archived by default (`include_archived=False`)
- Excludes disabled by default (`include_disabled=False`)
- Excludes quarantined by default (`include_quarantined=False`)
- Excludes broken by default (maturity=broken always excluded)
- Excludes high risk by default (`include_high_risk=False`)
- Excludes draft by default (`include_draft=False`)
- Excludes capabilities with missing required_tools when available_tools is provided
- Required_tools filtering is deterministic (set subset check)
- Deduplicates by id with scope precedence: session > workspace > user > global
- Enforces max_results (default 5)
- Deterministic ordering (same input → same output across repeated runs)

## Ranking Behavior

Verified by tests:

| Signal | Weight |
|--------|--------|
| Keyword in name | +10 (exact) / +5 (partial) |
| Keyword in triggers | +5 |
| Keyword in tags | +4 |
| Keyword in description | +3 |
| Scope: session | +4 |
| Scope: workspace | +3 |
| Scope: user | +2 |
| Scope: global | +1 |
| Maturity: stable | +5 |
| Maturity: testing | +3 |
| Maturity: draft | 0 |
| Maturity: broken | -10 |
| Risk: low | 0 |
| Risk: medium | -2 |
| Risk: high | -10 |
| Success/usage ratio | up to +3 |
| Recent update | +0.5 |

- No embeddings (verified: ranking.py contains no embedding/numpy/torch imports)
- No LLM judge (verified: no anthropic/openai/llm imports)
- No network access (verified: no requests/httpx/urllib/http imports)
- Pure deterministic scoring (same inputs → same score)

## StateView Content Safety

Verified by hardening tests:

**Included in summaries:**
- id, name, description, type, scope, maturity, risk_level
- triggers (use_when), required_tools
- match_reason

**Never included (verified by sentinel tests):**
- Full CAPABILITY.md body
- Procedure section text
- Script contents
- Trace contents
- Eval contents
- Example contents
- Version snapshot contents
- Raw file paths
- Malicious prompt-injection text from CAPABILITY.md body

**Dataclass safety (verified):**
- StateView CapabilitySummary dataclass has no body/procedure/scripts/traces/evals/examples/versions/instructions/commands/system_message/developer_message fields
- Summaries are frozen dataclasses (immutable)
- Summaries are tuples in StateView (not callables)

## Permission Isolation

Verified by hardening tests:

- [x] retrieval_enabled does not register tools (no tool_registry references in retrieval block)
- [x] CapabilityRetriever has no register_tools or get_tools methods
- [x] CapabilityRetriever is not a tool (no tool_spec, no json_schema)
- [x] CapabilityRetriever has no enabled/retrieval_enabled flag — gating is external
- [x] retrieval_enabled does not grant capability_read
- [x] retrieval_enabled does not grant capability_lifecycle
- [x] Lifecycle tools remain independently gated behind lifecycle_tools_enabled
- [x] Lifecycle tool registration does not appear in retrieval wiring block
- [x] Retriever never calls store mutation methods (create_draft/disable/archive)

## Failure-Closed Behavior

Verified by hardening tests:

- [x] Missing/corrupt capability index → empty results (no crash)
- [x] Corrupt candidate row (missing fields) → skipped safely
- [x] Retriever exception → empty list (never raises to caller)
- [x] StateViewBuilder without retriever → empty capability_summaries
- [x] Empty trajectory query → no retrieval attempted
- [x] No stack traces in StateView
- [x] No user-facing crash from retrieval failures

## Execution Safety

Verified by hardening tests:

- [x] CapabilityRetriever has no run/execute/run_capability methods
- [x] CapabilityRetriever has no _scripts_dir or execute_script
- [x] StateViewBuilder has no _execute_capability or _run_capability
- [x] Brain does not import src.capabilities
- [x] TaskRuntime does not import src.capabilities
- [x] StateViewBuilder uses duck typing (no src.capabilities import)

## Runtime Import Grep

```
src/tools/capability_tools.py  — allowed (Phase 2B/3C tools, inside TYPE_CHECKING)
src/app/container.py            — allowed (wiring), includes src.capabilities.retriever
```

Verified no imports from: Brain, TaskRuntime, SkillExecutor, ToolDispatcher, AgentRegistry, AgentPolicy, dynamic agent runtime paths.

## Existing Behavior Regression

- [x] Old skills list/read/run/promote unchanged (64 tests pass)
- [x] Dynamic agents unchanged (208 tests pass)
- [x] ToolDispatcher permission checks unchanged (87 tests pass)
- [x] RuntimeProfile behavior unchanged (32 tests pass)
- [x] MutationLog existing enum values unchanged (57 tests pass)
- [x] StateView existing sections unchanged when retrieval disabled (84 tests pass)
- [x] list_capabilities remains read-only
- [x] search_capability remains read-only
- [x] view_capability remains read-only
- [x] evaluate_capability remains behind lifecycle_tools_enabled
- [x] plan_capability_transition remains behind lifecycle_tools_enabled
- [x] transition_capability remains behind lifecycle_tools_enabled

## Hard Constraint Verification

- [x] run_capability does not exist (verified by grep + Phase 3B regression test + hardening test)
- [x] No capability scripts executed
- [x] No ExperienceCurator exists (verified by grep across all capabilities/ *.py)
- [x] No task-end auto-draft exists
- [x] No automatic promotion
- [x] No modification to existing promote_skill
- [x] No modification to dynamic agents
- [x] No new write tools exposed
- [x] retrieval_enabled does not grant lifecycle permissions
- [x] No full capability documents injected into StateView
- [x] No embeddings or LLM judge used
- [x] No network access in retriever/ranking
- [x] StateViewBuilder does not instantiate CapabilityStore or CapabilityIndex directly
- [x] StateViewBuilder does not own capability lifecycle state

## Known Issues

None.

## Rollback Notes

To roll back Phase 4:
1. Set `capabilities.retrieval_enabled = false` in config.toml
2. Or set `capabilities.enabled = false` in config.toml
3. No code changes needed — all wiring is feature-gated
