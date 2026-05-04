# Snooker Session Fix 2026-05-04

## Scope

This repair follows `data/reports/snooker_session_audit_20260503.md` and fixes the child-agent service injection failure plus the ambient pollution chain that amplified stale or weak research output into proactive factual claims.

## Commits

| Task | Commit | Summary |
|------|--------|---------|
| P0-1 / P0-2 / P0-3 | `41dbe4c` | Repaired legacy agent service override, canonicalized `llm_router`, removed the `_run_agent` service refill hotfix, and propagated child hard tool errors. |
| P1-1 | `18425ba` | Added and ran the one-off ambient cleanup script; committed the cleanup report. |
| P1-2 / P1-3 | `5003758` | Tightened research ambient writeback and cache-hit rules. |
| P1-4 | `6f0bc00` | Filtered and conflict-suppressed ambient awareness injection, with source/confidence/age metadata. |
| P2 | `51b8756` | Added proactive `send_message` factual-claim gate requiring fresh non-cache evidence or softened wording. |

Note: P0 and P1-2/P1-3 were committed as grouped atomic changes because the branch already contained unrelated WIP in shared files. Only task-specific hunks were staged; unrelated WIP remains unstaged.

## Changed Files

- P0: `src/agents/base.py`, `src/agents/registry.py`, `src/agents/types.py`, `src/core/brain.py`, `src/core/tool_dispatcher.py`, `src/tools/agent_tools.py`, plus agent delegation regression tests.
- P1-1: `scripts/cleanup_polluted_ambient.py`, `data/reports/ambient_cleanup_20260504T132016Z.md`.
- P1-2/P1-3: `src/core/task_runtime.py`, `tests/core/test_task_runtime_ambient_writeback.py`, `tests/core/test_task_runtime_ambient_cache.py`.
- P1-4: `src/core/state_view_builder.py`, `src/core/state_serializer.py`, related serializer/builder tests.
- P2: `src/tools/personal_tools.py`, `tests/tools/test_send_message_factual_gate.py`.

## Validation

- Targeted delegation failure subset after fixture repair: `venv/bin/python -m pytest -p no:rerunfailures tests/agents/test_e2e_chain_trace.py tests/agents/test_e2e_delegation.py tests/agents/test_e2e_dynamic.py tests/agents/test_e2e_shim.py tests/tools/test_agent_tools_v2.py -q` -> `37 passed`.
- Repair regression suite: `venv/bin/python -m pytest -p no:rerunfailures tests/tools/test_agent_tools.py tests/tools/test_agent_tools_v2.py tests/agents/test_agent_tools_v2.py tests/agents/test_agent_tool_dispatcher.py tests/agents/test_registry.py tests/agents/test_e2e_chain_trace.py tests/agents/test_e2e_delegation.py tests/agents/test_e2e_dynamic.py tests/agents/test_e2e_shim.py tests/core/test_tool_dispatcher.py tests/core/test_task_runtime_ambient_writeback.py tests/core/test_task_runtime_ambient_cache.py tests/core/test_state_view_builder.py tests/core/test_state_serializer.py tests/tools/test_send_message_factual_gate.py tests/tools/test_personal_tools.py tests/core/test_proactive_message_gate.py -q` -> `291 passed`.
- Full regression: `venv/bin/python -m pytest -p no:rerunfailures tests/ -q` -> `4821 passed, 11 skipped` in 819.09s.
- Ambient cleanup verification: `remaining_polluted_before_cutoff=0` for `source='research_writeback' AND confidence < 0.7 AND fetched_at < 2026-05-04T00:00:00+08:00`.

## Runtime Notes

- `research_engine`, `ambient_store`, and `llm_router` are now required for researcher delegation before execution; if they are absent, `delegate_to_agent` returns `success=False` with `agent_services_unavailable` rather than letting a child agent fabricate a plausible explanation.
- Legacy cached agents now receive the current `services_override`, which removes the need for the previous per-delegation `_services` refill hotfix in `agent_tools._run_agent`.
- Ambient cache hits now expose `cache_hit: True` and `cached_at`, giving downstream gates a concrete freshness signal.
- Proactive factual claims such as “刚搜到” are blocked unless the current iteration has a fresh non-cache `research` or `get_sports_score` result, or the wording explicitly weakens the claim as cached/previous information.

## Not Performed

- The 30-minute cold-start runtime soak and live snooker replay were not run in this shell session. The code-level and full pytest regression gates passed; runtime soak should be done under the service supervisor to include inner tick timing and real adapter delivery.
