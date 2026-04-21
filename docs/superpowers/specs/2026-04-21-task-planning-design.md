# Task Planning System Design

> Lapwing tool loop planning layer — plan_task / update_plan tools, per-round
> injection, tell_user soft gate.

## Problem

Lapwing's tool loop is pure ReAct: the model sees context, picks a tool, sees the
result, repeats. For multi-step tasks ("查天气，写进日记，提醒我带伞") the model
often completes step 1, calls `tell_user`, and forgets the rest. As the context
fills with tool output, the original request gets buried.

## Design Goals

1. Model decides whether to plan (via tool_choice=auto). No harness heuristics.
2. Plan is visible to the model on every tool loop round.
3. Structural constraint: `tell_user` warns when steps remain incomplete (soft gate).
4. Pure chat, inner tick, and agent delegation paths are unaffected.

## Decision: Per-Round Injection (Approach B)

Plan state is injected into the system message before each LLM call in `_run_step()`,
using the same pattern as `_with_shell_state_context()`. This guarantees the plan is
always visible regardless of how many tool calls occur between plan updates.

Alternatives considered:

- **Tool return values + staleness reminder (A)**: Plan drifts back in context between
  updates; staleness detection adds complexity.
- **Dual injection + rich returns (C)**: Redundant — tool returns would show the plan
  twice when the model just called a plan tool.

Approach B eliminates the failure mode structurally. Token cost is ~50-100 per round
when a plan exists, zero otherwise.

---

## 1. Plan Data Model

File: `src/core/plan_state.py`

### PlanStep

```python
@dataclass
class PlanStep:
    index: int
    description: str
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"
    note: str = ""
```

### PlanState

```python
@dataclass
class PlanState:
    steps: list[PlanStep]
    created_at: float
    soft_gate_armed: bool = True
```

Methods:

- `has_incomplete() -> bool` — True if any step is pending or in_progress.
- `current_step() -> PlanStep | None` — First step with status in_progress.
- `advance(step_index, status, note="") -> PlanStep` — Set step status, with
  auto-advance and transition validation (see Transition Rules below).
- `render() -> str` — Full plan rendering with status markers.
- `render_incomplete() -> str` — Only pending/in_progress steps.
- `check_soft_gate() -> str | None` — If has_incomplete() and gate is armed, disarm
  gate and return warning text. Otherwise return None.

### Transition Rules

Legal transitions for `advance()`:

| From | To | Allowed? | Notes |
|------|----|----------|-------|
| in_progress | completed | Yes | Normal completion. Auto-advances next pending → in_progress. |
| in_progress | blocked | Yes | Hit external dependency. Auto-advances next pending → in_progress. |
| pending | blocked | Yes | Model foresees a blocker on a future step. Auto-advances next pending → in_progress if the blocked step was about to be reached. |
| completed | any | No | Completed is terminal. |
| blocked | any | No | Blocked is terminal. |
| pending | completed | No | Must go through in_progress first. |

Auto-advance rule: after any successful `advance()` call, if no step is currently
in_progress, scan forward from the updated step and set the first pending step to
in_progress.

### Lifecycle

- Created by `plan_task` tool, stored in `context.services["plan_state"]`.
- Lives for the duration of one `complete_chat()` call.
- Garbage collected when the services dict goes out of scope.
- No persistence, no per-chat_id registry, no brain-level management.

### Rendering Format

```
## 当前计划

[✓] 查询明天天气
[→] 判断是否需要带伞并告诉用户  ← 当前
[ ] 将天气信息写入日记
[✗] 某步骤被阻塞（原因：等待用户确认）
```

Symbols: `✓` completed, `→` in_progress, ` ` (space) pending, `✗` blocked.
The `← 当前` marker on the in_progress step gives the model an unambiguous anchor.

---

## 2. Tool Definitions

File: `src/tools/plan_tools.py`

### plan_task

**Description** (Chinese, consistent with all other Lapwing tools):
> 当用户请求包含多个需要分步完成的子任务时，先用此工具制定计划再逐步执行。
> 简单的单步请求不需要计划。

**Schema**:

```json
{
  "type": "object",
  "properties": {
    "steps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "description": { "type": "string" }
        },
        "required": ["description"]
      },
      "description": "计划的步骤列表，按执行顺序排列",
      "minItems": 2
    }
  },
  "required": ["steps"]
}
```

**Handler logic**:

1. Reject if a plan already exists in services (one plan per tool loop).
2. Create PlanState with step 0 set to in_progress.
3. Store in `context.services["plan_state"]`.
4. Return short confirmation: `"计划已创建，共 N 步。当前执行：步骤 1。"`

`minItems: 2` ensures single-step tasks never create a plan — enforced at schema
level, not prompt level.

### update_plan

**Description**:
> 更新计划中某个步骤的状态。完成当前步骤后调用此工具标记为 completed，
> 下一步会自动变为 in_progress。

**Schema**:

```json
{
  "type": "object",
  "properties": {
    "step_index": {
      "type": "integer",
      "description": "步骤编号（从 0 开始）"
    },
    "status": {
      "type": "string",
      "enum": ["completed", "blocked"],
      "description": "新状态"
    },
    "note": {
      "type": "string",
      "description": "可选备注（如阻塞原因）"
    }
  },
  "required": ["step_index", "status"]
}
```

**Handler logic**:

1. Get plan from `context.services.get("plan_state")`, fail if None.
2. Validate step_index in range. Validate transition (cannot complete a blocked step,
   cannot go backwards from completed).
3. Call `plan.advance(step_index, status, note)`.
4. Return confirmation: `"步骤 {i} 已完成。当前执行：步骤 {j}。"` or
   `"所有步骤已完成。"` if done.

Status enum is `["completed", "blocked"]` only — in_progress is set by auto-advance,
pending is the initial state. Neither requires manual model intervention.

### Registration

Both tools registered in `build_default_tool_registry()` in `src/tools/registry.py`:

- `capability="general"`, `risk_level="low"`, `visibility="model"`.
- No auth gating (no entry in OPERATION_AUTH).
- Depth-0 tools: visible to Lapwing directly, not TeamLead-specific.

### chat_tools() Whitelist

`TaskRuntime.chat_tools()` maintains an explicit `tool_names` set — tools not in
this set are invisible to the LLM regardless of registration. Plan tools must be
added using the same conditional pattern as the promise tools:

```python
for plan_tool in ("plan_task", "update_plan"):
    if self._tool_registry.get(plan_tool) is not None:
        tool_names.add(plan_tool)
```

Inner tick isolation: `think_inner` shares `_complete_chat()` which calls
`chat_tools()`, so the inner tick LLM will see plan tools in its schema. This
is acceptable — the inner tick prompt doesn't ask for multi-step task execution,
and if the model somehow calls `plan_task` during inner tick, the plan is local
to that `complete_chat()` call and gets discarded. No special gating needed.
The `tell_user` soft gate is also moot for inner tick because `send_fn=None`
(tell_user already fails before reaching the gate check).

---

## 3. Per-Round Injection

File modified: `src/core/task_runtime.py`

### New method

```python
def _with_plan_context(
    self,
    messages: list[dict[str, Any]],
    services: dict[str, Any] | None,
) -> list[dict[str, Any]]:
```

Logic: if services has a "plan_state" key with a non-None PlanState, call
`plan.render()` and append to the system message. Same merge pattern as
`_with_shell_state_context()`.

### Call site

In `_run_step()`, chained after the existing injection:

```python
messages = self._with_shell_state_context(ctx.messages, ctx.state)
messages = self._with_plan_context(messages, ctx.services)
response = await self._router.complete_with_tools(messages, ...)
```

### Overhead

- No plan: early return, zero cost.
- Active plan: ~50-100 tokens appended to system message per round.
- Plan rendering is a pure string format — no I/O, no async.

---

## 4. tell_user Soft Gate

File modified: `src/tools/tell_user.py`

### Mechanism

Before `await context.send_fn(text)`, check for active plan:

```python
plan = (context.services or {}).get("plan_state")
if plan is not None:
    warning = plan.check_soft_gate()
    if warning is not None:
        return ToolExecutionResult(
            success=False,
            payload={"delivered": False, "reason": "plan_incomplete"},
            reason=warning,
        )
```

