# Capability Evolution Architecture Map

Phase 0 baseline map. Describes current (pre-capability) architecture and
where the future capability system will attach. Phase 0/1 does NOT attach to
runtime behavior.

## 1. Current Skill Lifecycle

### Storage: SkillStore (`src/skills/skill_store.py`)
- Directory-based: `data/skills/{skill_id}/SKILL.md`
- YAML frontmatter + Markdown body with Python code block
- Legacy single-file `{skill_id}.md` format auto-migrated on read

### Execution: SkillExecutor (`src/skills/skill_executor.py`)
- Sandboxed execution via ExecutionSandbox (STRICT or STANDARD tier)
- Maturity-gated: draft/testing/broken → STRICT sandbox; stable → STANDARD
- Calls `SkillStore.record_execution()` after each run

### Tool surface: `src/tools/skill_tools.py`
- 8 tools: create_skill, run_skill, edit_skill, list_skills, promote_skill,
  delete_skill, search_skill, install_skill
- `run_skill` has multi-tier gate: non-gated profiles bypass, standard profile
  checks stability + trust_required, inner_tick checks auto_run/inner_tick tags
- `promote_skill` hot-registers stable skill as ToolSpec in registry

### Maturity promotion:
```
draft → testing (auto on first success)
testing → stable (manual via promote_skill tool)
stable → broken (auto on failure)
```

### Autonomous capture: SkillCapturer (`src/skills/skill_capturer.py`)
- Triggered by MaintenanceTimer at 3AM
- Analyzes 24h of TrajectoryStore entries, LLM judges whether to capture
- Creates skills with origin=captured, maturity=draft

## 2. Current Agent Lifecycle

### Catalog: AgentCatalog (`src/agents/catalog.py`)
- SQLite-backed persistent storage for AgentSpec rows
- Integrity via spec_hash verification on read

### Registry: AgentRegistry (`src/agents/registry.py`)
- Facade over Catalog + Factory + Policy
- Two-mode: ephemeral (in-memory), session (in-memory with TTL), persistent (catalog)
- create → ephemeral/session; save → persistent (catalog); destroy → archive/delete

### Policy: AgentPolicy (`src/agents/policy.py`)
- Centralized validation for dynamic agents
- Profile allowlist, model_slot allowlist, lifecycle checks, resource limit sanity
- LLM-based semantic lint for create + save

### Factory: AgentFactory (`src/agents/factory.py`)
- builtin → Researcher.create() / Coder.create() (bypasses spec internals)
- dynamic → DynamicAgent with RuntimeProfile + denylist merge

### Execution: BaseAgent (`src/agents/base.py`)
- Tool loop with BudgetLedger, LoopDetector, ToolDispatcher
- DynamicAgent extends BaseAgent with v2 AgentSpec + RuntimeProfile
- Researcher, Coder are builtin agents with factory methods

### Denylist: DYNAMIC_AGENT_DENYLIST (`src/agents/spec.py`)
- 20+ tools blocked for dynamic agents (create_agent, delegate_*, send_message,
  edit_soul, memory_note, promises, reminders, plans, focus)

## 3. Current Tool Dispatch / Permission Path

ToolDispatcher (`src/core/tool_dispatcher.py`) is the single universal gate:

```
dispatch(request, profile, agent_spec, services, ...)
  → Agent Policy Check (dynamic agents: validate_tool_access + denylist)
  → Tool Lookup (tool_registry.get)
  → Profile Gate (tool_names_for_profile)
  → AuthorityGate (auth_level → authorize)
  → CheckpointManager (snapshot workspace for shell/file-write)
  → VitalGuard (compound command check / file target check)
  → BrowserGuard (browser mount + URL check)
  → ShellPolicy (pre/post execute hooks)
  → AmbientKnowledge cache (research tool only)
  → tool_registry.execute(request, context)
```

## 4. Current Mutation Logging Path

