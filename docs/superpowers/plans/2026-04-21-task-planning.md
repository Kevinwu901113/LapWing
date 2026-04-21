# Task Planning System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add plan_task / update_plan tools to Lapwing's tool loop so the model can create structured multi-step plans, with per-round visibility injection and a tell_user soft gate.

**Architecture:** PlanState is a local object scoped to one `complete_chat()` call. Tools read/write it via `context.services["plan_state"]`. Before each LLM call, `_with_plan_context()` injects the plan rendering into the system message. tell_user checks `PlanState.check_soft_gate()` and returns failure once if steps remain.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, dataclasses

**Spec:** `docs/superpowers/specs/2026-04-21-task-planning-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/core/plan_state.py` (create) | PlanStep + PlanState dataclasses: state management, transition validation, rendering, soft gate |
| `src/tools/plan_tools.py` (create) | plan_task + update_plan tool executors, descriptions, schemas |
| `src/tools/registry.py` (modify ~10 lines) | Register plan_task + update_plan in `build_default_tool_registry()` |
| `src/core/task_runtime.py` (modify ~20 lines) | `_with_plan_context()` method + call in `_run_step()`, plan tools in `chat_tools()` |
| `src/tools/tell_user.py` (modify ~6 lines) | Soft gate check before `send_fn` call |
| `tests/core/test_plan_state.py` (create) | PlanState unit tests |
| `tests/tools/test_plan_tools.py` (create) | Tool handler tests |
| `tests/tools/test_tell_user_plan_gate.py` (create) | Soft gate tests |
| `tests/core/test_plan_injection.py` (create) | `_with_plan_context` integration tests |

---

## Task 1: PlanState Data Model

**Files:**
- Create: `src/core/plan_state.py`
- Test: `tests/core/test_plan_state.py`

- [ ] **Step 1: Write PlanStep and PlanState creation tests**

```python
# tests/core/test_plan_state.py
"""PlanState + PlanStep 单元测试。"""
from __future__ import annotations

import time

import pytest

from src.core.plan_state import PlanState, PlanStep, PlanTransitionError


class TestPlanCreation:
    def test_create_sets_first_step_in_progress(self):
        plan = PlanState.create([
            {"description": "查天气"},
            {"description": "写日记"},
        ])
        assert plan.steps[0].status == "in_progress"
        assert plan.steps[1].status == "pending"
        assert plan.steps[0].index == 0
        assert plan.steps[1].index == 1
        assert plan.created_at <= time.time()

    def test_create_requires_at_least_two_steps(self):
        with pytest.raises(ValueError, match="至少需要 2 个步骤"):
            PlanState.create([{"description": "only one"}])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_plan_state.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.core.plan_state'`

- [ ] **Step 3: Implement PlanStep, PlanState, and PlanState.create()**

```python
# src/core/plan_state.py
"""任务规划状态——tool loop 内的多步骤计划管理。

PlanState 的生命周期与一次 complete_chat() 调用绑定，不跨会话持久化。
通过 context.services["plan_state"] 注入到工具执行上下文。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

StepStatus = Literal["pending", "in_progress", "completed", "blocked"]


class PlanTransitionError(ValueError):
    pass


@dataclass
class PlanStep:
    index: int
    description: str
    status: StepStatus = "pending"
    note: str = ""


@dataclass
class PlanState:
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = 0.0
    soft_gate_armed: bool = True

    @classmethod
    def create(cls, step_dicts: list[dict]) -> PlanState:
        if len(step_dicts) < 2:
            raise ValueError("至少需要 2 个步骤")
        steps = [
            PlanStep(index=i, description=d["description"])
            for i, d in enumerate(step_dicts)
        ]
        steps[0].status = "in_progress"
        return cls(steps=steps, created_at=time.time())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_plan_state.py -x -q`
Expected: 2 passed

- [ ] **Step 5: Write advance() tests — happy path**

Add to `tests/core/test_plan_state.py`:

