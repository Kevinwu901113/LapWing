# Dynamic Agent System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Dynamic Agent System per the blueprint — Brain can create/save/destroy/delegate to dynamic agents at runtime, with builtin researcher/coder migrated to the new spec-driven model, all enforced by an AgentPolicy + per-turn BudgetLedger.

**Architecture:** AgentSpec (config-only, persistable) → AgentCatalog (SQLite store) → AgentFactory (instantiates BaseAgent/DynamicAgent from spec) → AgentRegistry (facade over catalog+factory) → 5 Brain tools (`delegate_to_agent`, `list_agents`, `create_agent`, `destroy_agent`, `save_agent`). Permissions are spec-driven via existing RuntimeProfile + a hard-coded `DYNAMIC_AGENT_DENYLIST`, validated by `AgentPolicy` (incl. NIM-based semantic lint, fail-closed). A turn-shared `BudgetLedger` caps LLM/tool/token/wall-time/depth across Brain + delegated agents. Builtin Researcher/Coder are upserted into the catalog as `kind="builtin"` and Factory routes to their existing `create()` constructors.

**Tech Stack:** Python 3.11+, asyncio, aiosqlite, dataclasses; existing Lapwing infra (`StateMutationLog`, `LLMRouter`, `ToolRegistry`, `RuntimeProfile`, `StateViewBuilder`).

**Source of truth:** `docs/superpowers/plans/2026-04-28-dynamic-agent-system-blueprint.md` (or the inline blueprint in conversation). Sections referenced as `§N` below.

---

## Phase 0 — Workspace baseline

### Task 0: Pin baseline + save blueprint

**Files:**
- Create: `docs/superpowers/plans/2026-04-28-dynamic-agent-system-blueprint.md` (verbatim copy of the blueprint as single source of truth)

- [ ] **Step 1: Save the blueprint verbatim**

Copy the full blueprint markdown that the user shared into the file above. Do not edit, summarize, or reformat. Future tasks reference it by section.

- [ ] **Step 2: Confirm working tree clean and on a feature branch**

Run: `git status && git log -1 --oneline`
Expected: clean tree, current commit `2b8d75b fix(phase2): close current-info bypass paths in IntentRouter + fallback`.

- [ ] **Step 3: Create feature branch**

Run: `git checkout -b feat/dynamic-agent-system`
Expected: switched to new branch.

- [ ] **Step 4: Commit blueprint copy**

```bash
git add docs/superpowers/plans/2026-04-28-dynamic-agent-system-blueprint.md \
        docs/superpowers/plans/2026-04-28-dynamic-agent-system.md
git commit -m "docs(plan): pin dynamic-agent blueprint + plan"
```

---

## Phase 1 — Data models

Foundation. No dependencies on later phases. Land first so all subsequent code can import the new types.

### Task 1: AgentSpec, lifecycle, resource limits + constants

Implements **§1.1** in full.

**Files:**
- Create: `src/agents/spec.py`
- Test: `tests/agents/test_spec.py`

- [ ] **Step 1: Write failing tests for AgentSpec**

Per blueprint **T-01**-related preconditions and §1.1 contract. Tests must cover:
- defaults match blueprint (`mode="ephemeral"`, `ttl_seconds=3600`, `max_runs=1`, `max_tool_calls=20`, etc.)
- `id` auto-generated as `agent_<12 hex>`
- `created_at` / `updated_at` populated via `src.core.time_utils.now`
- `spec_hash()` is deterministic for identical content, changes when `system_prompt` / `model_slot` / `runtime_profile` / `tool_denylist` / any `resource_limits` field changes; reordering `tool_denylist` does NOT change the hash
- frozenset constants `ALLOWED_MODEL_SLOTS`, `ALLOWED_DYNAMIC_PROFILES`, `DYNAMIC_AGENT_DENYLIST` contain the exact members listed in §1.1

```python
# tests/agents/test_spec.py
from src.agents.spec import (
    AgentSpec, AgentLifecyclePolicy, AgentResourceLimits,
    ALLOWED_MODEL_SLOTS, ALLOWED_DYNAMIC_PROFILES, DYNAMIC_AGENT_DENYLIST,
)

def test_defaults():
    s = AgentSpec(name="x", system_prompt="p", runtime_profile="agent_researcher")
    assert s.kind == "dynamic"
    assert s.lifecycle.mode == "ephemeral"
    assert s.lifecycle.ttl_seconds == 3600
    assert s.lifecycle.max_runs == 1
    assert s.resource_limits.max_tool_calls == 20
    assert s.resource_limits.max_child_agents == 0
    assert s.id.startswith("agent_") and len(s.id) == 18

def test_spec_hash_deterministic_and_order_invariant():
    a = AgentSpec(name="x", tool_denylist=["a", "b"])
    b = AgentSpec(name="x", tool_denylist=["b", "a"])
    # name + tool_denylist contribute; everything else default
    assert a.spec_hash() == b.spec_hash()

def test_spec_hash_changes_on_prompt_change():
    a = AgentSpec(name="x", system_prompt="p1")
    b = AgentSpec(name="x", system_prompt="p2")
    assert a.spec_hash() != b.spec_hash()

def test_constants_exact_members():
    assert ALLOWED_MODEL_SLOTS == frozenset({
        "agent_researcher", "agent_coder", "lightweight_judgment",
    })
    assert ALLOWED_DYNAMIC_PROFILES == frozenset({
        "agent_researcher", "agent_coder",
    })
    # spot-check denylist coverage required by T-14
    for t in [
        "create_agent", "destroy_agent", "save_agent", "delegate_to_agent",
        "delegate_to_researcher", "delegate_to_coder",
        "send_message", "send_image", "proactive_send",
        "memory_note", "edit_soul", "edit_voice", "add_correction",
        "commit_promise", "fulfill_promise", "abandon_promise",
        "set_reminder", "cancel_reminder",
        "plan_task", "update_plan",
        "close_focus", "recall_focus",
    ]:
        assert t in DYNAMIC_AGENT_DENYLIST, t
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `pytest tests/agents/test_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: src.agents.spec`.

- [ ] **Step 3: Implement `src/agents/spec.py`**

Copy the `AgentLifecyclePolicy`, `AgentResourceLimits`, `AgentSpec`, `spec_hash()`, and three constants verbatim from blueprint §1.1. Use `from src.core.time_utils import now as local_now` (file already exists). Do NOT add fields the blueprint does not define.

- [ ] **Step 4: Run test, verify PASS**

Run: `pytest tests/agents/test_spec.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agents/spec.py tests/agents/test_spec.py
git commit -m "feat(agents): add AgentSpec + lifecycle/limits + denylist constants"
```

---

### Task 2: Rename legacy AgentSpec; add `AgentResult.budget_status`

Implements **§1.2**. Production code keeps using existing module path during this task — it gets switched in Task 8/10.

**Files:**
- Modify: `src/agents/types.py`
- Modify: `tests/agents/test_types.py` (add new field test)

- [ ] **Step 1: Read current `src/agents/types.py`**

Note current `AgentSpec` class definition (the legacy dataclass with `tools`/`runtime_profile`/`max_rounds`).

- [ ] **Step 2: Write failing test for `budget_status`**

Append to `tests/agents/test_types.py`:
```python
from src.agents.types import AgentResult

def test_agent_result_budget_status_default():
    r = AgentResult(task_id="t1", status="done", result="ok")
    assert r.budget_status == ""

def test_agent_result_budget_status_set():
    r = AgentResult(task_id="t1", status="done", result="ok",
                    budget_status="budget_exhausted")
    assert r.budget_status == "budget_exhausted"
```

Also add a test that `LegacyAgentSpec` is importable:
```python
from src.agents.types import LegacyAgentSpec
def test_legacy_spec_alias():
    s = LegacyAgentSpec(name="x", description="", system_prompt="",
                        model_slot="agent_researcher")
    assert s.name == "x"