StateMutationLog (`src/logging/state_mutation_log.py`):
- Dual output: SQLite (`mutation_log.db`) + daily JSONL mirror
- 50+ MutationType enum members
- Context propagation via contextvars (`_current_iteration_id`, `_current_chat_id`,
  `_last_llm_request_id`)
- Live SSE fanout via subscribe/unsubscribe

## 5. Where the Future Capability System Will Eventually Attach

The capability system is designed to eventually replace/augment these
attachment points (NOT wired in Phase 0/1):

| Attachment Point | Current Module | Future Role |
|---|---|---|
| Skill loading | SkillStore | CapabilityStore reads capability documents, SkillStore becomes legacy compat adapter |
| Skill execution | SkillExecutor | run_capability replaces execute, with capability-level sandbox policy |
| Skill tool surface | skill_tools.py | Tools read CapabilityDocument for metadata, trust, permissions |
| Agent creation | AgentRegistry | Agents are capabilities with type=dynamic_agent |
| Agent policy | AgentPolicy | Policy reads capability.trust_required, capability.risk_level |
| Tool dispatch | ToolDispatcher | Capability-level permission sets replace per-tool checks |
| State view | StateViewBuilder | Injects capability summaries alongside skill summaries |
| Mutation log | StateMutationLog | Records capability lifecycle events |
| Service wiring | Brain._build_services | Adds capability_store, curator to services dict |
| Runtime profiles | RuntimeProfile | Capability-scoped profiles |

**Phase 0/1 constraint:** None of these attachment points are wired. The new
`src/capabilities/` package is pure data model + parser + hashing, with zero
runtime reachability from Brain, TaskRuntime, StateViewBuilder, SkillExecutor,
ToolDispatcher, or agent execution paths.

## 6. Feature Flag Design

New feature flags under `[capabilities]` section, all defaulting to false/off:

- `capabilities.enabled` — master kill switch
- `capabilities.retrieval_enabled` — enables capability document retrieval
- `capabilities.curator_enabled` — enables ExperienceCurator
- `capabilities.auto_draft_enabled` — enables automatic capability drafting

These flags are defined in the config model and exposed via the compat shim,
but no runtime code reads them in Phase 0/1. They exist so Phase 2+ code
can gate behavior behind them.

## 7. Phase 2A: CapabilityStore + CapabilityIndex + Versioning

### Status: Implemented (2026-04-30)
### Files: `src/capabilities/store.py`, `src/capabilities/index.py`, `src/capabilities/search.py`, `src/capabilities/versioning.py`

### Key Design Decisions

- **Synchronous I/O**: All store/index code uses sync `sqlite3` and sync file I/O. Phase 1 is sync, nothing in runtime calls these yet.
- **Index as derived cache**: The filesystem (CapabilityStore) is the source of truth. The SQLite index mirrors it. `rebuild_index()` provides full consistency recovery.
- **Archive by moving**: `archive()` moves the directory from `<scope>/<id>/` to `archived/<scope>/<id>/`. Writes `manifest.archive.json` with archival metadata.
- **Scope precedence**: `session > workspace > user > global`. Narrower scopes override broader ones when the same cap_id exists across scopes.
- **MutationLog optional**: `CapabilityStore(..., mutation_log=None)`. When provided, records create/disable/archive events. When absent, store works fine.

### CapabilityStore (`src/capabilities/store.py`)

Filesystem-backed CRUD. Supported operations:
- `create_draft(scope, *, cap_id, name, description, ...)` — creates directory layout with CAPABILITY.md, manifest.json, standard subdirs
- `get(capability_id, scope=None)` — loads from disk; scope=None uses precedence
- `list(**filters)` — filters by scope/type/maturity/status/risk_level/tags; excludes disabled/archived by default
- `search(query, *, filters, limit)` — keyword search via index (falls back to filesystem scan)
- `disable(capability_id, scope=None)` — sets status=disabled, preserves files, updates hash
- `archive(capability_id, scope=None)` — moves to archived/ dir, preserves metadata
- `rebuild_index()` — full index rebuild from filesystem
- `refresh_index_for(capability_id, scope=None)` — re-indexes one capability