```python
class TestAdvance:
    def test_complete_in_progress_advances_next(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
            {"description": "步骤3"},
        ])
        step = plan.advance(0, "completed")
        assert step.status == "completed"
        assert plan.steps[1].status == "in_progress"
        assert plan.steps[2].status == "pending"

    def test_complete_last_step(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "completed")
        assert plan.has_incomplete() is False

    def test_block_in_progress_advances_next(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
            {"description": "步骤3"},
        ])
        step = plan.advance(0, "blocked", note="需要用户确认")
        assert step.status == "blocked"
        assert step.note == "需要用户确认"
        assert plan.steps[1].status == "in_progress"

    def test_block_pending_step(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
            {"description": "步骤3"},
        ])
        plan.advance(2, "blocked", note="提前发现问题")
        assert plan.steps[2].status == "blocked"
        # step 0 still in_progress, step 2 blocked doesn't change current
        assert plan.steps[0].status == "in_progress"

    def test_auto_advance_skips_blocked(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
            {"description": "步骤3"},
        ])
        plan.advance(1, "blocked")
        plan.advance(0, "completed")
        # step 1 is blocked, should skip to step 2
        assert plan.steps[2].status == "in_progress"

    def test_all_remaining_blocked_no_advance(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(1, "blocked")
        plan.advance(0, "completed")
        # step 1 blocked, no pending left — no in_progress step
        assert plan.current_step() is None
        assert plan.has_incomplete() is False
```

- [ ] **Step 6: Write advance() tests — error cases**

Add to `tests/core/test_plan_state.py`:

```python
    def test_reject_out_of_range_index(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        with pytest.raises(PlanTransitionError, match="超出范围"):
            plan.advance(5, "completed")

    def test_reject_complete_blocked_step(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(0, "blocked")
        with pytest.raises(PlanTransitionError, match="终态"):
            plan.advance(0, "completed")

    def test_reject_change_completed_step(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(0, "completed")
        with pytest.raises(PlanTransitionError, match="终态"):
            plan.advance(0, "blocked")

    def test_reject_complete_pending_step(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
            {"description": "步骤3"},
        ])
        with pytest.raises(PlanTransitionError, match="不能直接完成"):
            plan.advance(1, "completed")
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_plan_state.py -x -q`
Expected: FAIL — `AttributeError: 'PlanState' object has no attribute 'advance'`

- [ ] **Step 8: Implement advance(), has_incomplete(), current_step()**

Add to `src/core/plan_state.py` inside the `PlanState` class:

```python
    def has_incomplete(self) -> bool:
        return any(s.status in ("pending", "in_progress") for s in self.steps)

    def current_step(self) -> PlanStep | None:
        for s in self.steps:
            if s.status == "in_progress":
                return s
        return None

    def advance(self, step_index: int, status: str, note: str = "") -> PlanStep:
        if step_index < 0 or step_index >= len(self.steps):
            raise PlanTransitionError(
                f"步骤索引 {step_index} 超出范围 [0, {len(self.steps) - 1}]"
            )
        step = self.steps[step_index]
        if step.status in ("completed", "blocked"):
            raise PlanTransitionError(
                f"步骤 {step_index} 已是终态 ({step.status})，不可更改"
            )
        if status == "completed" and step.status == "pending":
            raise PlanTransitionError(
                f"步骤 {step_index} 状态为 pending，不能直接完成（需先变为 in_progress）"
            )

        step.status = status
        step.note = note

        self._auto_advance()
        return step

    def _auto_advance(self) -> None:
        if self.current_step() is not None:
            return
        for s in self.steps:
            if s.status == "pending":
                s.status = "in_progress"
                return
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_plan_state.py -x -q`
Expected: All passed

- [ ] **Step 10: Write render() tests**

Add to `tests/core/test_plan_state.py`:

```python
class TestRender:
    def test_render_initial(self):
        plan = PlanState.create([
            {"description": "查天气"},
            {"description": "写日记"},
        ])
        rendered = plan.render()
        assert "## 当前计划" in rendered
        assert "[→] 查天气  ← 当前" in rendered
        assert "[ ] 写日记" in rendered

    def test_render_mid_execution(self):
        plan = PlanState.create([
            {"description": "查天气"},
            {"description": "判断带伞"},
            {"description": "写日记"},
        ])
        plan.advance(0, "completed")
        rendered = plan.render()
        assert "[✓] 查天气" in rendered
        assert "[→] 判断带伞  ← 当前" in rendered
        assert "[ ] 写日记" in rendered

    def test_render_blocked_with_note(self):
        plan = PlanState.create([
            {"description": "查天气"},
            {"description": "等用户确认"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "blocked", note="需要用户输入")
        rendered = plan.render()
        assert "[✗] 等用户确认（需要用户输入）" in rendered

    def test_render_all_completed(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "completed")
        rendered = plan.render()
        assert "[✓] 步骤1" in rendered
        assert "[✓] 步骤2" in rendered
        assert "← 当前" not in rendered
```

- [ ] **Step 11: Write render_incomplete() test**

Add to `tests/core/test_plan_state.py`:

```python
    def test_render_incomplete(self):
        plan = PlanState.create([
            {"description": "查天气"},
            {"description": "判断带伞"},
            {"description": "写日记"},
        ])
        plan.advance(0, "completed")
        incomplete = plan.render_incomplete()
        assert "查天气" not in incomplete
        assert "[→] 判断带伞  ← 当前" in incomplete
        assert "[ ] 写日记" in incomplete
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_plan_state.py::TestRender -x -q`
Expected: FAIL — `AttributeError: 'PlanState' object has no attribute 'render'`

- [ ] **Step 13: Implement render() and render_incomplete()**

Add to `src/core/plan_state.py` inside the `PlanState` class:

```python
    def render(self) -> str:
        lines = ["## 当前计划\n"]
        for s in self.steps:
            lines.append(self._render_step(s))
        return "\n".join(lines)

    def render_incomplete(self) -> str:
        lines: list[str] = []
        for s in self.steps:
            if s.status in ("pending", "in_progress"):
                lines.append(self._render_step(s))
        return "\n".join(lines)

    @staticmethod
    def _render_step(step: PlanStep) -> str:
        symbols = {
            "completed": "✓",
            "in_progress": "→",
            "pending": " ",
            "blocked": "✗",
        }
        symbol = symbols[step.status]
        line = f"[{symbol}] {step.description}"
        if step.status == "blocked" and step.note:
            line += f"（{step.note}）"
        if step.status == "in_progress":
            line += "  ← 当前"
        return line
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_plan_state.py -x -q`
Expected: All passed

- [ ] **Step 15: Write check_soft_gate() tests**

Add to `tests/core/test_plan_state.py`:

```python
class TestSoftGate:
    def test_fires_once_then_disarms(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        warning = plan.check_soft_gate()
        assert warning is not None
        assert "未完成" in warning
        assert "步骤2" in warning
        assert "再次调用 tell_user" in warning

        second = plan.check_soft_gate()
        assert second is None

    def test_no_warning_when_all_completed(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "completed")
        assert plan.check_soft_gate() is None

    def test_no_warning_when_all_blocked_or_completed(self):
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "blocked")
        assert plan.check_soft_gate() is None
```

- [ ] **Step 16: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_plan_state.py::TestSoftGate -x -q`
Expected: FAIL — `AttributeError: 'PlanState' object has no attribute 'check_soft_gate'`

- [ ] **Step 17: Implement check_soft_gate()**

Add to `src/core/plan_state.py` inside the `PlanState` class:

```python
    def check_soft_gate(self) -> str | None:
        if not self.has_incomplete() or not self.soft_gate_armed:
            return None
        self.soft_gate_armed = False
        incomplete = self.render_incomplete()
        return (
            f"当前计划中还有未完成的步骤，请先完成再回复用户：\n"
            f"{incomplete}\n"
            f"如果确实需要先告诉用户中间结果，再次调用 tell_user 即可。"
        )