```

- [ ] **Step 3: Run test, verify FAIL**

Run: `pytest tests/agents/test_types.py -v`
Expected: FAIL on `budget_status` and `LegacyAgentSpec` (ImportError).

- [ ] **Step 4: Edit `src/agents/types.py`**

1. Rename the existing `AgentSpec` class to `LegacyAgentSpec` (keep all fields).
2. Re-export it as `AgentSpec` to avoid breaking imports during transition: `AgentSpec = LegacyAgentSpec` at module level.
3. Add `budget_status: str = ""` to `AgentResult` (last field, with default).

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/agents/test_types.py -v`
Expected: PASS.

Run full agent suite to confirm no regression: `pytest tests/agents -v`
Expected: PASS (existing tests still use the alias).

- [ ] **Step 6: Commit**

```bash
git add src/agents/types.py tests/agents/test_types.py
git commit -m "refactor(agents): alias legacy AgentSpec, add AgentResult.budget_status"
```

---

## Phase 2 — Stores

### Task 3: AgentCatalog (SQLite)

Implements **§2** in full.

**Files:**
- Create: `src/agents/catalog.py`
- Test: `tests/agents/test_catalog.py`

- [ ] **Step 1: Write failing tests**

Cover blueprint §2 contract:
- `init()` creates table + indices (idempotent)
- `save()` round-trips spec via `get()` and `get_by_name()` (full equality on every field including nested lifecycle/limits)
- `save()` overwrites by id (`INSERT OR REPLACE`)
- `list_specs(kind="builtin")` filters correctly; `list_specs(status="archived")` filters correctly
- `archive()` flips status to `archived` without deleting
- `delete()` hard-deletes
- `count(kind="dynamic", status="active")` returns correct count
- DB file path uses temp file (use `tmp_path` fixture)

```python
import pytest
from pathlib import Path
from src.agents.catalog import AgentCatalog
from src.agents.spec import AgentSpec, AgentLifecyclePolicy

@pytest.mark.asyncio
async def test_init_idempotent(tmp_path):
    db = tmp_path / "x.db"
    cat = AgentCatalog(db)
    await cat.init()
    await cat.init()  # idempotent

@pytest.mark.asyncio
async def test_save_get_roundtrip(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    s = AgentSpec(name="alpha", system_prompt="hi",
                  runtime_profile="agent_researcher",
                  lifecycle=AgentLifecyclePolicy(mode="persistent"))
    await cat.save(s)
    got = await cat.get(s.id)
    assert got is not None
    assert got.name == "alpha"
    assert got.lifecycle.mode == "persistent"
    by_name = await cat.get_by_name("alpha")
    assert by_name.id == s.id

@pytest.mark.asyncio
async def test_list_filters(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    await cat.save(AgentSpec(name="b1", kind="builtin"))
    await cat.save(AgentSpec(name="d1", kind="dynamic"))
    builtins = await cat.list_specs(kind="builtin")
    assert {s.name for s in builtins} == {"b1"}

@pytest.mark.asyncio
async def test_archive_and_count(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    s = AgentSpec(name="x"); await cat.save(s)
    assert await cat.count(status="active") == 1
    await cat.archive(s.id)
    got = await cat.get(s.id)
    assert got.status == "archived"
    assert await cat.count(status="active") == 0
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/agents/test_catalog.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/agents/catalog.py`**

Use `aiosqlite` (already a dep — `feedback/correction_store.py` shows precedent for sqlite, but use `aiosqlite` since `commitments.py` and `state_mutation_log.py` use async sqlite). Schema exactly as in blueprint §2. Serialize `AgentSpec` to JSON via `dataclasses.asdict` + `json.dumps(default=str)` (datetimes); deserialize with explicit reconstruction (`AgentLifecyclePolicy(**row["lifecycle"])`, `AgentResourceLimits(**row["resource_limits"])`, parse `created_at`/`updated_at` via `datetime.fromisoformat`).

`save()` must compute `spec.spec_hash()` and store separately for audit. `save()` updates `spec.updated_at = local_now()` before persisting.

Skeleton:
```python
import json
from datetime import datetime
from pathlib import Path
import aiosqlite
from src.agents.spec import AgentSpec, AgentLifecyclePolicy, AgentResourceLimits
from src.core.time_utils import now as local_now

class AgentCatalog:
    TABLE = "agent_catalog"
    def __init__(self, db_path):
        self._db_path = str(db_path)
    async def init(self): ...
    async def save(self, spec: AgentSpec) -> None: ...
    async def get(self, agent_id: str) -> AgentSpec | None: ...
    async def get_by_name(self, name: str) -> AgentSpec | None: ...
    async def list_specs(self, *, kind=None, status=None, limit=50): ...
    async def archive(self, agent_id: str) -> None: ...
    async def delete(self, agent_id: str) -> None: ...
    async def count(self, *, kind=None, status=None) -> int: ...
```

Use the `CREATE TABLE IF NOT EXISTS` + indices SQL verbatim from §2.

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/agents/test_catalog.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agents/catalog.py tests/agents/test_catalog.py
git commit -m "feat(agents): add AgentCatalog SQLite store"
```

---

### Task 4: New MutationType members + denylist guard value

Implements **§11.1, §11.2** definitions only. Payload emitters land in later tasks.

**Files:**
- Modify: `src/logging/state_mutation_log.py`
- Test: `tests/logging/test_state_mutation_log.py` (extend) — file path verify; if dir layout differs, drop into `tests/agents/test_mutation_types_dynamic_agent.py`.

- [ ] **Step 1: Confirm existing test file**

Run: `find /home/kevin/lapwing/tests -name "test_state_mutation_log*.py"`

- [ ] **Step 2: Write failing test asserting new members**

```python
from src.logging.state_mutation_log import MutationType
def test_dynamic_agent_mutation_types_present():
    assert MutationType.AGENT_CREATED.value == "agent.created"
    assert MutationType.AGENT_SAVED.value == "agent.saved"
    assert MutationType.AGENT_DESTROYED.value == "agent.destroyed"
    assert MutationType.AGENT_SPEC_UPDATED.value == "agent.spec_updated"
    assert MutationType.AGENT_BUDGET_EXHAUSTED.value == "agent.budget_exhausted"
```

- [ ] **Step 3: Run, verify FAIL**

Expected: AttributeError.

- [ ] **Step 4: Add the 5 enum members to `MutationType`**

In `src/logging/state_mutation_log.py`, add (alongside existing `AGENT_*` block ~ line 130):
```python
AGENT_CREATED = "agent.created"
AGENT_SAVED = "agent.saved"
AGENT_DESTROYED = "agent.destroyed"
AGENT_SPEC_UPDATED = "agent.spec_updated"
AGENT_BUDGET_EXHAUSTED = "agent.budget_exhausted"
```

- [ ] **Step 5: Run, verify PASS**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(logging): add dynamic agent MutationType members"
```

---

## Phase 3 — Policy + Budget

These are leaf modules with no inter-dependency, can be implemented in parallel.

### Task 5: BudgetLedger

Implements **§5** in full.

**Files:**
- Create: `src/agents/budget.py`
- Test: `tests/agents/test_budget.py`

- [ ] **Step 1: Write failing tests covering every dimension (T-08)**

```python
import pytest
from src.agents.budget import BudgetLedger, BudgetExhausted

def test_llm_call_limit():
    led = BudgetLedger(max_llm_calls=2)
    led.charge_llm_call(); led.charge_llm_call()
    with pytest.raises(BudgetExhausted) as ei:
        led.charge_llm_call()
    assert ei.value.dimension == "llm_calls"

def test_tool_call_limit():
    led = BudgetLedger(max_tool_calls=1)
    led.charge_tool_call()
    with pytest.raises(BudgetExhausted):
        led.charge_tool_call()

def test_token_limit():
    led = BudgetLedger(max_total_tokens=100)
    led.charge_llm_call(input_tokens=60, output_tokens=30)
    with pytest.raises(BudgetExhausted):
        led.charge_llm_call(input_tokens=20)

def test_delegation_depth():
    led = BudgetLedger(max_delegation_depth=1)
    led.enter_delegation()
    with pytest.raises(BudgetExhausted):
        led.enter_delegation()
    led.exit_delegation()
    led.enter_delegation()  # should now succeed

def test_snapshot_and_exhausted():
    led = BudgetLedger(max_llm_calls=1)
    led.charge_llm_call()
    snap = led.snapshot()
    assert snap.llm_calls_used == 1
    try: led.charge_llm_call()
    except BudgetExhausted: pass
    assert led.exhausted is True
```