### CapabilityIndex (`src/capabilities/index.py`)

SQLite-backed fast lookup. Schema includes all manifest fields plus usage/success/failure counters, last_used_at, last_tested_at. Primary key: `(id, scope)`. Indexes on `type`, `scope`, `status`, `maturity`, `risk_level`, `(scope, status)`.

Operations: `upsert`, `remove`, `mark_disabled`, `mark_archived`, `search` (keyword + filters), `resolve_with_precedence`, `rebuild_from_store`.

### Search Helpers (`src/capabilities/search.py`)

Pure functions on `list[CapabilityManifest]`: `filter_active`, `filter_by_tags`, `filter_by_type`, `filter_by_scope`, `filter_stable`, `filter_trust_level`, `text_search`, `deduplicate_by_precedence`, `sort_by_name`, `sort_by_maturity`, `sort_by_updated`.

### Versioning (`src/capabilities/versioning.py`)

Creates version snapshots in `versions/v<version>_<timestamp>/` before destructive state transitions:
- `create_version_snapshot(doc, trigger, *, reason)` — copies CAPABILITY.md and manifest.json
- `snapshot_on_disable(doc)` / `snapshot_on_archive(doc)` — convenience wrappers
- `list_version_snapshots(doc)` — parses `versions/` directory, sorted by timestamp desc

### MutationLog Integration

Four new `MutationType` enum members added to `state_mutation_log.py`:
- `CAPABILITY_DRAFT_CREATED = "capability.draft_created"`
- `CAPABILITY_DISABLED = "capability.disabled"`
- `CAPABILITY_ARCHIVED = "capability.archived"`
- `CAPABILITY_VERSION_CREATED = "capability.version_created"`

CapabilityStore accepts `mutation_log=None`. When provided, `_maybe_record()` calls `record()` after state mutations, wrapped in try/except so failures never break primary operations.

### Config Additions

Two new fields on `CapabilitiesConfig`:
- `data_dir: str = "data/capabilities"` — root directory for capability storage
- `index_db_path: str = "data/capabilities/capability_index.sqlite"` — SQLite index path

### Storage Layout

```
data/capabilities/
  global/<id>/CAPABILITY.md, manifest.json, versions/, scripts/, tests/, ...
  user/<id>/...
  workspace/<id>/...
  session/<id>/...
  archived/<scope>/<id>/...    # moved here by archive()
  capability_index.sqlite
```

### Hard Constraints (still enforced)

- **No runtime wiring**: grep confirms zero non-capabilities imports of `src.capabilities`
- **No Brain wiring**: Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, AgentRegistry untouched
- **No capability execution**: No scripts are executed
- **No retrieval, promotion, evaluation, curation**: Not in this phase
- **Feature flags remain default false**: `capabilities.enabled = false`

### What Phase 2A Does NOT Do

- No read/view/search tools exposed to the agent
- No capability script execution
- No automatic retrieval or injection into StateView
- No promotion/evaluation/policy gate
- No ExperienceCurator
- No vector search
- No rollback/restore from version snapshots (snapshots are write-only for audit)

## 8. Phase 3A: Policy + Evaluator + Eval Records + Promotion Planner

### Status: Implemented (2026-04-30)
### Files: `src/capabilities/policy.py`, `src/capabilities/evaluator.py`, `src/capabilities/eval_records.py`, `src/capabilities/promotion.py`

### Key Design Decisions