```

- [ ] **Step 18: Run all PlanState tests**

Run: `python -m pytest tests/core/test_plan_state.py -x -q`
Expected: All passed

- [ ] **Step 19: Commit**

```bash
git add src/core/plan_state.py tests/core/test_plan_state.py
git commit -m "feat(plan): add PlanState data model with transitions, rendering, and soft gate"
```

---

## Task 2: Plan Tool Executors

**Files:**
- Create: `src/tools/plan_tools.py`
- Test: `tests/tools/test_plan_tools.py`

- [ ] **Step 1: Write plan_task executor tests**

```python
# tests/tools/test_plan_tools.py
"""plan_task / update_plan 工具测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.plan_state import PlanState
from src.tools.plan_tools import plan_task_executor, update_plan_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(*, services: dict | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services if services is not None else {},
        chat_id="chat-x",
    )


@pytest.mark.asyncio
class TestPlanTask:
    async def test_creates_plan_in_services(self):
        ctx = _make_ctx()
        result = await plan_task_executor(
            ToolExecutionRequest(
                name="plan_task",
                arguments={"steps": [
                    {"description": "查天气"},
                    {"description": "写日记"},
                ]},
            ),
            ctx,
        )
        assert result.success is True
        plan = ctx.services.get("plan_state")
        assert isinstance(plan, PlanState)
        assert len(plan.steps) == 2
        assert plan.steps[0].status == "in_progress"
        assert "共 2 步" in result.payload.get("message", "")

    async def test_rejects_duplicate_plan(self):
        ctx = _make_ctx()
        await plan_task_executor(
            ToolExecutionRequest(
                name="plan_task",
                arguments={"steps": [
                    {"description": "a"},
                    {"description": "b"},
                ]},
            ),
            ctx,
        )
        result = await plan_task_executor(
            ToolExecutionRequest(
                name="plan_task",
                arguments={"steps": [
                    {"description": "c"},
                    {"description": "d"},
                ]},
            ),
            ctx,
        )
        assert result.success is False
        assert "已存在" in result.reason

    async def test_rejects_single_step(self):
        ctx = _make_ctx()
        result = await plan_task_executor(
            ToolExecutionRequest(
                name="plan_task",
                arguments={"steps": [{"description": "only one"}]},
            ),
            ctx,
        )
        assert result.success is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_plan_tools.py::TestPlanTask -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tools.plan_tools'`

- [ ] **Step 3: Implement plan_task executor**

```python
# src/tools/plan_tools.py
"""plan_task / update_plan — 任务规划工具。