- [ ] **Step 2: FAIL** → `pytest tests/agents/test_budget.py -v`

- [ ] **Step 3: Implement `src/agents/budget.py`**

Per §5. `wall_time_seconds` accumulates via `time.perf_counter()` — provide `start()` that records baseline and a property/method that returns elapsed; check at every `charge_*`. `check()` re-validates all dimensions.

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add src/agents/budget.py tests/agents/test_budget.py
git commit -m "feat(agents): add BudgetLedger turn-shared budget"
```

---

### Task 6: AgentPolicy + LintResult + AgentPolicyViolation

Implements **§4** in full incl. **§4.1** semantic lint fail-closed.

**Files:**
- Create: `src/agents/policy.py`
- Test: `tests/agents/test_policy.py`

- [ ] **Step 1: Write failing tests covering T-04, T-07, T-11**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agents.policy import (
    AgentPolicy, AgentPolicyViolation, LintResult,
)
from src.agents.spec import AgentSpec, AgentLifecyclePolicy
from src.agents.catalog import AgentCatalog

# Helper: build a CreateAgentInput-shaped dataclass
from src.agents.policy import CreateAgentInput

def _safe_lint():
    return LintResult(verdict="safe", reason="ok")

@pytest.mark.asyncio
async def test_validate_create_rejects_unknown_profile(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            CreateAgentInput(
                name_hint="t", purpose="p", instructions="x",
                profile="admin_full_access", model_slot="agent_researcher",
                lifecycle="ephemeral", max_runs=1, ttl_seconds=3600,
            ),
            creator_context=MagicMock(),
        )

@pytest.mark.asyncio
async def test_validate_create_rejects_persistent_lifecycle(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            CreateAgentInput(profile="agent_researcher", lifecycle="persistent",
                             name_hint="t", purpose="p", instructions="x",
                             model_slot="agent_researcher",
                             max_runs=1, ttl_seconds=3600),
            creator_context=MagicMock(),
        )

@pytest.mark.asyncio
async def test_lint_fail_closed_on_uncertain(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=LintResult(verdict="uncertain"))
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            CreateAgentInput(profile="agent_researcher", lifecycle="ephemeral",
                             name_hint="t", purpose="p", instructions="x",
                             model_slot="agent_researcher",
                             max_runs=1, ttl_seconds=3600),
            creator_context=MagicMock(),
        )

@pytest.mark.asyncio
async def test_lint_fail_closed_on_exception(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_create(
            CreateAgentInput(profile="agent_researcher", lifecycle="ephemeral",
                             name_hint="t", purpose="p", instructions="x",
                             model_slot="agent_researcher",
                             max_runs=1, ttl_seconds=3600),
            creator_context=MagicMock(),
        )

def test_validate_tool_access_blocks_denylist():
    pol = AgentPolicy(catalog=MagicMock())
    spec = AgentSpec(name="x", runtime_profile="agent_researcher")
    assert pol.validate_tool_access(spec, "send_message") is False
    assert pol.validate_tool_access(spec, "research") is True

@pytest.mark.asyncio
async def test_validate_save_rejects_unrun(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    pol = AgentPolicy(cat)
    pol._semantic_lint = AsyncMock(return_value=_safe_lint())
    spec = AgentSpec(name="x", runtime_profile="agent_researcher")
    with pytest.raises(AgentPolicyViolation):
        await pol.validate_save(spec, run_history=[])
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement `src/agents/policy.py`**

Per §4 + §4.1. Define:
- `LintResult` dataclass (verdict, risk_categories, reason)
- `AgentPolicyViolation(Exception)` with `reason`/`details`
- `CreateAgentInput` dataclass mirroring blueprint create_agent schema (§7.2)
- `AgentPolicy` with `MAX_PERSISTENT_AGENTS=10`, `MAX_SESSION_AGENTS=5`
- `validate_create()`: checks (1) profile in `ALLOWED_DYNAMIC_PROFILES`, (2) model_slot in `ALLOWED_MODEL_SLOTS`, (3) `tool_denylist` ⊆ `DYNAMIC_AGENT_DENYLIST` (denylist may only ADD restrictions, never relax), (4) resource_limits ranges (positive ints, max_wall_time_seconds ≤ 600), (5) `name = _normalize_name(name_hint)`, append `_<4 hex>` suffix if collision in catalog, snake_case (lowercase ascii + digits + underscore), (6) lifecycle ∈ {"ephemeral","session"}, (7) `_semantic_lint(instructions)` — fail-closed: only `verdict=="safe"` proceeds. Returns the constructed `AgentSpec`.
- `_semantic_lint()`: calls `lightweight_judgment` slot via `LLMRouter`; system prompt as in §4.1; expects strict JSON; on any exception / non-JSON / verdict ≠ "safe" returns `LintResult(verdict="unsafe", ...)` so caller raises.

> **Open knob:** the policy needs an `LLMRouter` for `_semantic_lint`. Add an optional constructor arg `llm_router` (allowed to be `None` only in tests where `_semantic_lint` is monkey-patched). Production wiring (Task 18) provides the real router.

- [ ] **Step 4: PASS**

Run: `pytest tests/agents/test_policy.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agents/policy.py tests/agents/test_policy.py
git commit -m "feat(agents): add AgentPolicy + fail-closed semantic lint"
```

---

## Phase 4 — Factory + Dynamic agent + BaseAgent hooks

### Task 7: AgentFactory

Implements **§3** in full.

**Files:**
- Create: `src/agents/factory.py`
- Test: `tests/agents/test_factory.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from unittest.mock import MagicMock
from src.agents.factory import AgentFactory
from src.agents.spec import AgentSpec, AgentLifecyclePolicy
from src.agents.dynamic import DynamicAgent
from src.agents.researcher import Researcher
from src.agents.coder import Coder

def test_create_builtin_researcher(monkeypatch):
    # Researcher.create reads get_settings().agent_team.researcher — stub it.
    fake_cfg = MagicMock(max_rounds=15, max_tokens=30000, timeout_seconds=180)
    monkeypatch.setattr("src.agents.researcher.get_settings",
                        lambda: MagicMock(agent_team=MagicMock(researcher=fake_cfg)))
    f = AgentFactory(llm_router=MagicMock(), tool_registry=MagicMock(),
                     mutation_log=MagicMock())
    spec = AgentSpec(id="builtin_researcher", name="researcher",
                     kind="builtin", runtime_profile="agent_researcher",
                     model_slot="agent_researcher")
    inst = f.create(spec)
    assert isinstance(inst, Researcher)
    # Builtin path discards new AgentSpec's prompt/limits — not asserted here.

def test_create_builtin_coder(monkeypatch):
    fake_cfg = MagicMock(max_rounds=15, max_tokens=30000, timeout_seconds=180)
    monkeypatch.setattr("src.agents.coder.get_settings",
                        lambda: MagicMock(agent_team=MagicMock(coder=fake_cfg)))
    f = AgentFactory(MagicMock(), MagicMock(), MagicMock())
    spec = AgentSpec(id="builtin_coder", name="coder", kind="builtin",
                     runtime_profile="agent_coder", model_slot="agent_coder")
    assert isinstance(f.create(spec), Coder)

def test_create_dynamic():
    f = AgentFactory(MagicMock(), MagicMock(), MagicMock())
    spec = AgentSpec(name="translator", kind="dynamic",
                     runtime_profile="agent_researcher",
                     model_slot="agent_researcher",
                     system_prompt="translate")
    inst = f.create(spec)
    assert isinstance(inst, DynamicAgent)

