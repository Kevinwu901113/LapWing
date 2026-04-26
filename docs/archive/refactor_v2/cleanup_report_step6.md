# cleanup_report_step6.md — Agent Team 对齐 Refactor v2 架构

Step 6 of the v2.0 recast. The Agent Team scaffolding was already in
place from Phase 6 (merged 2026-04-16), but its observability + capability
boundaries weren't aligned with the v2 architecture that shipped in
Steps 1–5. This Step retrofits the Phase 6 code so that:

- Agent execution emits through `StateMutationLog` (the audit source
  of truth), not `Dispatcher` (the ad-hoc broadcast bus).
- Agent tool whitelists live on `RuntimeProfile` (the same mechanism
  TaskRuntime's main loop uses), not inline `tools: list[str]`.
- No Agent can bypass Lapwing and speak to the user — `tell_user` is
  structurally absent from every Agent profile.
- The `delegate` / `delegate_to_agent` tool descriptions and enum
  populate from `AgentRegistry` live, so adding an Agent doesn't require
  a tool-schema edit.

Branch: `refactor/step6-agent-team-align` from master `11d4517` (post-Step-5).
Final tag: `recast_v2_step6_complete`.

## §1 — Deletion clipboard

| Path / Site | Status | Replacement / Reason |
|-------------|--------|----------------------|
| `src/agents/base.py` constructor param `dispatcher: "Dispatcher"` | REPLACED | Now `mutation_log: "StateMutationLog \| None"`. Dispatcher was the observability source; StateMutationLog is the architectural real source of truth (Blueprint v2.0 §2). |
| `src/agents/base.py:40` `dispatcher.submit(event_type="agent.task_started", ...)` | DELETED | Replaced by `mutation_log.record(MutationType.AGENT_STARTED, ...)`. |
| `src/agents/base.py:102` `dispatcher.submit(event_type="agent.tool_called", ...)` | DELETED | Replaced by `mutation_log.record(MutationType.AGENT_TOOL_CALL, ...)`. |
| `src/tools/agent_tools.py:43` `dispatcher.submit(event_type="agent.task_created", ...)` | DELETED | No separate `task_created` event — `AGENT_STARTED` in `BaseAgent.execute` covers the lifecycle start. Phase 6's double-emit (tool + BaseAgent) was redundant. |
| `src/tools/agent_tools.py:61` `dispatcher.submit(event_type=f"agent.task_{status}", ...)` | DELETED | `AGENT_COMPLETED` / `AGENT_FAILED` emitted inside BaseAgent's `_finalize_*` helpers cover the terminal transitions. |
| `src/tools/agent_tools.py:116` `dispatcher.submit(event_type="agent.task_assigned", ...)` | DELETED | Same as task_created — `AGENT_STARTED` in the sub-agent's execute covers it. Having both the tool and BaseAgent emit separate events for the same transition was a Phase 6 carryover. |
| `src/tools/agent_tools.py:134` `dispatcher.submit(event_type=f"agent.task_{status}", ...)` | DELETED | Same as the outer `_status` emit. |
| `agent_services["dispatcher"]` key in `src/app/container.py` | DELETED | No Agent / agent tool reads it anymore. Removing the key prevents accidental re-introduction of dispatcher-based emits. |
| `dispatcher` parameter on `Researcher.create` / `Coder.create` / `TeamLead.create` | REPLACED | All three factories take `mutation_log` now. |
| Hardcoded `AgentSpec.tools=["research", "browse"]` (etc.) | REPLACED | Agents now use `runtime_profile=AGENT_*_PROFILE`. `AgentSpec.tools` is retained as a legacy fallback only for test fixtures that construct an AgentSpec without a profile. |
| Hardcoded `"enum": ["researcher"]` in delegate_to_agent schema (Phase 6 Spec suggestion) | NEVER LANDED + NOW LIVE | `register_agent_tools` now takes `agent_registry` and populates `enum` from `list_specs()` at registration time. |

## §2 — `grep -rn` verification

```
$ grep -rn 'event_type="agent\.' src/ tests/ --include="*.py" | grep -v __pycache__
src/tools/agent_tools.py:6:``dispatcher.submit(event_type="agent.task_*")`` 双层 emit 已删除，
src/agents/base.py:5:四类 mutation。Phase 6 原本的 ``dispatcher.submit(event_type="agent.*")``
(both are docstring removal-rationale comments, not active emit sites)

$ grep -rn "dispatcher" src/agents/ src/tools/agent_tools.py --include="*.py" | grep -v __pycache__
src/agents/base.py:5:(docstring rationale)
src/tools/agent_tools.py:6:(docstring rationale)
(no active dispatcher references remain)

$ grep -rn "tell_user" src/agents/ --include="*.py" | grep -v __pycache__
src/agents/researcher.py: "你没有 tell_user 权限"（system prompt string）
src/agents/coder.py:     "你没有 tell_user 权限"（system prompt string）
src/agents/team_lead.py: "你没有 tell_user 权限"（system prompt string）
(prompts only — no Agent code invokes tell_user)

$ grep -rn "AGENT_STARTED\|AGENT_COMPLETED\|AGENT_FAILED\|AGENT_TOOL_CALL" \
    src/ --include="*.py" | grep -v __pycache__ | grep -v docstring
src/logging/state_mutation_log.py  (enum definition)
src/agents/base.py                 (4 emit sites)
(single producer — no scattered emit points)
```

## §3 — New TODO/FIXME

None added. Step 6 ships clean — the only deferred items are in §8 and
each has explicit cleanup conditions.

## §4 — Test net change

| Bucket | Δ |
|---|---|
| Baseline (Step 5 final, master `11d4517`) | 1205 |
| BaseAgent RuntimeProfile + tool_call_count in payload | +3 |
| AgentSpec runtime_profile field | +1 |
| Researcher/Coder/TeamLead `test_no_tell_user_capability` (×3) | +3 |
| E2E `test_dynamic_agent_list_in_description` | +1 |
| **Final** | **1213** (+8 net) |

```
$ python -m pytest tests/ -q | tail -1
1213 passed, 2 warnings in 183.34s
```

Note: several tests were *rewritten in place* (dispatcher mock → mutation_log
mock; `spec.tools` assertion → `spec.runtime_profile.tool_names`). The
net-8 count reflects added tests only; zero net deletions.

## §5 — Data / schema changes

| Subsystem | Change | Migration |
|---|---|---|
| `MutationType` enum | Added `AGENT_STARTED = "agent.task_started"`, `AGENT_COMPLETED = "agent.task_done"`, `AGENT_FAILED = "agent.task_failed"`, `AGENT_TOOL_CALL = "agent.tool_called"` | Pure addition. Event string values chosen to match the existing Desktop SSE consumer (`useSSEv2.ts` / `types/events.ts`) — zero frontend changes needed. |
| `RuntimeProfile` constants | Added `AGENT_RESEARCHER_PROFILE`, `AGENT_CODER_PROFILE`, `AGENT_TEAM_LEAD_PROFILE`. Capabilities = `frozenset()` (no `communication`/`commitment`); tool_names whitelisted per Agent. | Pure addition to `_PROFILES` dict. |
| `AgentSpec` dataclass | `tools: list[str]` default changed from required → `default_factory=list`. Added `runtime_profile: "RuntimeProfile \| None" = None`. | Backwards-compatible — `tools` still readable; tests that only set `tools=...` still work. |
| `BaseAgent.__init__` | Param `dispatcher` replaced by `mutation_log`. | Breaking for anyone constructing a BaseAgent directly — only three `.create()` classmethods and the test fixtures were call sites; all updated. |
| `src/app/container.py` Agent wiring block | `agent_services = {"agent_registry": …}` only (dispatcher key dropped); Agent constructors take `self.mutation_log`; `register_agent_tools(registry, agent_registry)` moved to after Agent registration so dynamic schema populates. | No DB schema impact. |
| `register_agent_tools(registry, agent_registry=None)` | Added optional `agent_registry` parameter. Description + enum populated from live registry when provided. | Backwards-compatible — callers without `agent_registry` get the old static description. |

## §6 — Exit invariants (Step 6 contract)

1. **Agent execution emits through StateMutationLog, never through
   Dispatcher.** Verified by `grep -r 'dispatcher' src/agents/
   src/tools/agent_tools.py` — zero active references.
2. **Every Agent has a `RuntimeProfile`; every profile excludes the
   `communication` capability and excludes `tell_user` / `commit_promise`
   / `fulfill_promise` / `abandon_promise` from `tool_names`.** Verified
   by `test_researcher.py::test_no_tell_user_capability` and equivalents
   on Coder / TeamLead.
3. **`BaseAgent._get_tools` routes through `ToolRegistry.function_tools`
   using `runtime_profile` when set; falls back to `spec.tools` only
   when no profile is set.** Verified by
   `TestBaseAgentRuntimeProfile::test_profile_drives_tool_filtering` +
   `test_legacy_tools_fallback`.
4. **`delegate` and `delegate_to_agent` descriptions list every
   registered Agent; `delegate_to_agent.agent` enum equals the
   registered Agent names exactly.** Verified by
   `TestE2ERealDelegation::test_dynamic_agent_list_in_description`.
5. **`AGENT_COMPLETED` payload carries `tool_calls_made: int` and
   `duration_seconds: float`; these never appear on `AgentResult`
   itself.** Verified by
   `TestBaseAgentWithToolCalls::test_tool_call_count_in_completed_payload`.
6. **`AgentResult` schema unchanged** (status/result/reason/evidence/
   artifacts/attempted_actions/task_id). See
   `docs/refactor_v2/step6_agent_result_schema.md` for rationale.

## §7 — Architectural decisions

### Why keep Phase 6 framework instead of rewriting?

See `step6_delegation_reuse.md`. Short answer: the Phase 6 designs for
three-layer orchestration, workspace sandboxing, and Team Lead prompt
engineering are sound domain choices. The broken parts were all at the
plumbing layer (observability source, capability enforcement, schema
dynamism) and are local fixes. Rewriting would discard 21 days of
work + 31 tests for no architectural win.

### Why keep TeamLead as an independent Agent?

See `step6_teamlead_design.md`. Short answer: with only 2 sub-Agents
today, TeamLead's LLM-driven agent selection looks thin. But the Agent
Team is early-stage; future Agents (Browser, Writer) will make
selection genuinely complex. Dynamic prompt-driven routing is cheaper
to extend than hardcoded selection rules, and the 6–10s added latency
is covered by the `tell_user("让团队看看") + commit_promise` UX already
in place.

### Why keep `delegate` + `delegate_to_agent` as two tools instead of one?

Same memo. The two-tool structure matches the layer boundary: Lapwing
delegates to *the team* (she doesn't need to pick a specific Agent),
TeamLead delegates to *a specific Agent*. Collapsing to a single
`delegate_task(agent=...)` would force Lapwing to know Agent names,
coupling her prompt to Agent Team membership. Zero cost to keep;
high cost to change (tests + prompt + TeamLead removal all entangle).

### Why keep event string values `"agent.task_started"` / `"agent.task_done"` / `"agent.task_failed"` instead of renaming to `"agent.started"` etc.?

Desktop v2's `useSSEv2.ts` + `types/events.ts` already recognize the
Phase 6 strings. SSE source switched (dispatcher → mutation_log) but
the string contract is the client-side API. Renaming would be a second,
unrelated breaking change — do it only if the strings themselves
become wrong (they don't; `agent.task_*` fits the lifecycle).

### Why not emit `AGENT_STARTED` from `delegate_executor` too (Lapwing-level task creation)?

`BaseAgent.execute` emits `AGENT_STARTED` for both TeamLead and the
sub-Agent. That's two events per top-level delegate — one per layer,
one per LLM loop. Emitting a third from `delegate_executor` would
add a fourth "created" event that doesn't correspond to an LLM loop,
complicating downstream task-tree reconstruction. Phase 6 did this;
Step 6 drops it because "task created" ≡ "TeamLead's AGENT_STARTED"
in the v2 model.

### Why soft-fail on mutation_log write in BaseAgent?

`BaseAgent._emit` wraps `mutation_log.record` in try/except, logging
warnings and continuing on failure. Rationale: a mutation_log write
failure during an Agent's tool loop must not take the whole loop
down — the user-visible outcome (delegate returns a result) is more
important than perfect audit coverage. `StateMutationLog.record`
itself already soft-fails the JSONL mirror; the outer wrapper here
handles SQLite transient errors the same way.

### Why register `agent_tools` *after* workspace tools + Agents?

`register_agent_tools` reads `agent_registry.list_specs()` at
registration time to populate description + enum. If called before
Agents register, the output has empty agent list — we'd need a rebuild
step. Doing it last eliminates order fragility.

## §8 — Carryover debt registry

Re-evaluation of Step 5's §8 list + Step 6 additions:

| Debt | Source | Step-6 verdict |
|------|--------|----------------|
| `MESSAGE_SPLIT_*` settings unused at runtime | Step 5 | **Not touched this Step** — outside Agent Team scope. Defer to whenever `output_sanitizer` is next audited. |
| `ConversationMemory.user_facts` facade | Step 3 D-1 | **Not touched** — Memory v2 migration is a separate stream. |
| `ConversationMemory.reminders` / `todos` facade | Step 3 D-2 | **Not touched** — same reason. |
| `durable_scheduler._fire_agent` calls `brain.think_conversational` directly | Step 4 D-3 | **Not touched** — MainLoop event API is a future Step. |
| `MemorySnippets` placeholder in StateView | Step 3 C | **Not touched**. |
| `commit_promise.source_trajectory_entry_id = 0` sentinel | Step 5 | **Not touched** — trajectory linkage is memory-layer territory. |
| `IDENTITY_EDITED`, `MEMORY_RAPTOR_UPDATED`, `MEMORY_FILE_EDITED` MutationType members unemitted | Step 1 | **Not touched**. |
| Dispatcher remains for non-Agent subsystems (consciousness / reminder) | Step 1 + this Step | **New entry**: Cleanup condition: when the last non-Agent Dispatcher subscriber migrates to mutation_log or EventQueue. Today the agent.* migration proves the pattern; next candidate is consciousness events. Target: Step 7+. |
| Agent workspace (`data/agent_workspace/`) has no cleanup mechanism | Phase 6 carryover | **New entry**: Coder writes files with no retention policy. Low priority until the workspace grows; then add a periodic cleanup task to heartbeat. |
| Phase 6 plan file `docs/superpowers/plans/2026-04-16-agent-team-phase6.md` describes pre-alignment design | This Step | **New entry**: Plan is historical record; do not edit. Step 6 alignment is documented in `step6_delegation_reuse.md`. |

## §9 — Regression evaluation summary

Step 6 is an alignment step — no new failure-mode regression cases were
introduced (those come with new features, which this Step didn't add).
The existing Step 5 regression suite (8 cases) and Step 4 suite both
pass unchanged, validating that:

- Step 5's `tell_user` structural contract holds (Agents provably
  can't bypass it, because their profile excludes the capability).
- Step 4's MainLoop unification holds (Agent execution still flows
  through TaskRuntime via the delegate tool, not a side channel).

New failure modes to watch for in production:

- **Agent mutation_log write amplification**: one delegate can emit
  ~30–60 `AGENT_TOOL_CALL` events (3 agents × 10–20 tool calls each).
  SQLite journal growth is not tested under this load. Mitigation if
  it hurts: add batching to StateMutationLog, or drop
  `AGENT_TOOL_CALL` payload verbosity (truncate earlier, 800→400).
- **Profile drift**: `AGENT_RESEARCHER_PROFILE.tool_names` hardcodes
  `{"research", "browse"}`. If either tool is renamed, the profile
  silently fails the `ToolNotRegisteredError` strict check at Agent
  runtime (not at container init). Mitigation: move profile→tool
  name validation to `prepare()` boot-time. Future item.

## §10 — Test count reconciliation

```
Step 5 final (11d4517):                      1205
+  改动 1/3/5 (mutation_log, RuntimeProfile,
   tell_user exclusion, dynamic agent_list)  +8   1213
─────────────────────────────────────────────────
Step 6 final:                                      1213
```

Net delta vs Step 5: **+8 tests**. Zero deletions (rewrites happen
in-place). All 1213 green; no skips, no xfails introduced.

Breakdown:
- `test_base_agent.py`: +3 (`test_tool_call_count_in_completed_payload`,
  `test_profile_drives_tool_filtering`, `test_legacy_tools_fallback`)
- `test_types.py`: +1 (`test_runtime_profile_field`)
- `test_researcher.py`: +1 (`test_no_tell_user_capability`)
- `test_coder.py`: +1 (`test_no_tell_user_capability`)
- `test_team_lead.py`: +1 (`test_no_tell_user_capability`)
- `test_e2e_delegation.py`: +1 (`test_dynamic_agent_list_in_description`)