给 Lapwing 的 tool loop 提供结构化的多步骤规划能力。PlanState 通过
context.services["plan_state"] 注入，生命周期与单次 complete_chat() 绑定。
"""
from __future__ import annotations

import logging

from src.core.plan_state import PlanState, PlanTransitionError
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)

logger = logging.getLogger("lapwing.tools.plan")


PLAN_TASK_DESCRIPTION = (
    "当用户请求包含多个需要分步完成的子任务时，先用此工具制定计划再逐步执行。"
    "简单的单步请求不需要计划。"
)

PLAN_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                },
                "required": ["description"],
            },
            "description": "计划的步骤列表，按执行顺序排列",
            "minItems": 2,
        },
    },
    "required": ["steps"],
}

UPDATE_PLAN_DESCRIPTION = (
    "更新计划中某个步骤的状态。完成当前步骤后调用此工具标记为 completed，"
    "下一步会自动变为 in_progress。"
)

UPDATE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "step_index": {
            "type": "integer",
            "description": "步骤编号（从 0 开始）",
        },
        "status": {
            "type": "string",
            "enum": ["completed", "blocked"],
            "description": "新状态",
        },
        "note": {
            "type": "string",
            "description": "可选备注（如阻塞原因）",
        },
    },
    "required": ["step_index", "status"],
}


async def plan_task_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}

    if services.get("plan_state") is not None:
        return ToolExecutionResult(
            success=False,
            payload={},
            reason="当前任务已存在计划，不能重复创建。",
        )

    step_dicts = request.arguments.get("steps", [])
    if not isinstance(step_dicts, list) or len(step_dicts) < 2:
        return ToolExecutionResult(
            success=False,
            payload={},
            reason="计划至少需要 2 个步骤。",
        )

    try:
        plan = PlanState.create(step_dicts)
    except (ValueError, KeyError) as exc:
        return ToolExecutionResult(
            success=False,
            payload={},
            reason=f"创建计划失败：{exc}",
        )

    services["plan_state"] = plan
    n = len(plan.steps)
    return ToolExecutionResult(
        success=True,
        payload={"message": f"计划已创建，共 {n} 步。当前执行：步骤 1。"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/tools/test_plan_tools.py::TestPlanTask -x -q`
Expected: All passed

- [ ] **Step 5: Write update_plan executor tests**

Add to `tests/tools/test_plan_tools.py`:

```python
@pytest.mark.asyncio
class TestUpdatePlan:
    async def test_completes_step_and_advances(self):
        ctx = _make_ctx()
        await plan_task_executor(
            ToolExecutionRequest(
                name="plan_task",
                arguments={"steps": [
                    {"description": "步骤1"},
                    {"description": "步骤2"},
                    {"description": "步骤3"},
                ]},
            ),
            ctx,
        )
        result = await update_plan_executor(
            ToolExecutionRequest(
                name="update_plan",
                arguments={"step_index": 0, "status": "completed"},
            ),
            ctx,
        )
        assert result.success is True
        plan = ctx.services["plan_state"]
        assert plan.steps[0].status == "completed"
        assert plan.steps[1].status == "in_progress"

    async def test_reports_all_done(self):
        ctx = _make_ctx()
        await plan_task_executor(
            ToolExecutionRequest(
                name="plan_task",
                arguments={"steps": [
                    {"description": "步骤1"},
                    {"description": "步骤2"},
                ]},
            ),
            ctx,
        )
        await update_plan_executor(
            ToolExecutionRequest(
                name="update_plan",
                arguments={"step_index": 0, "status": "completed"},
            ),
            ctx,
        )
        result = await update_plan_executor(
            ToolExecutionRequest(
                name="update_plan",
                arguments={"step_index": 1, "status": "completed"},
            ),
            ctx,
        )
        assert result.success is True
        assert "所有步骤已完成" in result.payload.get("message", "")

    async def test_fails_when_no_plan(self):
        ctx = _make_ctx()
        result = await update_plan_executor(
            ToolExecutionRequest(
                name="update_plan",
                arguments={"step_index": 0, "status": "completed"},
            ),
            ctx,
        )
        assert result.success is False
        assert "没有活跃的计划" in result.reason

    async def test_rejects_invalid_transition(self):
        ctx = _make_ctx()
        await plan_task_executor(
            ToolExecutionRequest(
                name="plan_task",
                arguments={"steps": [
                    {"description": "步骤1"},
                    {"description": "步骤2"},
                ]},
            ),
            ctx,
        )
        result = await update_plan_executor(
            ToolExecutionRequest(
                name="update_plan",
                arguments={"step_index": 1, "status": "completed"},
            ),
            ctx,
        )
        assert result.success is False
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_plan_tools.py::TestUpdatePlan -x -q`
Expected: FAIL — `cannot import name 'update_plan_executor'`

- [ ] **Step 7: Implement update_plan executor**

Add to `src/tools/plan_tools.py`:

```python
async def update_plan_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    services = context.services or {}
    plan = services.get("plan_state")

    if not isinstance(plan, PlanState):
        return ToolExecutionResult(
            success=False,
            payload={},
            reason="没有活跃的计划。请先调用 plan_task 创建计划。",
        )

    step_index = request.arguments.get("step_index")
    status = request.arguments.get("status")
    note = request.arguments.get("note", "")

    if step_index is None or status is None:
        return ToolExecutionResult(
            success=False,
            payload={},
            reason="缺少必要参数 step_index 或 status。",
        )

    try:
        plan.advance(int(step_index), str(status), note=str(note))
    except PlanTransitionError as exc:
        return ToolExecutionResult(
            success=False,
            payload={},
            reason=str(exc),
        )

    status_zh = {"completed": "完成", "blocked": "标记为阻塞"}
    label = status_zh.get(status, status)

    current = plan.current_step()
    if current is not None:
        msg = f"步骤 {step_index + 1} 已{label}。当前执行：步骤 {current.index + 1}。"
    elif plan.has_incomplete():
        msg = f"步骤 {step_index + 1} 已{label}。剩余步骤均被阻塞。"
    else:
        msg = "所有步骤已完成。"

    return ToolExecutionResult(
        success=True,
        payload={"message": msg},
    )
```

- [ ] **Step 8: Run all tool tests**

Run: `python -m pytest tests/tools/test_plan_tools.py -x -q`
Expected: All passed

- [ ] **Step 9: Commit**

```bash
git add src/tools/plan_tools.py tests/tools/test_plan_tools.py
git commit -m "feat(plan): add plan_task and update_plan tool executors"
```

---

## Task 3: Tool Registration + chat_tools() Whitelist

**Files:**
- Modify: `src/tools/registry.py:28-33` (imports), `src/tools/registry.py:412` (registration, before `return registry`)
- Modify: `src/core/task_runtime.py:338-355` (chat_tools whitelist)

- [ ] **Step 1: Register plan tools in build_default_tool_registry()**

Add import at top of `src/tools/registry.py` (after the commitments import block):

```python
from src.tools.plan_tools import (
    PLAN_TASK_DESCRIPTION,
    PLAN_TASK_SCHEMA,
    UPDATE_PLAN_DESCRIPTION,
    UPDATE_PLAN_SCHEMA,
    plan_task_executor,
    update_plan_executor,
)
```

Add registration before `return registry` (after the verify_workspace block, before the comment):

```python
    # 任务规划工具——模型可选使用的多步骤计划
    registry.register(
        ToolSpec(
            name="plan_task",
            description=PLAN_TASK_DESCRIPTION,
            json_schema=PLAN_TASK_SCHEMA,
            executor=plan_task_executor,
            capability="general",
            risk_level="low",
        )
    )
    registry.register(
        ToolSpec(
            name="update_plan",
            description=UPDATE_PLAN_DESCRIPTION,
            json_schema=UPDATE_PLAN_SCHEMA,
            executor=update_plan_executor,
            capability="general",
            risk_level="low",
        )
    )
```

- [ ] **Step 2: Add plan tools to chat_tools() whitelist**

In `src/core/task_runtime.py`, in `chat_tools()`, add after the promise tools block (after line 343):

```python
        for plan_tool in ("plan_task", "update_plan"):
            if self._tool_registry.get(plan_tool) is not None:
                tool_names.add(plan_tool)
```

- [ ] **Step 3: Verify registry imports work**

Run: `python -c "from src.tools.registry import build_default_tool_registry; r = build_default_tool_registry(); print('plan_task' in [t.name for t in r._tools.values()])"``
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add src/tools/registry.py src/core/task_runtime.py
git commit -m "feat(plan): register plan tools and add to chat_tools whitelist"
```

---

## Task 4: Per-Round Plan Injection

**Files:**
- Modify: `src/core/task_runtime.py:658-659` (call site), `src/core/task_runtime.py:1269` (new method after `_with_shell_state_context`)
- Test: `tests/core/test_plan_injection.py`

- [ ] **Step 1: Write injection tests**

```python
# tests/core/test_plan_injection.py
"""_with_plan_context 注入测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.plan_state import PlanState
from src.core.task_runtime import TaskRuntime