- **Deterministic only**: All policy/evaluator/promotion logic is pure computation. No LLM calls, no script execution, no tool registration, no store mutation.
- **Dataclass-based models**: `PolicyDecision`, `EvalRecord`, `EvalFinding`, `PromotionPlan` are all `@dataclass` types — simple data carriers, no Pydantic overhead.
- **No runtime wiring**: None of these modules are importable from Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, or agent runtime paths.
- **Policy handles dict-like and object-like inputs**: `validate_promote` accepts both dict and object forms for `eval_record` and `approval` via `_get_field` helper.
- **PromotionPlanner computes but does not execute**: Returns `PromotionPlan` with `allowed`/`blocking_findings`/`required_approval`. Never calls `CapabilityStore.disable/archive/create`. Never mutates manifest maturity/status.

### CapabilityPolicy (`src/capabilities/policy.py`)

Deterministic policy layer returning `PolicyDecision` (allowed, severity, code, message, details).

Methods:
- `validate_create(manifest, context)` — validates scope, type, maturity, status, risk_level, required_tools
- `validate_patch(old_manifest, new_manifest, context)` — id/scope immutability + revalidation
- `validate_promote(manifest, eval_record, approval, context)` — risk-gated promotion eligibility
- `validate_run(manifest, runtime_profile, context)` — status-gated run eligibility
- `validate_install(manifest, source, context)` — external source quarantine
- `validate_scope(manifest, context)` — scope enum validation
- `validate_required_tools(manifest, available_tools, context)` — tool availability check
- `validate_risk(manifest, context)` — risk/permission compatibility

Policy rules:
- high risk promotion requires explicit owner approval
- medium risk promotion requires approval OR sufficient eval evidence
- low risk promotion allowed if evaluator passes
- quarantined/archived cannot be promoted or run
- disabled cannot be run
- external install source defaults to quarantined unless explicitly trusted
- required_tools must be known when available_tools provided
- policy never grants new permissions, never modifies RuntimeProfile

### CapabilityEvaluator (`src/capabilities/evaluator.py`)

Deterministic evaluation/linting. Returns `EvalRecord` with `EvalFinding` list, `passed`, `score` (0.0-1.0), `required_approval`, `recommended_maturity`.

Checks:
- Required CAPABILITY.md sections: When to use, Procedure, Verification, Failure handling
- Description quality: non-empty, not vague (todo/tbd/wip), minimum length
- Trigger coverage: skills/workflows should have triggers; overbroad triggers (*, .*, always) flagged
- Format validation: required_tools and required_permissions must be list[str]
- Risk/permission consistency: low risk + sensitive perms → warning
- Dangerous shell patterns: rm -rf /, sudo rm, chmod 777, curl|bash, wget|sh, dd if=, mkfs, fork bomb, ~/.ssh, system file writes
- Prompt injection detection: "ignore instructions", "you are now", "pretend you are", "override", "bypass"
- Path references: scripts/tests/examples paths validated; absolute system paths flagged
- Promotion eligibility: stable without eval evidence → info; high risk → info

Scoring: start 1.0, -0.3 per error, -0.1 per warning, floor 0.0.

### Eval Record Persistence (`src/capabilities/eval_records.py`)

Stores JSON records in `<capability_dir>/evals/eval_<timestamp>.json`.

Functions:
- `write_eval_record(record, doc, *, mutation_log)` — writes JSON, optionally records MutationLog
- `read_eval_record(doc, created_at)` — reads one record by timestamp
- `list_eval_records(doc)` — all records sorted by created_at descending
- `get_latest_eval_record(doc)` — most recent record or None

Does NOT: mutate manifest, change maturity/status, trigger promotion.

### PromotionPlanner (`src/capabilities/promotion.py`)

Computes whether a maturity transition would be allowed. Returns `PromotionPlan` with `allowed`, `required_approval`, `required_evidence`, `blocking_findings`, `explanation`.

Supported transitions:
| From | To | Gate |
|------|----|------|
| draft | testing | evaluator pass or only warnings |
| testing | stable | evaluator pass; medium/high risk needs approval |
| stable | broken | requires failure evidence |
| broken | repairing | always allowed |
| repairing | testing | evaluator pass recommended |
| repairing | draft | always allowed |
| testing | draft | always allowed (downgrade) |
| any | disabled/archived | can be planned |