def test_resolve_profile_merges_denylist():
    f = AgentFactory(MagicMock(), MagicMock(), MagicMock())
    spec = AgentSpec(name="x", kind="dynamic",
                     runtime_profile="agent_researcher",
                     tool_denylist=["browse"])
    profile = f._resolve_profile(spec)
    # browse is in agent_researcher tool_names but spec excludes it
    assert "browse" in profile.exclude_tool_names
    # DYNAMIC_AGENT_DENYLIST also merged
    assert "send_message" in profile.exclude_tool_names
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement `src/agents/factory.py`**

Per §3:
- Constructor stores router/registry/mutation_log
- `_resolve_profile(spec)`:
  - look up `RuntimeProfile` by `spec.runtime_profile` from `src.core.runtime_profiles` (use a small registry or `getattr` against module)
  - return new `RuntimeProfile(..., exclude_tool_names = base.exclude_tool_names | set(spec.tool_denylist) | (DYNAMIC_AGENT_DENYLIST if spec.kind=="dynamic" else frozenset()))`
- `create(spec)`:
  - if `kind=="builtin"` and `name=="researcher"`: `return Researcher.create(self.llm_router, self.tool_registry, self.mutation_log)` — **existing classmethod takes NO `spec` argument**. It builds its own `LegacyAgentSpec` from `get_settings().agent_team.researcher`. Per blueprint §8, the builtin `AgentSpec` has `system_prompt=""` precisely because `Researcher.create()` generates the prompt internally. Factory therefore **discards** the new `AgentSpec`'s system_prompt/model_slot/limits for builtins — the catalog spec is metadata for listing/audit only, NOT a runtime config override for builtins. Call `os.makedirs` only for dynamic; builtin workspace stays as-is.
  - if `kind=="builtin"` and `name=="coder"`: `return Coder.create(self.llm_router, self.tool_registry, self.mutation_log)` — same pattern.
  - else: build `DynamicAgent(spec=spec, profile=resolved_profile, llm_router=self.llm_router, tool_registry=self.tool_registry, mutation_log=self.mutation_log, services={"shell_default_cwd": f"/tmp/lapwing/agents/{spec.id}"})`. `os.makedirs(..., exist_ok=True)` first.

> **Type discipline:** `Researcher`/`Coder` continue to import `LegacyAgentSpec` (aliased as `AgentSpec` in `src/agents/types.py` per Task 2). The new `src.agents.spec.AgentSpec` is used only by Catalog/Factory/DynamicAgent/Registry/Policy. Do NOT change the Researcher/Coder import paths in this PR — that's an explicit non-goal (reduces blast radius).

- [ ] **Step 4: PASS**

- [ ] **Step 5: Add VitalGuard cwd-confinement test**

Per blueprint §12, dynamic agents rely on **existing** VitalGuard path protection — no code change to VitalGuard. Add a regression test that proves a DynamicAgent with cwd `/tmp/lapwing/agents/<id>/` cannot write outside it:

```python
# tests/agents/test_factory.py (append)
@pytest.mark.asyncio
async def test_dynamic_agent_cwd_blocks_outside_writes(tmp_path, monkeypatch):
    spec = AgentSpec(name="probe", kind="dynamic",
                     runtime_profile="agent_coder", model_slot="agent_coder")
    f = AgentFactory(MagicMock(), real_tool_registry, MagicMock())
    agent = f.create(spec)
    # attempt a write to /etc/passwd via the agent's tool registry
    res = await real_tool_registry.execute(
        ToolExecutionRequest(name="ws_file_write",
            arguments={"path": "/etc/passwd", "content": "x"}),
        ctx=agent_ctx_for(agent),
    )
    assert res.success is False
    assert "outside" in (res.reason or "").lower() or "denied" in (res.reason or "").lower()
```

If VitalGuard does not currently confine to the agent cwd (verify with `grep -n "VitalGuard\|sandbox_root" src/core/vital_guard.py`), STOP and surface to user — blueprint claims existing protection covers this. Do not add new guard logic in this PR.

- [ ] **Step 6: Commit**

```bash
git add src/agents/factory.py tests/agents/test_factory.py
git commit -m "feat(agents): add AgentFactory dispatching builtin vs dynamic"
```

---

### Task 8: DynamicAgent

Implements **§3** DynamicAgent + **§14** denylist runtime check + budget plumbing.

**Files:**
- Create: `src/agents/dynamic.py`
- Test: `tests/agents/test_dynamic_agent.py`

- [ ] **Step 1: Write failing tests covering T-05, T-06, T-08, T-14**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agents.dynamic import DynamicAgent
from src.agents.spec import AgentSpec, AgentResourceLimits
from src.agents.budget import BudgetLedger, BudgetExhausted
from src.agents.types import AgentMessage
from src.logging.state_mutation_log import MutationType

@pytest.mark.asyncio
async def test_denylist_tool_blocked_at_runtime(monkeypatch, tool_registry_with_send_message,
                                                fake_llm_router_returning_send_message_call,
                                                fake_mutation_log):
    spec = AgentSpec(name="x", kind="dynamic", runtime_profile="agent_researcher",
                     model_slot="agent_researcher", system_prompt="p",
                     tool_denylist=[])  # blueprint denylist still applies (T-06)
    agent = DynamicAgent(spec=spec, profile=MagicMock(),
                         llm_router=fake_llm_router_returning_send_message_call,
                         tool_registry=tool_registry_with_send_message,
                         mutation_log=fake_mutation_log)
    msg = AgentMessage(from_agent="lapwing", to_agent="x", task_id="t1",
                       content="do x", message_type="request")
    result = await agent.execute(msg)
    # send_message must NOT have been executed
    assert tool_registry_with_send_message.executed_calls == []
    # TOOL_DENIED with guard='dynamic_agent_denylist'
    denied = [e for e in fake_mutation_log.events if e.event_type == MutationType.TOOL_DENIED]
    assert any(e.payload.get("guard") == "dynamic_agent_denylist" for e in denied)

@pytest.mark.parametrize("tool_name", sorted([
    "create_agent", "list_agents", "save_agent", "destroy_agent", "delegate_to_agent",
    "delegate_to_researcher", "delegate_to_coder",
    "send_message", "send_image", "proactive_send",
    "memory_note", "edit_soul", "edit_voice", "add_correction",
    "commit_promise", "fulfill_promise", "abandon_promise",
    "set_reminder", "cancel_reminder",
    "plan_task", "update_plan",
    "close_focus", "recall_focus",
]))
@pytest.mark.asyncio
async def test_t14_every_denylist_tool_blocked_at_runtime(
    tool_name, build_dynamic_agent_with_llm_calling, fake_mutation_log,
):
    """T-14: every member of DYNAMIC_AGENT_DENYLIST must be runtime-blocked
    for a DynamicAgent even when spec.tool_denylist is empty (T-06 invariant)."""
    agent, registry = build_dynamic_agent_with_llm_calling(tool_name)
    msg = AgentMessage(from_agent="lapwing", to_agent="x", task_id="t",
                       content="x", message_type="request")
    await agent.execute(msg)
    assert tool_name not in [c.name for c in registry.executed_calls]
    denied = [e for e in fake_mutation_log.events
              if e.event_type == MutationType.TOOL_DENIED
              and e.payload.get("tool") == tool_name]
    assert denied, f"expected TOOL_DENIED for {tool_name}"
    assert denied[0].payload.get("guard") == "dynamic_agent_denylist"

@pytest.mark.asyncio
async def test_budget_exhausted_returns_partial(fake_llm_router_endless_loop,
                                                tool_registry_safe, fake_mutation_log):
    spec = AgentSpec(name="x", kind="dynamic", runtime_profile="agent_researcher",
                     model_slot="agent_researcher", system_prompt="p",
                     resource_limits=AgentResourceLimits(max_llm_calls=2))
    led = BudgetLedger(max_llm_calls=2)
    agent = DynamicAgent(spec=spec, profile=MagicMock(),
                         llm_router=fake_llm_router_endless_loop,
                         tool_registry=tool_registry_safe,
                         mutation_log=fake_mutation_log,
                         services={"budget_ledger": led})
    msg = AgentMessage(from_agent="lapwing", to_agent="x", task_id="t",
                       content="x", message_type="request")
    result = await agent.execute(msg)
    assert result.budget_status == "budget_exhausted"
    assert result.status == "done"