def _make_runtime() -> TaskRuntime:
    router = MagicMock()
    registry = MagicMock()
    return TaskRuntime(router=router, tool_registry=registry)


class TestWithPlanContext:
    def test_no_plan_returns_unchanged(self):
        rt = _make_runtime()
        msgs = [{"role": "system", "content": "hello"}]
        result = rt._with_plan_context(msgs, {})
        assert result is msgs

    def test_no_plan_returns_unchanged_none_services(self):
        rt = _make_runtime()
        msgs = [{"role": "system", "content": "hello"}]
        result = rt._with_plan_context(msgs, None)
        assert result is msgs

    def test_appends_to_existing_system_message(self):
        rt = _make_runtime()
        plan = PlanState.create([
            {"description": "查天气"},
            {"description": "写日记"},
        ])
        msgs = [{"role": "system", "content": "你是 Lapwing"}]
        result = rt._with_plan_context(msgs, {"plan_state": plan})
        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert "你是 Lapwing" in result[0]["content"]
        assert "## 当前计划" in result[0]["content"]
        assert "[→] 查天气" in result[0]["content"]

    def test_creates_system_message_when_none_exists(self):
        rt = _make_runtime()
        plan = PlanState.create([
            {"description": "查天气"},
            {"description": "写日记"},
        ])
        msgs = [{"role": "user", "content": "帮我查天气"}]
        result = rt._with_plan_context(msgs, {"plan_state": plan})
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "## 当前计划" in result[0]["content"]
        assert result[1]["role"] == "user"

    def test_does_not_mutate_original_messages(self):
        rt = _make_runtime()
        plan = PlanState.create([
            {"description": "a"},
            {"description": "b"},
        ])
        original = [{"role": "system", "content": "base"}]
        result = rt._with_plan_context(original, {"plan_state": plan})
        assert original[0]["content"] == "base"
        assert result is not original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_plan_injection.py -x -q`
Expected: FAIL — `AttributeError: 'TaskRuntime' object has no attribute '_with_plan_context'`

- [ ] **Step 3: Implement _with_plan_context()**

Add to `src/core/task_runtime.py`, immediately after `_with_shell_state_context()` (after line 1269):

```python
    def _with_plan_context(
        self,
        messages: list[dict[str, Any]],
        services: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not services:
            return messages
        plan = services.get("plan_state")
        if plan is None:
            return messages
        plan_text = plan.render()
        if not plan_text:
            return messages

        if messages and messages[0].get("role") == "system":
            merged_system = dict(messages[0])
            base_content = str(merged_system.get("content", "")).strip()
            merged_system["content"] = (
                f"{base_content}\n\n{plan_text}" if base_content else plan_text
            )
            return [merged_system, *messages[1:]]
        return [{"role": "system", "content": plan_text}, *messages]
```

- [ ] **Step 4: Wire into _run_step() call site**

In `src/core/task_runtime.py`, change the LLM call at line 658-659 from:

```python
            turn = await self._router.complete_with_tools(
                self._with_shell_state_context(ctx.messages, ctx.state),
```

To:

```python
            messages_with_state = self._with_shell_state_context(ctx.messages, ctx.state)
            messages_with_state = self._with_plan_context(messages_with_state, ctx.services)
            turn = await self._router.complete_with_tools(
                messages_with_state,
```

- [ ] **Step 5: Run injection tests**

Run: `python -m pytest tests/core/test_plan_injection.py -x -q`
Expected: All passed

- [ ] **Step 6: Run existing task_runtime tests for regression**

Run: `python -m pytest tests/core/test_task_runtime.py -x -q`
Expected: All existing tests pass

- [ ] **Step 7: Commit**

```bash
git add src/core/task_runtime.py tests/core/test_plan_injection.py
git commit -m "feat(plan): add per-round plan injection in tool loop"
```

---

## Task 5: tell_user Soft Gate

**Files:**
- Modify: `src/tools/tell_user.py:58` (insert before `send_fn` call)
- Test: `tests/tools/test_tell_user_plan_gate.py`

- [ ] **Step 1: Write soft gate tests**

```python
# tests/tools/test_tell_user_plan_gate.py
"""tell_user soft gate 测试——当计划有未完成步骤时的行为。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.plan_state import PlanState
from src.tools.tell_user import tell_user_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(
    *, send_fn=None, services: dict | None = None,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services if services is not None else {},
        adapter="qq",
        user_id="user1",
        auth_level=2,
        chat_id="chat-x",
        send_fn=send_fn,
    )


def _noop_send():
    sent: list[str] = []
    async def send_fn(text: str) -> None:
        sent.append(text)
    return send_fn, sent


@pytest.mark.asyncio
class TestTellUserPlanGate:
    async def test_no_plan_delivers_normally(self):
        send_fn, sent = _noop_send()
        ctx = _make_ctx(send_fn=send_fn)
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hi"}),
            ctx,
        )
        assert result.success is True
        assert sent == ["hi"]

    async def test_incomplete_plan_blocks_first_attempt(self):
        send_fn, sent = _noop_send()
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        ctx = _make_ctx(send_fn=send_fn, services={"plan_state": plan})

        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "hi"}),
            ctx,
        )
        assert result.success is False
        assert result.payload["reason"] == "plan_incomplete"
        assert sent == []  # nothing sent

    async def test_disarmed_gate_delivers(self):
        send_fn, sent = _noop_send()
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        ctx = _make_ctx(send_fn=send_fn, services={"plan_state": plan})

        # First call: blocked
        await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "try1"}),
            ctx,
        )
        assert sent == []

        # Second call: gate disarmed, delivers
        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "try2"}),
            ctx,
        )
        assert result.success is True
        assert sent == ["try2"]

    async def test_all_completed_delivers(self):
        send_fn, sent = _noop_send()
        plan = PlanState.create([
            {"description": "步骤1"},
            {"description": "步骤2"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "completed")
        ctx = _make_ctx(send_fn=send_fn, services={"plan_state": plan})

        result = await tell_user_executor(
            ToolExecutionRequest(name="tell_user", arguments={"text": "done!"}),
            ctx,
        )
        assert result.success is True
        assert sent == ["done!"]
```

- [ ] **Step 2: Run tests to verify current behavior (no gate yet)**

Run: `python -m pytest tests/tools/test_tell_user_plan_gate.py -x -q`
Expected: `test_incomplete_plan_blocks_first_attempt` FAILS (tell_user currently delivers even with incomplete plan)

- [ ] **Step 3: Add soft gate check to tell_user_executor**

In `src/tools/tell_user.py`, add after the `send_fn is None` check (after line 67, before `try: await context.send_fn(text)`):

```python
    # Soft gate: 若有活跃计划且存在未完成步骤，首次拦截并提醒
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

- [ ] **Step 4: Run soft gate tests**

Run: `python -m pytest tests/tools/test_tell_user_plan_gate.py -x -q`
Expected: All passed

- [ ] **Step 5: Run existing tell_user tests for regression**

Run: `python -m pytest tests/tools/test_tell_user.py -x -q`
Expected: All existing tests pass (they have no plan in services)

- [ ] **Step 6: Commit**

```bash
git add src/tools/tell_user.py tests/tools/test_tell_user_plan_gate.py
git commit -m "feat(plan): add tell_user soft gate for incomplete plans"
```

---

## Task 6: Full Regression + Cleanup

**Files:** None new — verification only

- [ ] **Step 1: Run all new tests together**

Run: `python -m pytest tests/core/test_plan_state.py tests/tools/test_plan_tools.py tests/core/test_plan_injection.py tests/tools/test_tell_user_plan_gate.py -v`
Expected: All passed

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: All ~1257 tests pass, no regressions

- [ ] **Step 3: Verify import chain works end-to-end**

Run: `python -c "from src.tools.registry import build_default_tool_registry; r = build_default_tool_registry(); names = sorted(t.name for t in r._tools.values() if t.is_model_facing); print([n for n in names if 'plan' in n])"`
Expected: `['plan_task', 'update_plan']`

- [ ] **Step 4: Final commit if any cleanup needed**

Only if steps above revealed issues requiring fixes.