Rules:
- high risk never auto-promotes (requires explicit owner approval)
- quarantined cannot promote directly to stable
- archived cannot transition (restore not implemented)
- planner does NOT mutate store or manifest

### Hard Constraints (still enforced)

- **No runtime wiring**: Only `src/tools/capability_tools.py` and `src/app/container.py` import `src.capabilities`
- **No Brain/TaskRuntime/StateView/SkillExecutor/ToolDispatcher wiring**
- **No script execution**: No capability scripts are executed or imported
- **No promotion execution**: PromotionPlanner computes, does not mutate
- **No write tools**: No create/disable/archive/promote capability tools
- **No run_capability tool**
- **Feature flags remain default false**
- **All Phase 0/1/2A/2B tests pass**

### What Phase 3A Does NOT Do

- No actual promotion wiring (promote_skill unchanged)
- No run_capability implementation
- No script execution
- No automatic retrieval
- No ExperienceCurator
- No dynamic agent changes
- No write tools
- No actual stable promotion
- No mutation of capability maturity/status through promotion.py

## 9. Phase 2B: Read-Only Capability Tools

### Status: Implemented (2026-04-30)
### Files: `src/tools/capability_tools.py`, `tests/capabilities/test_phase2b_tools.py`

### Key Design Decisions

- **Feature-gated**: Tools register only when `capabilities.enabled=true`. When false, zero capability tools exist in the registry.
- **Read-only only**: Three tools — `list_capabilities`, `search_capability`, `view_capability`. No create, disable, archive, promote, or execute tools.
- **capability_read tag**: All three tools use `capability="capability_read"`, `risk_level="low"`. No existing profile uses this capability, so tools are not auto-exposed to any agent surface.
- **Executor closure pattern**: Store and index are captured in executor closures rather than resolved from Brain services, keeping Brain._build_services() untouched.
- **Sync store in async executors**: Executors are `async` functions that call sync store/index methods. The sync I/O is fast (local filesystem reads) and Python's asyncio handles this without blocking.
- **Archived capability lookup**: `view_capability` falls back to scanning `archived/<scope>/<id>/` when `store.get()` fails and `include_archived=true`. When `include_archived=false` and the capability is archived, returns a descriptive error.

### Tool 1: list_capabilities

- Delegates to `CapabilityStore.list()` with user-provided filters.
- Returns compact summaries: id, name, description, type, scope, maturity, status, risk_level, tags, triggers, updated_at.
- Never returns body, scripts, traces, evals, or version contents.
- Default: active only, limit 20, max 100.

### Tool 2: search_capability

- Delegates to `CapabilityIndex.search()` when index is available; falls back to `CapabilityStore.search()` (filesystem scan).
- Supports keyword search across name, description, triggers, tags.
- Filters: scope, type, maturity, status, risk_level, required_tools, tags.
- Default deduplication by scope precedence (session > workspace > user > global).
- `include_all_scopes=true` returns duplicates across scopes.
- Excludes disabled/archived/quarantined by default.

### Tool 3: view_capability

- Delegates to `CapabilityStore.get()` with scope precedence when scope is omitted.
- Falls back to archived directory scan when `include_archived=true`.
- Returns full metadata, body (when `include_body=true`), and file listings (when `include_files=true`).
- File listings are names only — never returns script/trace/eval contents.
- Does not execute or import scripts.
- Body content is treated as untrusted data, returned as payload string.

### Container Wiring

