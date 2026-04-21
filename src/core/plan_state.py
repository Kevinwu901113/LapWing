"""任务计划状态模型 — PlanStep / PlanState / PlanTransitionError。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


class PlanTransitionError(ValueError):
    pass


@dataclass
class PlanStep:
    index: int
    description: str
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"
    note: str = ""


@dataclass
class PlanState:
    steps: list[PlanStep]
    created_at: float
    soft_gate_armed: bool = True

    # ── 构造 ──────────────────────────────────────────────────────

    @classmethod
    def create(cls, step_dicts: list[dict]) -> PlanState:
        if len(step_dicts) < 2:
            raise ValueError("计划至少需要 2 个步骤")
        steps = [
            PlanStep(index=i, description=d["description"])
            for i, d in enumerate(step_dicts)
        ]
        steps[0].status = "in_progress"
        return cls(steps=steps, created_at=time.time())

    # ── 查询 ──────────────────────────────────────────────────────

    def has_incomplete(self) -> bool:
        return any(s.status in ("pending", "in_progress") for s in self.steps)

    def current_step(self) -> PlanStep | None:
        for s in self.steps:
            if s.status == "in_progress":
                return s
        return None

    # ── 状态转换 ──────────────────────────────────────────────────

    _TERMINAL = frozenset({"completed", "blocked"})

    def advance(self, step_index: int, status: Literal["completed", "blocked"], note: str = "") -> PlanStep:
        if step_index < 0 or step_index >= len(self.steps):
            raise IndexError(f"步骤索引 {step_index} 超出范围 [0, {len(self.steps)})")

        step = self.steps[step_index]

        # 终态不可变
        if step.status in self._TERMINAL:
            raise PlanTransitionError(
                f"步骤 {step_index} 已处于终态 {step.status!r}，不能再转换"
            )

        # pending → completed 不合法，必须经过 in_progress
        if step.status == "pending" and status == "completed":
            raise PlanTransitionError(
                f"步骤 {step_index} 状态为 pending，必须先变为 in_progress 才能 completed"
            )

        step.status = status
        step.note = note

        # 自动推进：如果当前没有 in_progress，找第一个 pending 设为 in_progress
        if self.current_step() is None:
            for s in self.steps:
                if s.status == "pending":
                    s.status = "in_progress"
                    break

        return step

    # ── 渲染 ──────────────────────────────────────────────────────

    def render(self) -> str:
        lines = ["## 当前计划", ""]
        for s in self.steps:
            lines.append(self._render_step(s))
        return "\n".join(lines)

    def render_incomplete(self) -> str:
        lines = ["## 当前计划", ""]
        for s in self.steps:
            if s.status in ("pending", "in_progress"):
                lines.append(self._render_step(s))
        return "\n".join(lines)

    def _render_step(self, s: PlanStep) -> str:
        match s.status:
            case "completed":
                return f"[✓] {s.description}"
            case "in_progress":
                return f"[→] {s.description}  ← 当前"
            case "blocked":
                suffix = f"（{s.note}）" if s.note else ""
                return f"[✗] {s.description}{suffix}"
            case "pending":
                return f"[ ] {s.description}"

    # ── soft gate ─────────────────────────────────────────────────

    def check_soft_gate(self) -> str | None:
        if not self.has_incomplete():
            return None
        if not self.soft_gate_armed:
            return None
        self.soft_gate_armed = False
        return (
            "当前计划中还有未完成的步骤，请先完成再回复用户：\n"
            f"{self.render_incomplete()}\n"
            "如果确实需要先告诉用户中间结果，再次调用 tell_user 即可。"
        )