```

Use module-scoped fixtures in `tests/agents/conftest.py` for the fake LLM/tool stubs (or inline factories).

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement `src/agents/dynamic.py`**

`DynamicAgent(BaseAgent)` overrides:
- Tool dispatch wrapper — before each tool execution, check `if self.spec.kind == "dynamic" and tool_name in DYNAMIC_AGENT_DENYLIST or tool_name in self.spec.tool_denylist:` → emit `TOOL_DENIED(guard="dynamic_agent_denylist", agent_name=self.spec.name, tool=tool_name, reason="...")`, append a synthetic tool_result block telling the LLM the tool is forbidden, continue loop without calling the executor.
- Budget hook — pull `BudgetLedger` from `self._services.get("budget_ledger")` (may be `None` for unit tests with own ledger). Wrap LLM call with `try: ledger.charge_llm_call(...)` and tool exec with `try: ledger.charge_tool_call()`; on `BudgetExhausted`: emit `AGENT_BUDGET_EXHAUSTED` mutation, break loop, return `AgentResult(task_id, status="done", result=<partial summary>, budget_status="budget_exhausted")`.

- [ ] **Step 4: PASS** → `pytest tests/agents/test_dynamic_agent.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/agents/dynamic.py tests/agents/test_dynamic_agent.py tests/agents/conftest.py
git commit -m "feat(agents): add DynamicAgent with runtime denylist + budget hooks"
```

---

### Task 9: BaseAgent budget + denylist hooks (shared with builtin)

Implements **§3 BaseAgent 改造** so Researcher/Coder also honor budget. Denylist is dynamic-only (per spec) — builtins keep full RuntimeProfile.

**Files:**
- Modify: `src/agents/base.py`
- Test: `tests/agents/test_base_agent.py` (extend)

- [ ] **Step 1: Write failing test that BaseAgent stops on budget exhaustion**

```python
import pytest
from src.agents.base import BaseAgent
from src.agents.types import LegacyAgentSpec, AgentMessage
from src.agents.budget import BudgetLedger

@pytest.mark.asyncio
async def test_base_agent_respects_budget_ledger(
    fake_llm_router_endless_loop, tool_registry_safe, fake_mutation_log,
):
    spec = LegacyAgentSpec(name="probe", description="", system_prompt="p",
                            model_slot="agent_researcher", max_rounds=20)
    led = BudgetLedger(max_llm_calls=2)
    agent = BaseAgent(spec=spec,
                     llm_router=fake_llm_router_endless_loop,
                     tool_registry=tool_registry_safe,
                     mutation_log=fake_mutation_log,
                     services={"budget_ledger": led})
    msg = AgentMessage(from_agent="lapwing", to_agent="probe", task_id="t",
                       content="x", message_type="request")
    result = await agent.execute(msg)
    assert result.budget_status == "budget_exhausted"
    assert result.status == "done"
    # AGENT_BUDGET_EXHAUSTED emitted
    from src.logging.state_mutation_log import MutationType
    assert any(e.event_type == MutationType.AGENT_BUDGET_EXHAUSTED
               for e in fake_mutation_log.events)
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Edit `src/agents/base.py`**

In the existing tool loop:
- Before LLM call: `ledger = self._services.get("budget_ledger")`; if not None: `try: ledger.charge_llm_call(input_tokens=..., output_tokens=...) except BudgetExhausted as e: ...emit AGENT_BUDGET_EXHAUSTED... break`.
- Before tool execution: `ledger.charge_tool_call()` likewise.
- Capture partial state in returned `AgentResult` with `budget_status="budget_exhausted"`.

Important: do not change the constructor signature today — `_services` is already accepted. Do not add policy enforcement to BaseAgent (that's DynamicAgent's job).

- [ ] **Step 4: PASS** + run full agent test suite to ensure existing Researcher/Coder tests still pass.

Run: `pytest tests/agents -v`

- [ ] **Step 5: Commit**

```bash
git add src/agents/base.py tests/agents/test_base_agent.py
git commit -m "feat(agents): wire BudgetLedger into BaseAgent tool loop"
```

---

## Phase 5 — Registry refactor

### Task 10: AgentRegistry as Catalog+Factory facade

Implements **§6** in full. Backwards-compat: keep `register()` / `get()` / `list_names()` for callers in `app/container.py` until rewired.

**Files:**
- Modify: `src/agents/registry.py`
- Create: `tests/agents/test_registry_v2.py`

- [ ] **Step 1: Write failing tests covering T-09, T-10, T-11**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agents.registry import AgentRegistry
from src.agents.catalog import AgentCatalog
from src.agents.factory import AgentFactory
from src.agents.policy import AgentPolicy, CreateAgentInput
from src.agents.spec import AgentSpec, AgentLifecyclePolicy

@pytest.mark.asyncio
async def test_init_upserts_builtins(tmp_path):
    cat = AgentCatalog(tmp_path / "x.db"); await cat.init()
    fac = MagicMock()
    pol = AgentPolicy(cat); pol._semantic_lint = AsyncMock()
    reg = AgentRegistry(cat, fac, pol); await reg.init()
    assert (await cat.get_by_name("researcher")).kind == "builtin"
    assert (await cat.get_by_name("coder")).kind == "builtin"

@pytest.mark.asyncio
async def test_session_agent_fresh_runtime(tmp_path):
    # T-09: each get_or_create_instance returns a fresh agent
    ...

@pytest.mark.asyncio
async def test_persistent_agent_only_spec(tmp_path):
    # T-10: after save_agent, repo holds spec only; instance is recreated on demand
    ...
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement new `src/agents/registry.py`**

Per §6 verbatim. Internal state:
- `_session_agents: dict[str, _SessionEntry]` (TTL-tracked)
- `_ephemeral_agents: dict[str, AgentSpec]` (no DB)
- `_session_instances: dict[str, BaseAgent]` (keyed by name) — fresh runtime per delegation per T-09 means **do NOT cache instance**; the instance is created fresh in `get_or_create_instance()` from the cached spec. The "session" semantic is that the spec persists in memory and TTL ticks; runtime is always fresh.

Public methods:
- `init()`: ensure builtin specs exist in catalog (upsert by name).
- `create_agent(request, ctx)` → calls `policy.validate_create(...)` → places spec in `_ephemeral_agents` or `_session_agents` per lifecycle.
- `get_or_create_instance(name)` → search ephemeral → session → catalog (active+builtin+persistent); call `factory.create(spec)`. Updates `last_used_at` for session.
- `destroy_agent(name)` → forbid for builtins; remove from session/ephemeral; for persistent → archive in catalog (do NOT physically delete to keep audit trail).
- `save_agent(name, reason, run_history)` → `policy.validate_save(...)` → set `lifecycle.mode="persistent"`, `created_reason=reason`, `updated_at`, `version+=1`; persist via `catalog.save()`; remove from session/ephemeral.
- `list_agents(full=False)` → merge sources, mask `system_prompt`.
- `render_agent_summary_for_stateview()` → sync, in-memory only (do NOT do async DB calls). Pulls builtin from a cached snapshot built at `init()`; ephemeral + session from in-memory dicts. **Persistent agents are NOT shown** to keep snapshot sync — only builtin + active session/ephemeral. (Blueprint §6 +§9.4 explicitly limits to builtin + session/ephemeral.)
- `cleanup_expired_sessions()` → walk `_session_agents`, remove entries where `time.monotonic() - last_used_at > ttl_seconds`; emit `AGENT_DESTROYED` per removal.

Keep legacy `register/get/list_names` as compatibility methods that internally consult `_session_instances` for tests, OR migrate Task 18 to update callers. Choose: **keep legacy methods returning instances for direct registration in tests**.

