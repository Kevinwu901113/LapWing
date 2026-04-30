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

## 8. Phase 2B: Read-Only Capability Tools

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