In `src/app/container.py`, after the skill system block:
```python
from config.settings import CAPABILITIES_ENABLED
if CAPABILITIES_ENABLED:
    capability_index = CapabilityIndex(CAPABILITIES_INDEX_DB_PATH)
    capability_index.init()
    capability_store = CapabilityStore(
        data_dir=CAPABILITIES_DATA_DIR,
        mutation_log=self.mutation_log,
        index=capability_index,
    )
    self.brain._capability_store = capability_store
    self.brain._capability_index = capability_index
    register_capability_tools(self.brain.tool_registry, capability_store, capability_index)
```

### Hard Constraints (still enforced)

- **No runtime wiring**: Only `src/tools/capability_tools.py` and `src/app/container.py` import `src.capabilities`. Brain, TaskRuntime, StateViewBuilder, SkillExecutor, ToolDispatcher, agent runtime paths remain clean.
- **No capability execution**: No scripts are executed or imported.
- **No automatic retrieval**: No capability content is auto-injected into StateView or prompts.
- **No mutation tools**: Only read operations. No create/disable/archive/promote.
- **No StateView injection**: StateView has no capability section.
- **Feature flags remain default false**: `capabilities.enabled = false` in config.toml.
- **Existing runtime behavior unchanged**: All Phase 0/1, Phase 2A, and legacy tests pass.

### What Phase 2B Does NOT Do

- No run_capability / execute_capability
- No CapabilityRetriever
- No StateView capability section
- No Brain pre-run retrieval
- No TaskRuntime task-end hook
- No ExperienceCurator
- No SkillEvaluator
- No promotion logic
- No create/disable/archive tools
- No script execution
- No dynamic agent changes

## 10. Phase 3B: Gated Lifecycle Transitions

### Status: Implemented (2026-05-01)
### Files: `src/capabilities/lifecycle.py`, `tests/capabilities/test_phase3b_lifecycle.py`, `tests/capabilities/test_phase3b_transition_atomicity.py`, `tests/capabilities/test_phase3b_regression.py`

### Key Design Decisions

- **CapabilityLifecycleManager** orchestrates Policy, Evaluator, Planner, Store, Versioning, and EvalRecords to apply controlled maturity/status transitions.
- **TransitionResult** dataclass reports full transition state: applied/blocked, before/after maturity and status, eval record id, version snapshot id, policy decisions, blocking findings, content hashes.
- **Planner-first gating**: Every transition is planned by PromotionPlanner, then validated by CapabilityPolicy. If either denies, no files are modified.
- **Evaluator integration**: `draft->testing`, `testing->stable`, and `repairing->testing` always create a fresh eval record before planning.
- **Version snapshots**: Every applied transition writes a version snapshot before mutation.
- **Index refresh**: After every applied transition, the capability index is refreshed so search/list reflect new state.
- **MutationLog integration**: Optional mutation_log records transition events. Log failure never corrupts the transition.
- **Atomic enough for local filesystem**: Snapshot first, then manifest update, then re-parse, then index refresh. Blocked transitions leave zero file changes.

### Supported Transitions

| Transition | Type | Eval Required | Approval |
|---|---|---|---|
| `draft -> testing` | maturity | yes (fresh eval) | not required |
| `testing -> stable` | maturity | yes (fresh eval) | high risk requires; medium risk with passing eval suffices |
| `stable -> broken` | maturity | no | failure_evidence required |
| `broken -> repairing` | maturity | no | not required |
| `repairing -> testing` | maturity | yes (fresh eval) | not required |
| `testing -> draft` | maturity (downgrade) | no | not required |
| `repairing -> draft` | maturity (reset) | no | not required |
| `active -> disabled` | status | no | policy check only |
| `active/.../draft -> archived` | status | no | policy check only |

### Blocking Rules

- Disabled capabilities cannot be promoted (maturity transitions blocked).
- Archived capabilities cannot transition at all (not found by store.get).
- Quarantined capabilities cannot promote to stable.
- High risk capabilities never auto-promote; explicit approval required.

### Apply Transition Flow

