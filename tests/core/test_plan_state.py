"""PlanState 数据模型测试 — 状态转换、渲染、soft gate。"""
from __future__ import annotations

import pytest

from src.core.plan_state import PlanState, PlanStep, PlanTransitionError


# ── creation ────────────────────────────────────────────────────────

class TestPlanCreation:

    def test_create_sets_step0_in_progress(self):
        plan = PlanState.create([
            {"description": "第一步"},
            {"description": "第二步"},
        ])
        assert plan.steps[0].status == "in_progress"
        assert plan.steps[1].status == "pending"

    def test_create_rejects_fewer_than_two_steps(self):
        with pytest.raises(ValueError, match="至少"):
            PlanState.create([{"description": "只有一步"}])

    def test_create_rejects_empty(self):
        with pytest.raises(ValueError, match="至少"):
            PlanState.create([])


# ── advance ─────────────────────────────────────────────────────────

class TestAdvance:

    def _two_step_plan(self) -> PlanState:
        return PlanState.create([
            {"description": "步骤 A"},
            {"description": "步骤 B"},
        ])

    def _three_step_plan(self) -> PlanState:
        return PlanState.create([
            {"description": "步骤 A"},
            {"description": "步骤 B"},
            {"description": "步骤 C"},
        ])

    def test_complete_advances_next(self):
        plan = self._two_step_plan()
        step = plan.advance(0, "completed")
        assert step.status == "completed"
        assert plan.steps[1].status == "in_progress"

    def test_complete_last_step(self):
        plan = self._two_step_plan()
        plan.advance(0, "completed")
        step = plan.advance(1, "completed")
        assert step.status == "completed"
        assert not plan.has_incomplete()

    def test_block_advances_next(self):
        plan = self._two_step_plan()
        step = plan.advance(0, "blocked", note="缺少权限")
        assert step.status == "blocked"
        assert step.note == "缺少权限"
        assert plan.steps[1].status == "in_progress"

    def test_block_pending_step(self):
        plan = self._three_step_plan()
        step = plan.advance(2, "blocked", note="依赖缺失")
        assert step.status == "blocked"
        # step 0 还是 in_progress（没受影响）
        assert plan.steps[0].status == "in_progress"

    def test_auto_advance_skips_blocked(self):
        plan = self._three_step_plan()
        # block step 1 (pending)
        plan.advance(1, "blocked", note="跳过")
        # complete step 0 → auto-advance 应该跳过 blocked 的 step 1，到 step 2
        plan.advance(0, "completed")
        assert plan.steps[1].status == "blocked"
        assert plan.steps[2].status == "in_progress"

    def test_all_remaining_blocked_no_advance(self):
        plan = self._two_step_plan()
        plan.advance(1, "blocked", note="坏了")
        plan.advance(0, "completed")
        # 没有 pending 的了
        assert plan.current_step() is None

    def test_reject_out_of_range(self):
        plan = self._two_step_plan()
        with pytest.raises(IndexError):
            plan.advance(5, "completed")

    def test_reject_completed_to_blocked(self):
        plan = self._two_step_plan()
        plan.advance(0, "completed")
        with pytest.raises(PlanTransitionError, match="终态"):
            plan.advance(0, "blocked")

    def test_reject_blocked_to_completed(self):
        plan = self._two_step_plan()
        plan.advance(0, "blocked")
        with pytest.raises(PlanTransitionError, match="终态"):
            plan.advance(0, "completed")

    def test_reject_pending_to_completed(self):
        plan = self._three_step_plan()
        with pytest.raises(PlanTransitionError, match="in_progress"):
            plan.advance(2, "completed")


# ── render ──────────────────────────────────────────────────────────

class TestRender:

    def test_initial_render(self):
        plan = PlanState.create([
            {"description": "获取数据"},
            {"description": "处理数据"},
        ])
        text = plan.render()
        assert "[→] 获取数据" in text
        assert "← 当前" in text
        assert "[ ] 处理数据" in text

    def test_mid_execution_render(self):
        plan = PlanState.create([
            {"description": "步骤一"},
            {"description": "步骤二"},
            {"description": "步骤三"},
        ])
        plan.advance(0, "completed")
        text = plan.render()
        assert "[✓] 步骤一" in text
        assert "[→] 步骤二" in text
        assert "[ ] 步骤三" in text

    def test_blocked_with_note(self):
        plan = PlanState.create([
            {"description": "步骤一"},
            {"description": "步骤二"},
        ])
        plan.advance(0, "blocked", note="网络不通")
        text = plan.render()
        assert "[✗] 步骤一（网络不通）" in text

    def test_all_completed(self):
        plan = PlanState.create([
            {"description": "a"},
            {"description": "b"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "completed")
        text = plan.render()
        assert "[✓] a" in text
        assert "[✓] b" in text
        assert "← 当前" not in text


# ── render_incomplete ───────────────────────────────────────────────

class TestRenderIncomplete:

    def test_only_shows_pending_and_in_progress(self):
        plan = PlanState.create([
            {"description": "done"},
            {"description": "current"},
            {"description": "future"},
        ])
        plan.advance(0, "completed")
        text = plan.render_incomplete()
        assert "done" not in text
        assert "[→] current" in text
        assert "[ ] future" in text


# ── soft gate ───────────────────────────────────────────────────────

class TestSoftGate:

    def test_fires_once_then_disarms(self):
        plan = PlanState.create([
            {"description": "x"},
            {"description": "y"},
        ])
        first = plan.check_soft_gate()
        assert first is not None
        assert "未完成" in first
        second = plan.check_soft_gate()
        assert second is None

    def test_no_warning_when_all_completed(self):
        plan = PlanState.create([
            {"description": "x"},
            {"description": "y"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "completed")
        assert plan.check_soft_gate() is None

    def test_no_warning_when_all_blocked_or_completed(self):
        plan = PlanState.create([
            {"description": "x"},
            {"description": "y"},
        ])
        plan.advance(0, "completed")
        plan.advance(1, "blocked", note="skip")
        assert plan.check_soft_gate() is None
