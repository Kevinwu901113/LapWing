# Phase 6A Impact Map — Dynamic Agent Capability-Backed Metadata

**Date:** 2026-05-02
**Phase:** 6A — metadata foundation only

---

## Current AgentSpec Fields (`src/agents/spec.py`)

| Field | Type | Default | In spec_hash |
|-------|------|---------|--------------|
| id | str | `agent_<12hex>` | No |
| name | str | `""` | Yes |
| display_name | str | `""` | No |
| description | str | `""` | No |
| kind | Literal["builtin","dynamic"] | `"dynamic"` | No |
| version | int | 1 | No |
| status | Literal["active","archived","disabled"] | `"active"` | No |
| system_prompt | str | `""` | Yes |
| model_slot | str | `"agent_researcher"` | Yes |
| runtime_profile | str | `""` | Yes |
| tool_denylist | list[str] | `[]` | Yes |
| lifecycle | AgentLifecyclePolicy | ephemeral/3600/1 | No |
| resource_limits | AgentResourceLimits | defaults | Yes (all 5 fields) |
| created_by | str | `"brain"` | No |
| created_reason | str | `""` | No |
| created_at | datetime | now() | No |
| updated_at | datetime | now() | No |

## AgentLifecyclePolicy Fields

| Field | Type | Default |
|-------|------|---------|
| mode | Literal["ephemeral","session","persistent"] | `"ephemeral"` |
| ttl_seconds | int\|None | 3600 |
| max_runs | int\|None | 1 |
| reusable | bool | False |

## AgentResourceLimits Fields

| Field | Type | Default |
|-------|------|---------|
| max_tool_calls | int | 20 |
| max_llm_calls | int | 8 |
| max_tokens | int | 30000 |
| max_wall_time_seconds | int | 180 |
| max_child_agents | int | 0 |

## Current Persistence Format

- **Backend:** SQLite via `AgentCatalog` (`src/agents/catalog.py`)
- **Serialization:** `dataclasses.asdict(spec)` → `json.dumps(data, default=str)`
- **Deserialization:** `json.loads(spec_json_str)` → pop lifecycle + resource_limits → `AgentSpec(**raw, lifecycle=..., resource_limits=..., created_at=..., updated_at=...)`
- **Integrity:** `spec_hash` column verified against computed `spec.spec_hash()` on every read
- **Extra fields:** Any extra keys in spec_json would be passed to AgentSpec constructor via `**raw` — currently would raise TypeError for unknown fields

## Current Save Path

1. `save_agent_executor` (agent_tools.py) → `registry.save_agent(name, reason, run_history)`
2. `AgentRegistry.save_agent` → `policy.validate_save(spec, run_history)` → promote lifecycle to persistent → `catalog.save(promoted)`
3. `AgentCatalog.save` → `asdict(spec)` → `json.dumps` → SQLite INSERT OR REPLACE

## Current Runtime Creation Path

1. `create_agent_executor` (agent_tools.py) → `registry.create_agent(request, ctx)`
2. `AgentRegistry.create_agent` → `policy.validate_create(request, ctx, session_count)` → returns AgentSpec → stores in ephemeral/session dict
3. `AgentPolicy.validate_create` → validates profile, model_slot, lifecycle, resource_limits, name, semantic lint → returns AgentSpec with defaults

## Current AgentPolicy Validation Path

- **validate_create:** profile ∈ ALLOWED_DYNAMIC_PROFILES, model_slot ∈ ALLOWED_MODEL_SLOTS, lifecycle ∈ {ephemeral, session}, session count ≤ 5, resource limits ≤ max, name normalization, semantic lint
- **validate_tool_access:** denylist check → profile resolution → tool_names allowlist check
- **validate_save:** run_history truthiness, duplicate name, persistent count ≤ 10, tool_denylist subset, semantic lint

## Current ToolDispatcher Interaction

- DynamicAgent passes `self.dynamic_spec` to ToolDispatcher via `_dispatch_agent_spec()`
- ToolDispatcher uses spec only for operator profile gating (checks if profile is in `operator_profiles` set)
- No capability-based gating in ToolDispatcher for dynamic agents currently

## Where Capability-Backed Metadata Can Be Added Safely

### Safe additions (Phase 6A):
1. **AgentSpec new fields** — dataclass defaults ensure backward compatibility. `_row_to_spec` uses `**raw` so new fields pass through automatically if they have defaults.
2. **spec_hash expansion** — include structural metadata fields (bound_capabilities, risk_level, etc.), exclude runtime counters (success_count, failure_count).
3. **AgentPolicy.validate_capability_metadata** — read-only lint method, no save gate changes.
4. **AgentRegistry._spec_to_summary** — optionally expose new fields in `full=True` mode.

### NOT safe (deferred to Phase 6B+):
- Capability auto-loading into agents
- Runtime capability enforcement
- CapabilityStore imports in agent modules
- save_agent gate changes
- ToolDispatcher capability checks for agents
- Dynamic agent self-elevation

## Explicit Non-Runtime Guarantee

**Phase 6A does NOT change:**
- Dynamic agent execution semantics
- ToolDispatcher behavior
- RuntimeProfile resolution
- save_agent enforcement (beyond existing checks)
- Agent creation flow
- Agent lifecycle management
- Brain/TaskRuntime behavior
- StateView content
- Any tool's runtime behavior

**Phase 6A ONLY adds:**
- Optional metadata fields with safe defaults
- Read-only policy lint method
- Tests for metadata validation