### check_soft_gate() behavior

- If `has_incomplete()` is True and `soft_gate_armed` is True:
  set `soft_gate_armed = False`, return warning text. Otherwise return None.

Warning text format:

```
当前计划中还有未完成的步骤，请先完成再回复用户：
[→] 判断是否需要带伞  ← 当前
[ ] 将天气信息写入日记
如果确实需要先告诉用户中间结果，再次调用 tell_user 即可。
```

The warning includes the incomplete step list (same symbols as `render()`) and an
explicit instruction that retrying tell_user will succeed — this helps the model
make an informed decision rather than getting stuck.

### "Fire once then disarm" rationale

The gate is a speed bump, not a wall. After the model sees the warning:

- **Common case**: model continues executing remaining steps, then calls tell_user
  when all steps are done → gate doesn't fire (has_incomplete is False).
- **Legitimate mid-plan speech**: model calls tell_user again after seeing warning →
  gate is disarmed, message delivers. The model may have a good reason (partial
  result the user needs immediately).
- **No infinite loop risk**: the gate fires at most once per plan lifetime.

If M2.7 frequently ignores the warning and retries immediately, the gate can be
tightened later (e.g., armed per-phase rather than once-per-plan).

---

## 5. File Layout

### New files

| File | Purpose |
|------|---------|
| `src/core/plan_state.py` | PlanState + PlanStep dataclasses |
| `src/tools/plan_tools.py` | plan_task + update_plan executors |
| `tests/core/test_plan_state.py` | PlanState unit tests |
| `tests/tools/test_plan_tools.py` | Tool handler tests |
| `tests/tools/test_tell_user_plan_gate.py` | Soft gate tests |
| `tests/core/test_plan_injection.py` | Per-round injection integration tests |

### Modified files

| File | Change |
|------|--------|
| `src/tools/registry.py` | Register plan_task + update_plan |
| `src/tools/tell_user.py` | Add soft gate check (~6 lines) |
| `src/core/task_runtime.py` | Add `_with_plan_context()` + one call in `_run_step()`, add plan tools to `chat_tools()` whitelist |

### Not modified

- `src/core/brain.py` — services dict is already mutable; plan_task writes to it.
  `_complete_chat` assembles services and calls `chat_tools()` — no new wiring needed
  at the brain level.
- `src/core/state_view_builder.py`, `state_serializer.py` — plan injection is in the
  tool loop, not initial prompt assembly.
- `src/core/main_loop.py`, `inner_tick_scheduler.py` — inner tick shares `chat_tools()`
  and will see plan tools, but this is harmless (see chat_tools section above).
- Agent team files — untouched.

---

## 6. Testing

### test_plan_state.py

- Create plan: step 0 is in_progress, rest pending.
- advance() completes step → next auto-advances to in_progress.
- advance() last step → has_incomplete() returns False.
- advance() blocked with note → next pending auto-advances.
- advance() validation: out-of-range index, invalid transition.
- render(): correct symbols and ← 当前 marker.
- check_soft_gate(): first call returns warning, second returns None.
- check_soft_gate() all completed: returns None immediately.

### test_plan_tools.py

- plan_task creates PlanState in services, returns confirmation.
- plan_task rejects duplicate plan creation.
- update_plan completes step, auto-advances next.
- update_plan fails when no plan exists.
- update_plan rejects invalid index and transition.

### test_plan_injection.py

- `_with_plan_context` with no plan in services: messages returned unchanged.
- `_with_plan_context` with active plan + existing system message: plan appended.
- `_with_plan_context` with active plan + no system message: system message created.
- Chaining: `_with_shell_state_context` then `_with_plan_context` both inject content.

### test_tell_user_plan_gate.py

- tell_user with no plan: delivers normally.
- tell_user with incomplete plan (gate armed): returns failure.
- tell_user after gate fired (gate disarmed): delivers normally.
- tell_user with all steps completed: delivers normally.

### Regression

Existing tests for task_runtime, main_loop, tell_user should pass unchanged.

---

## Out of Scope

- task_learning integration (plan data can be written to trajectory later).
- TeamLead-level planning.
- Plan persistence across messages or to SQLite.
- Desktop frontend visualization.