- [ ] **Step 4: PASS**

Run: `pytest tests/agents -v`
Expected: all green; existing `test_registry.py` may need legacy method preserved.

- [ ] **Step 5: Commit**

```bash
git add src/agents/registry.py tests/agents/test_registry_v2.py
git commit -m "refactor(agents): AgentRegistry as Catalog+Factory facade"
```

---

## Phase 6 — Brain tools

### Task 11: Five new agent tools

Implements **§7.1, §7.2, §7.3** in full. Old shims land in Task 12.

**Files:**
- Modify: `src/tools/agent_tools.py`
- Test: `tests/tools/test_agent_tools_v2.py`

- [ ] **Step 1: Write failing tests for each executor**

For each of `delegate_to_agent`, `list_agents`, `create_agent`, `destroy_agent`, `save_agent`:
- happy path returns `ToolExecutionResult(success=True, payload=...)`
- agent-not-found → `success=False`
- builtin destroy → `success=False`
- create_agent: invalid profile → `success=False, reason=...`
- save_agent: builtin → `success=False`

Plus a test covering `delegate_to_agent` enters/exits delegation on the BudgetLedger:
```python
async def test_delegate_to_agent_charges_delegation_depth(...):
    ledger = BudgetLedger(max_delegation_depth=1)
    ctx.services["budget_ledger"] = ledger
    # nested delegation must fail
    ...
```

And: ephemeral `max_runs` auto-destroy after the delegation:
```python
async def test_ephemeral_auto_destroy_after_max_runs(...):
    # spec.lifecycle.max_runs=1 → after delegate, agent removed from registry
    ...
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement executors per §7.3**

In `src/tools/agent_tools.py`:
- Add the five schemas (DELEGATE_TO_AGENT_SCHEMA, LIST_AGENTS_SCHEMA, CREATE_AGENT_SCHEMA, DESTROY_AGENT_SCHEMA, SAVE_AGENT_SCHEMA) verbatim from §7.2.
- Add executors:
  - `delegate_to_agent_executor(req, ctx)` — refactor existing `_run_agent(name, request, context_digest, ctx)` helper at `src/tools/agent_tools.py:81` to be the budget-aware delegation core: it owns `ledger.enter_delegation()` / `exit_delegation()` (single source of truth — do NOT add the same hooks in the executor wrapper). The new `delegate_to_agent_executor` is a thin wrapper that pulls `agent_name` + `task` + `context` + `expected_output` from `req.arguments`, then delegates to `_run_agent(agent_name, task, context, ctx, expected_output=expected_output)`. Ephemeral `max_runs` post-delegation cleanup also lives in `_run_agent` so it applies to shim callers too.
  - `list_agents_executor(req, ctx)` — call `registry.list_agents(full=arguments.get("full", False))`.
  - `create_agent_executor(req, ctx)` — build `CreateAgentInput`, call `registry.create_agent(...)`, on `AgentPolicyViolation` return `success=False` with `payload={"reason": e.reason, "details": e.details}`. Emit `AGENT_CREATED` on success.
  - `destroy_agent_executor(req, ctx)` — block builtins; emit `AGENT_DESTROYED(reason="manual")`.
  - `save_agent_executor(req, ctx)` — block builtins; build `run_history` by querying mutation_log for `AGENT_COMPLETED` matching `agent_name`; emit `AGENT_SAVED` on success.

Register each via the existing `register_agent_tools(...)` function (extend).

- [ ] **Step 4: PASS** → `pytest tests/tools/test_agent_tools_v2.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/tools/agent_tools.py tests/tools/test_agent_tools_v2.py
git commit -m "feat(tools): add delegate_to_agent + list/create/destroy/save_agent"
```

---

### Task 12: Convert legacy delegate_to_researcher/coder to shims

Implements **§7.4**. Tools stay registered (so old tests / persisted plans still resolve), but are removed from every RuntimeProfile in Task 13.

**Files:**
- Modify: `src/tools/agent_tools.py`
- Test: `tests/agents/test_e2e_shim.py`

- [ ] **Step 1: Write failing test (T-02)**

```python
@pytest.mark.asyncio
async def test_shim_matches_new_path(make_ctx_with_registry):
    # Same input through both paths produces equivalent ToolExecutionResult
    new = await delegate_to_agent_executor(
        ToolExecutionRequest(name="delegate_to_agent",
            arguments={"agent_name":"researcher","task":"find foo"}),
        ctx)
    shim = await delegate_to_researcher_executor(
        ToolExecutionRequest(name="delegate_to_researcher",
            arguments={"request":"find foo"}),
        ctx)
    assert new.success == shim.success
    assert new.payload.keys() == shim.payload.keys()
```

- [ ] **Step 2: FAIL** (current shim does not exist; old executor is full implementation)

- [ ] **Step 3: Replace existing `delegate_to_researcher_executor` and `delegate_to_coder_executor` with shims**

Per §7.4 verbatim. Old implementation logic moves into `delegate_to_agent_executor` (already in Task 11). The shim only constructs a new `ToolExecutionRequest` and forwards.

- [ ] **Step 4: PASS** + run full tool tests `pytest tests/tools tests/agents -v`

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor(tools): legacy delegate_* now thin shims over delegate_to_agent"
```

---

## Phase 7 — StateView + Profiles + Config

### Task 13: RuntimeProfile tool_names + authority_gate update

Implements **§10.1, §10.2**, plus a corollary update to `src/core/authority_gate.py` (lines 85–86 currently grant `OWNER` to `delegate_to_researcher` / `delegate_to_coder` — the new tools need the same authority entries or they will be denied at the auth check before reaching the tool registry).

**Files:**
- Modify: `src/core/runtime_profiles.py`
- Modify: `src/core/authority_gate.py` (add `delegate_to_agent`, `list_agents`, `create_agent`, `destroy_agent`, `save_agent` with `AuthLevel.OWNER`; keep legacy two entries for shim compatibility)
- Modify: `tests/core/test_runtime_profiles_exclusion.py`
- Test: `tests/core/test_authority_gate_dynamic_agents.py` (new)

- [ ] **Step 1: Write failing tests (T-12)**

```python
def test_chat_extended_has_delegate_to_agent_only():
    tools = resolve_tools(CHAT_EXTENDED_PROFILE, registry)
    assert "delegate_to_agent" in tools
    assert "list_agents" in tools
    for forbidden in ("create_agent", "destroy_agent", "save_agent",
                      "delegate_to_researcher", "delegate_to_coder"):
        assert forbidden not in tools

def test_task_execution_has_all_five():
    tools = resolve_tools(TASK_EXECUTION_PROFILE, registry)
    for t in ("delegate_to_agent", "list_agents", "create_agent",
              "destroy_agent", "save_agent"):
        assert t in tools

def test_chat_minimal_no_agent_tools():
    tools = resolve_tools(CHAT_MINIMAL_PROFILE, registry)
    for t in ("delegate_to_agent","list_agents","create_agent",
              "destroy_agent","save_agent"):
        assert t not in tools

def test_research_browse_mutually_exclusive_with_delegate_to_agent():
    # CHAT_EXTENDED has research/browse → delegate_to_agent must not appear there?
    # Per blueprint §10.2, the rule is: if delegate_to_agent is exposed, research/browse
    # should not be — exception: AGENT_RESEARCHER_PROFILE. Fix CHAT_EXTENDED accordingly.
    ...
```

> **Resolution per blueprint §10.2:** CHAT_EXTENDED currently has `research`/`browse`. Adding `delegate_to_agent` to it violates the new exclusion rule. Per §10.1 the blueprint says `CHAT_EXTENDED_PROFILE 新增 tool_names: delegate_to_agent, list_agents`. Per §10.2 it then says they're mutually exclusive. **Reconcile by removing `research` and `browse` from CHAT_EXTENDED** — chat now goes through `delegate_to_agent` for research. Add this to test assertions.

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Edit `src/core/runtime_profiles.py`**