1. Resolve capability via `CapabilityStore.get()`.
2. Record `content_hash_before`.
3. For maturity transitions:
   a. Run evaluator (if quality-relevant) and persist eval record.
   b. Plan transition via PromotionPlanner.
   c. Validate via CapabilityPolicy.
   d. If blocked, return TransitionResult(applied=False) — no files changed.
   e. Write version snapshot.
   f. Update manifest maturity/updated_at.
   g. Sync manifest.json, re-parse, refresh index.
   h. Record mutation log event.
   i. Return TransitionResult(applied=True).
4. For status transitions (disable/archive):
   a. Plan check, policy check.
   b. Write version snapshot.
   c. Delegate to CapabilityStore.disable() / archive().
   d. Return TransitionResult(applied=True).

### What Phase 3B Does NOT Do

- No wire into legacy `promote_skill`.
- No user-facing write tools (create/disable/archive/promote capability tools).
- No `run_capability` implementation.
- No script execution.
- No automatic retrieval.
- No ExperienceCurator.
- No dynamic agent changes.
- No Brain / TaskRuntime / StateViewBuilder / SkillExecutor / ToolDispatcher modifications.
- No RuntimeProfile permission grants.
- No restore / unarchive.

## 11. Phase 3C: Lifecycle Management Tools

**Status:** Implemented (2026-05-01)

Phase 3C exposes explicit, feature-gated, operator-only lifecycle management
tools over the Phase 3B `CapabilityLifecycleManager`.

### Feature Flags

| Flag | Default | Effect |
|---|---|---|
| `capabilities.enabled` | `false` | Gates all capability tools |
| `capabilities.lifecycle_tools_enabled` | `false` | Gates lifecycle tools (requires `capabilities.enabled=true`) |

Registration matrix:

| `enabled` | `lifecycle_tools_enabled` | Tools registered |
|---|---|---|
| `false` | any | none |
| `true` | `false` | `list_capabilities`, `search_capability`, `view_capability` |
| `true` | `true` | read-only tools + `evaluate_capability`, `plan_capability_transition`, `transition_capability` |

### Lifecycle Tools

All three tools use `capability="capability_lifecycle"` — distinct from
`capability_read`. They are only accessible to profiles with the
`capability_lifecycle` capability, specifically the
`CAPABILITY_LIFECYCLE_OPERATOR_PROFILE` (`capability_lifecycle_operator`).

#### `evaluate_capability`

- Runs `CapabilityEvaluator` on a capability.
- Optionally persists an `EvalRecord` (`write_record`, default `true`).
- Optionally includes detailed findings (`include_findings`, default `true`).
- **Does not** change manifest maturity or status.
- **Does not** write version snapshots.
- **Does not** execute scripts.

#### `plan_capability_transition`

- Previews whether a transition would be allowed via `CapabilityLifecycleManager.plan_transition()`.
- Read-only: no manifest changes, no snapshots, no index refresh, no MutationLog.
- Returns `allowed`, `required_approval`, `required_evidence`, `blocking_findings`, `policy_decisions`, `explanation`.
- Blocks disabled/archived capabilities from promotion in preview.

#### `transition_capability`

- Applies a lifecycle transition via `CapabilityLifecycleManager.apply_transition()`.
- `dry_run=true` behaves like `plan_capability_transition` (no mutation).
- All Phase 3B gating applies: planner → policy → evaluator → snapshot → mutate → re-parse → index → log.
- Blocked transitions make **zero** file/index/mutation changes.
- Successful transitions: snapshot, manifest update, content_hash recompute, index refresh, optional MutationLog.

### Permission / Profile Rules

- Lifecycle tools use `capability_lifecycle` tag (not `capability_read`).
- Only `CAPABILITY_LIFECYCLE_OPERATOR_PROFILE` grants access.
- Standard, chat_shell, inner_tick, compose_proactive, local_execution, and all
  other profiles do **not** include `capability_lifecycle`.
- No existing broad profile accidentally gains lifecycle permissions.

### What Phase 3C Does NOT Do

