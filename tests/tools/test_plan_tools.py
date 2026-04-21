"""plan_task / update_plan 工具测试。

验证计划工具的契约：
- plan_task: 创建计划、拒绝重复、拒绝单步
- update_plan: 完成步骤并推进、全部完成、无计划失败、非法转换
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.tools.plan_tools import (
    plan_task_executor,
    update_plan_executor,
)
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
)


def _make_ctx(*, services: dict | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services if services is not None else {},
        chat_id="chat-x",
    )


def _plan_req(steps: list[dict]) -> ToolExecutionRequest:
    return ToolExecutionRequest(name="plan_task", arguments={"steps": steps})


def _update_req(
    step_index: int, status: str, note: str = "",
) -> ToolExecutionRequest:
    args: dict = {"step_index": step_index, "status": status}
    if note:
        args["note"] = note
    return ToolExecutionRequest(name="update_plan", arguments=args)


@pytest.mark.asyncio
class TestPlanTask:
    async def test_creates_plan_in_services(self):
        ctx = _make_ctx()
        result = await plan_task_executor(
            _plan_req([
                {"description": "查资料"},
                {"description": "写总结"},
            ]),
            ctx,
        )
        assert result.success is True
        assert result.payload["created"] is True
        assert result.payload["total_steps"] == 2
        assert "共 2 步" in result.payload["message"]
        assert "步骤 1" in result.payload["message"]

        # plan_state 已存入 services
        plan = ctx.services["plan_state"]
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].status == "in_progress"
        assert plan.steps[1].status == "pending"

    async def test_rejects_duplicate_plan(self):
        ctx = _make_ctx()
        # 第一次成功
        await plan_task_executor(
            _plan_req([{"description": "a"}, {"description": "b"}]),
            ctx,
        )
        # 第二次失败
        result = await plan_task_executor(
            _plan_req([{"description": "c"}, {"description": "d"}]),
            ctx,
        )
        assert result.success is False
        assert "已存在" in result.payload["reason"]

    async def test_rejects_single_step(self):
        ctx = _make_ctx()
        result = await plan_task_executor(
            _plan_req([{"description": "只有一步"}]),
            ctx,
        )
        assert result.success is False
        assert "2" in result.payload["reason"]

    async def test_rejects_empty_steps(self):
        ctx = _make_ctx()
        result = await plan_task_executor(
            _plan_req([]),
            ctx,
        )
        assert result.success is False


@pytest.mark.asyncio
class TestUpdatePlan:
    async def _create_plan(self, ctx, steps=None):
        """辅助：创建一个默认 3 步计划。"""
        if steps is None:
            steps = [
                {"description": "步骤一"},
                {"description": "步骤二"},
                {"description": "步骤三"},
            ]
        result = await plan_task_executor(_plan_req(steps), ctx)
        assert result.success is True
        return ctx.services["plan_state"]

    async def test_completes_step_and_advances(self):
        ctx = _make_ctx()
        await self._create_plan(ctx)

        result = await update_plan_executor(_update_req(0, "completed"), ctx)
        assert result.success is True
        assert result.payload["updated"] is True
        assert "步骤 1 已完成" in result.payload["message"]
        assert "步骤 2" in result.payload["message"]

        plan = ctx.services["plan_state"]
        assert plan.steps[0].status == "completed"
        assert plan.steps[1].status == "in_progress"

    async def test_reports_all_done(self):
        ctx = _make_ctx()
        await self._create_plan(
            ctx, [{"description": "a"}, {"description": "b"}],
        )

        await update_plan_executor(_update_req(0, "completed"), ctx)
        result = await update_plan_executor(_update_req(1, "completed"), ctx)
        assert result.success is True
        assert result.payload["message"] == "所有步骤已完成。"

    async def test_reports_remaining_blocked(self):
        ctx = _make_ctx()
        await self._create_plan(
            ctx, [{"description": "a"}, {"description": "b"}],
        )

        # 阻塞第一步（in_progress → blocked）
        result = await update_plan_executor(
            _update_req(0, "blocked", note="依赖外部"), ctx,
        )
        assert result.success is True
        assert "标记为阻塞" in result.payload["message"]

        # 自动推进到步骤 2
        plan = ctx.services["plan_state"]
        assert plan.steps[1].status == "in_progress"

        # 再阻塞步骤 2 → 全部剩余被阻塞
        result = await update_plan_executor(
            _update_req(1, "blocked", note="也卡住了"), ctx,
        )
        assert result.success is True
        assert "剩余步骤均被阻塞" in result.payload["message"]

    async def test_fails_when_no_plan(self):
        ctx = _make_ctx()
        result = await update_plan_executor(_update_req(0, "completed"), ctx)
        assert result.success is False
        assert "没有计划" in result.payload["reason"]

    async def test_rejects_invalid_transition(self):
        ctx = _make_ctx()
        await self._create_plan(ctx)

        # 步骤 1 是 pending，不能直接 completed（必须经过 in_progress）
        result = await update_plan_executor(_update_req(1, "completed"), ctx)
        assert result.success is False
        assert "pending" in result.payload["reason"]

    async def test_rejects_out_of_range_index(self):
        ctx = _make_ctx()
        await self._create_plan(ctx)

        result = await update_plan_executor(_update_req(99, "completed"), ctx)
        assert result.success is False
        assert "超出范围" in result.payload["reason"]

    async def test_rejects_completed_to_completed(self):
        ctx = _make_ctx()
        await self._create_plan(ctx)

        # 先完成步骤 0
        await update_plan_executor(_update_req(0, "completed"), ctx)
        # 再次标记 → 终态不可变
        result = await update_plan_executor(_update_req(0, "completed"), ctx)
        assert result.success is False
        assert "终态" in result.payload["reason"]