- `CHAT_EXTENDED_PROFILE`: add `delegate_to_agent`, `list_agents`; remove `research`, `browse`, `get_sports_score` (web capability).
- `TASK_EXECUTION_PROFILE`: add `delegate_to_agent`, `list_agents`, `create_agent`, `destroy_agent`, `save_agent`. Confirm `exclude_tool_names` already excludes `research/browse` (it does per current code at line 135).
- `COMPOSE_PROACTIVE_PROFILE`: replace `delegate_to_researcher`, `delegate_to_coder` → `delegate_to_agent`, `list_agents`.
- Globally: ensure no profile lists `delegate_to_researcher` or `delegate_to_coder` in `tool_names`. Add them to a project-wide hidden list if needed (or rely on registry exclusion at resolution time).

- [ ] **Step 4: Update `authority_gate.py`**

Open the file and locate the auth-level dict (around lines 80-90 — verify with `grep -n "delegate_to_researcher" src/core/authority_gate.py`). Add:
```python
"delegate_to_agent": AuthLevel.OWNER,
"list_agents": AuthLevel.OWNER,
"create_agent": AuthLevel.OWNER,
"destroy_agent": AuthLevel.OWNER,
"save_agent": AuthLevel.OWNER,
```

- [ ] **Step 5: Write authority_gate test**

```python
from src.core.authority_gate import AUTH_LEVELS, AuthLevel
def test_dynamic_agent_tools_have_owner_authority():
    for t in ["delegate_to_agent","list_agents","create_agent",
              "destroy_agent","save_agent"]:
        assert AUTH_LEVELS[t] == AuthLevel.OWNER
```

(Adjust import name to whatever the actual symbol is — `AUTH_LEVELS` / `_AUTH_LEVELS` / `auth_levels`.)

- [ ] **Step 6: PASS**

Run: `pytest tests/core/test_runtime_profiles_exclusion.py tests/core/test_authority_gate_dynamic_agents.py -v && pytest tests/core -v`

- [ ] **Step 7: Commit**

```bash
git commit -am "refactor(profiles+auth): wire dynamic agent tools through profiles + authority_gate"
```

---

### Task 14: StateView agent_summary field + builder + serializer

Implements **§9.1–§9.4**.

**Files:**
- Modify: `src/core/state_view.py`
- Modify: `src/core/state_view_builder.py`
- Modify: `src/core/state_serializer.py`
- Test: `tests/core/test_stateview_agent_summary.py`

- [ ] **Step 1: Write failing tests (T-03)**

```python
def test_stateview_renders_agent_summary(builder_with_registry):
    view = builder_with_registry.build_for_chat(...)
    assert view.agent_summary is not None
    rendered = serializer.render(view)
    assert "可用 Agent:" in rendered
    assert "researcher" in rendered
    assert "coder" in rendered

def test_dynamic_agent_appears_after_creation(builder_with_registry, registry):
    # create translator_a3f2 ephemeral
    ...
    view = builder_with_registry.build_for_chat(...)
    assert "translator_" in view.agent_summary

def test_system_prompt_not_leaked(builder_with_registry):
    view = builder_with_registry.build_for_chat(...)
    rendered = serializer.render(view)
    assert "BUILTIN_RESEARCHER_SYSTEM_PROMPT" not in rendered  # placeholder
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement**

- `state_view.py`: `agent_summary: str | None = None`.
- `state_view_builder.py`:
  - `__init__` adds optional `agent_registry: AgentRegistry | None = None`.
  - `_build_agent_summary()` returns `agent_registry.render_agent_summary_for_stateview()` or `None`.
  - `build_for_chat` and `build_for_inner` populate `agent_summary`.
- `state_serializer.py` `_render_runtime_state()`: locate the function (run `grep -n "_render_runtime_state\|commitments" src/core/state_serializer.py` first), insert agent block immediately before the commitments block (per §9.3):
  ```python
  if state.agent_summary:
      lines.append("")
      lines.append(state.agent_summary)
  ```
  If `_render_runtime_state` does not exist under that exact name, find the equivalent function building the runtime-state section of the system prompt and insert there. Confirm via a string match in the unit test below.
- `registry.render_agent_summary_for_stateview()`: synchronous, builds string per §9.4 format with truncation rules.

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(stateview): inject compact agent summary into runtime state block"
```

---

### Task 15: config.toml additions

Implements **§13**.

**Files:**
- Modify: `/home/kevin/lapwing/config.toml` (repo-root, NOT `config/config.toml` — there is no such directory)
- Modify: `/home/kevin/lapwing/config.example.toml`

- [ ] **Step 1: Append `[agent_team.dynamic]` and `[budget]` sections**

Use the exact TOML from §13. Mirror to `config.example.toml`.

- [ ] **Step 2: Verify config loader picks them up**

Run: `python -c "from src.config.settings import load_config; c=load_config(); print(c.budget, c.agent_team.dynamic)"`
Expected: prints values matching defaults.

> If the existing `Settings` schema does not yet have nested fields, add them in `src/config/settings.py` (assumed location — find the actual one first via `grep -rn "class Settings" src/`).

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(config): add [agent_team.dynamic] and [budget] sections"
```

---

## Phase 8 — App wiring + builtin specs

### Task 16: Builtin AgentSpec definitions

Implements **§8**.

**Files:**
- Create: `src/agents/builtin_specs.py`
- Test: covered indirectly via Task 10 (`test_init_upserts_builtins`).

- [ ] **Step 1: Add `BUILTIN_RESEARCHER_SPEC` and `BUILTIN_CODER_SPEC` exactly as in §8**

- [ ] **Step 2: Update `AgentRegistry.init()`** (Task 10) to import and upsert these.

> If Task 10 already imports from `builtin_specs.py`, this task only finalizes the file.

- [ ] **Step 3: Run** `pytest tests/agents/test_registry_v2.py::test_init_upserts_builtins -v`

- [ ] **Step 4: Commit**

```bash
git add src/agents/builtin_specs.py
git commit -m "feat(agents): define builtin researcher/coder AgentSpec"
```

---

### Task 17: AppContainer wiring

Stitches Catalog + Factory + Policy + Registry + per-turn BudgetLedger into the running app.

**Files:**
- Modify: `src/app/container.py`
- Modify: `src/core/task_runtime.py` (or wherever `complete_chat` lives) for per-turn ledger
- Test: `tests/app/test_container_wiring.py` (smoke)

- [ ] **Step 1: Read current container assembly**

```bash
grep -n "AgentRegistry\|register_agent_tools\|StateViewBuilder\|complete_chat" \
     /home/kevin/lapwing/src/app/container.py /home/kevin/lapwing/src/core/task_runtime.py
```

Pin the exact method name where the per-turn budget hook will live (likely `TaskRuntime.complete_chat` or `TaskRuntime.run_turn` — confirm before editing). Pin the `AppContainer` bootstrap method (likely `__init__` + an async `start()`/`init()` — confirm; the `AppContainer.create(...)` classmethod referenced in Step 6's smoke test may not exist verbatim — adapt to the real signature).

- [ ] **Step 2: Replace registry construction**

Replace the legacy `AgentRegistry()` + `registry.register("researcher", Researcher.create(...))` block with:
```python
self.agent_catalog = AgentCatalog(self._data_dir / "lapwing.db")
await self.agent_catalog.init()
self.agent_factory = AgentFactory(self.llm_router, self.tool_registry, self.mutation_log)
self.agent_policy = AgentPolicy(self.agent_catalog, llm_router=self.llm_router)
self.agent_registry = AgentRegistry(self.agent_catalog, self.agent_factory, self.agent_policy)
await self.agent_registry.init()
```

- [ ] **Step 3: Pass `agent_registry` to `StateViewBuilder`**

- [ ] **Step 4: Per-turn BudgetLedger**

In `TaskRuntime.complete_chat(...)`:
```python
ledger = BudgetLedger(
    max_llm_calls=settings.budget.max_llm_calls,
    max_tool_calls=settings.budget.max_tool_calls,
    max_total_tokens=settings.budget.max_total_tokens,
    max_wall_time_seconds=settings.budget.max_wall_time_seconds,
    max_delegation_depth=settings.budget.max_delegation_depth,
)
ctx.services["budget_ledger"] = ledger
ctx.services["agent_registry"] = self.agent_registry
```

- [ ] **Step 5: Schedule periodic session cleanup**

Hook into existing APScheduler tick (search `apscheduler\|scheduler.add_job`) every `settings.agent_team.dynamic.session_cleanup_interval_seconds` seconds → `await registry.cleanup_expired_sessions()`.

- [ ] **Step 6: Smoke test**

```python
@pytest.mark.asyncio
async def test_container_boots_with_dynamic_agents():
    app = await AppContainer.create(...)
    assert app.agent_registry is not None
    assert (await app.agent_catalog.get_by_name("researcher")) is not None
    await app.shutdown()