- No `run_capability` / `execute_capability`.
- No `create_capability` / `install_capability` / `patch_capability`.
- No `auto_promote_capability`.
- No capability retrieval into StateView.
- No ExperienceCurator.
- No automatic task-end learning.
- No dynamic agent capability binding.
- No Brain / TaskRuntime / StateViewBuilder / SkillExecutor / ToolDispatcher modifications.
- No script execution.

## 12. Phase 4: CapabilityRetriever + Progressive Disclosure

### Status: Implemented (2026-05-01)

### Files
- `src/capabilities/ranking.py` — deterministic scoring (no embeddings, no LLM, no network)
- `src/capabilities/retriever.py` — CapabilityRetriever, CapabilitySummary, RetrievalContext
- `src/core/state_view.py` — CapabilitySummary dataclass + capability_summaries field
- `src/core/state_view_builder.py` — _build_capability_summaries (duck-typed retriever)
- `src/app/container.py` — CapabilityRetriever wiring behind capabilities.retrieval_enabled
- `tests/capabilities/test_phase4_retriever.py` — 63 tests
- `tests/capabilities/test_phase4_state_view.py` — 14 tests

### Key Design Decisions

1. **Progressive disclosure, not instruction injection.**
   StateView receives compact summaries (id, name, description, type, scope,
   maturity, risk_level, triggers, required_tools, match_reason). Full
   CAPABILITY.md body, procedures, scripts, traces, evals, and version
   contents are never injected.

2. **Deterministic ranking only.**
   Scoring uses keyword matching, scope precedence, maturity boost, risk
   penalty, usage stats, and recency. No embeddings, no LLM judge, no
   network access.

3. **Duck-typed wiring.**
   StateViewBuilder receives the retriever as an optional object (no hard
   import from src.capabilities). The builder calls `.retrieve()` only if
   the retriever is present. Failures return empty — never break normal
   chat.

4. **Feature-gated behind capabilities.retrieval_enabled.**
   Requires capabilities.enabled=true. Defaults false. All other capability
   flags are independent.

### Feature Flag Matrix

| capabilities.enabled | capabilities.retrieval_enabled | Behavior |
|---------------------|-------------------------------|----------|
| false               | *                             | No capability section. Existing behavior unchanged. |
| true                | false                         | Tools may exist; no automatic retrieval; no StateView section. |
| true                | true                          | CapabilityRetriever wired. StateView may include compact summaries. |

### Retrieval Flow

1. StateViewBuilder._build_capability_summaries() called during
   build_for_chat / build_for_inner.
2. Query formed from last 3 user/assistant turns via existing
   _trajectory_query_text helper.
3. CapabilityRetriever.retrieve(query, context) called:
   a. _fetch_candidates — index.search() per allowed scope
   b. filter_candidates — apply status/maturity/risk/tools filters
   c. rank_candidates — score and sort, attach match_reason
   d. Return top-k (default 5)
4. Builder converts to StateView CapabilitySummary dataclass.
5. StateView.capability_summaries populated (empty tuple on any failure).

### Filtering Rules

- Exclude archived, disabled, quarantined by default
- Exclude broken maturity always
- Exclude draft by default (include_draft flag)
- Exclude high risk by default (include_high_risk flag)
- Exclude capabilities with unavailable required_tools
- Deduplicate by id with scope precedence: session > workspace > user > global

### Ranking (deterministic)

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

### What Phase 4 Does NOT Do

- No capability execution.
- No `run_capability` tool.
- No full document injection.
- No script execution.
- No ExperienceCurator.
- No task-end auto-draft.
- No automatic promotion.
- No modification to existing promote_skill.
- No modification to dynamic agents.
- No new write tools.
- No embedding-based or LLM-based retrieval.
- No network access.
- retrieval_enabled does not grant lifecycle or read permissions.
- Lifecycle tools remain separately gated behind lifecycle_tools_enabled.