```

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(app): wire AgentCatalog/Factory/Policy/Registry + per-turn BudgetLedger"
```

---

## Phase 9 — Acceptance tests (T-01 … T-14)

Most acceptance criteria are covered in unit tests already added. This phase focuses on the e2e tests that require the full container.

### Task 18: E2E dynamic agent flow (T-01, T-13)

**Files:**
- Create: `tests/agents/test_e2e_dynamic.py`

- [ ] **Step 1: Write the e2e test**

```python
@pytest.mark.asyncio
async def test_t01_delegate_to_builtin(app_container):
    # call delegate_to_agent(agent_name="researcher", task="...")
    res = await app_container.tool_registry.execute(
        ToolExecutionRequest(name="delegate_to_agent",
            arguments={"agent_name":"researcher","task":"hi"}), ctx)
    assert res.success
    assert res.payload["status"] == "done"

@pytest.mark.asyncio
async def test_t13_full_create_delegate_save_destroy_audit(app_container):
    # 1. create_agent → AGENT_CREATED
    # 2. delegate_to_agent → AGENT_STARTED + AGENT_COMPLETED
    # 3. save_agent → AGENT_SAVED
    # 4. destroy_agent → AGENT_DESTROYED
    events = mutation_log.events_in_order()
    types = [e.event_type for e in events]
    assert MutationType.AGENT_CREATED in types
    assert MutationType.AGENT_STARTED in types
    assert MutationType.AGENT_COMPLETED in types
    assert MutationType.AGENT_SAVED in types
    assert MutationType.AGENT_DESTROYED in types
```

- [ ] **Step 2: Run** → `pytest tests/agents/test_e2e_dynamic.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/test_e2e_dynamic.py
git commit -m "test(agents): e2e dynamic agent create→delegate→save→destroy"
```

---

### Task 19: Acceptance matrix sweep

Run the full suite and confirm every T-XX test exists and passes.

- [ ] **Step 1: Run focused acceptance**

```bash
pytest tests/agents tests/tools tests/core/test_runtime_profiles_exclusion.py \
       tests/core/test_stateview_agent_summary.py -v
```

- [ ] **Step 2: Tick off each acceptance ID against blueprint §15**

Walk the table: T-01 … T-14, confirm a green test exists per the column "所在文件". File any gaps.

- [ ] **Step 3: Run full project test suite**

```bash
pytest -x -q
```

Expected: green. If any unrelated test fails due to legacy `delegate_to_researcher`/`coder` removal from profiles, surface to user before patching — those tests may need profile updates.

- [ ] **Step 4: Commit (if any final patches)**

```bash
git commit -am "test: complete dynamic agent acceptance matrix"
```

---

## Phase 10 — Hand-off

### Task 20: Update CHANGELOG / RESUME notes

**Files:**
- Modify: `CHANGELOG.md` (if exists) or create a short note in `docs/superpowers/plans/2026-04-28-dynamic-agent-system-NOTES.md`

- [ ] **Step 1: Note user-facing change**

> Brain can now create, delegate, save, and destroy dynamic agents at runtime. Old `delegate_to_researcher`/`delegate_to_coder` are deprecated shims; new code should use `delegate_to_agent`. Per-turn budget caps LLM/tool/token/wall-time/delegation-depth across Brain + delegated agents.

- [ ] **Step 2: Commit + push branch**

```bash
git push -u origin feat/dynamic-agent-system
```

(Do **not** open a PR — leave that to the user per CLAUDE.md.)

---

## Out of scope (per blueprint §16)

- Deleting legacy `delegate_to_researcher` / `delegate_to_coder` (kept as shims).
- AgentPool / instance pooling.
- CapabilityGrant permission model.
- Formal spec versioning beyond `version: int`.
- `delegate_many` / parallel delegation.
- DAG / workflow engine.
- Dynamically creating new RuntimeProfile or model_slot.
- Long-term memory for dynamic agents.

If any of the above feels needed mid-implementation, **stop and surface to the user** — do not silently expand scope.

---

## Dependency graph (read top→bottom)

```
Task 1 (AgentSpec)
  ├── Task 2 (LegacyAgentSpec + budget_status)
  ├── Task 3 (AgentCatalog) ──────────────────────┐
  ├── Task 4 (MutationType members)               │
  ├── Task 5 (BudgetLedger) ──────────┐           │
  └── Task 6 (AgentPolicy) ───────────┤           │
                                       │           │
Task 7 (AgentFactory) ─────────────────┤           │
Task 8 (DynamicAgent) ─────────────────┤           │
Task 9 (BaseAgent budget hooks) ───────┘           │
                                                   │
Task 10 (AgentRegistry refactor) ──────────────────┤
Task 16 (Builtin specs) ───────────────────────────┘
                                                   │
Task 11 (5 new tools) ─────────────────────────────┤
Task 12 (legacy shims) ────────────────────────────┘
                                                   │
Task 13 (RuntimeProfile updates) ──────────────────┤
Task 14 (StateView injection) ─────────────────────┤
Task 15 (config.toml) ─────────────────────────────┘
                                                   │
Task 17 (AppContainer wiring) ─────────────────────┘
                                                   │
Task 18 (E2E tests) ───────────────────────────────┤
Task 19 (acceptance sweep) ────────────────────────┘
                                                   │
Task 20 (handoff)
```

Strict order: Phases 1→2→3→4→5→6→7→8→9→10. Within phase 3 (Tasks 5+6) and phase 4 (Tasks 7+8) the listed siblings can be done in either order if a single agent owns both; subagent-driven execution should serialize them as written.

---

## Reviewer feedback addressed

Patches applied after the plan-document-reviewer pass:

1. **Task 7** — clarified `Researcher.create()` / `Coder.create()` take **no spec argument**; Factory ignores the new `AgentSpec` for builtins (catalog spec is metadata only). Removed the misleading "tiny adapter" hint. Added monkeypatch for `get_settings` in builtin tests.
2. **Task 7 Step 5** — added VitalGuard cwd-confinement regression test (no new guard code; exercises existing protection per §12).
3. **Task 8** — added parametrized T-14 test enumerating all 22 members of `DYNAMIC_AGENT_DENYLIST`.
4. **Task 9** — replaced placeholder `...` test stub with a concrete `BaseAgent + BudgetLedger` test asserting `AGENT_BUDGET_EXHAUSTED` mutation.
5. **Task 11** — disambiguated budget delegation hooks: `_run_agent` (existing helper at `agent_tools.py:81`) owns `enter_delegation` / `exit_delegation` / ephemeral `max_runs` cleanup. New executors are thin wrappers. Shim path inherits these for free.
6. **Task 13** — added Steps 4–5 to update `src/core/authority_gate.py` (auth levels for the 5 new tools) plus a regression test, since `delegate_to_*` shim pattern depends on auth-gate entries.
7. **Task 14** — pinned the `_render_runtime_state` insertion point (with grep confirmation step) instead of assuming the function name.
8. **Task 15** — pinned absolute config path `/home/kevin/lapwing/config.toml` (no `config/` directory exists).
9. **Task 17** — added a grep step to discover the actual bootstrap method name (no assumption that `AppContainer.create(...)` exists).
