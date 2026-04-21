"""plan_task / update_plan — 任务计划工具。

当用户请求涉及多个子任务时，LLM 先调用 plan_task 制定计划，再逐步执行，
每完成一步调用 update_plan 推进状态。PlanState 存储在
context.services["plan_state"] 中，生命周期与单次 TaskRuntime 执行一致。
"""
from __future__ import annotations

import logging

from src.core.plan_state import PlanState, PlanTransitionError
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)

logger = logging.getLogger("lapwing.tools.plan_tools")


# ── plan_task ─────────────────────────────────────────────────────────

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
                    "description": {
                        "type": "string",
                        "description": "步骤描述",
                    },
                },
                "required": ["description"],
                "additionalProperties": False,
            },
            "minItems": 2,
            "description": "计划步骤列表，至少 2 步",
        },
    },
    "required": ["steps"],
    "additionalProperties": False,
}


async def plan_task_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """创建新计划。拒绝重复创建；要求 >= 2 步。"""
    services = context.services or {}

    # 已有计划则拒绝
    if services.get("plan_state") is not None:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "计划已存在，不能重复创建"},
            reason="plan_task 在已有计划的上下文中被调用",
        )

    step_dicts = request.arguments.get("steps")
    if not isinstance(step_dicts, list) or len(step_dicts) < 2:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": "计划至少需要 2 个步骤"},
            reason="plan_task 步骤数不足",
        )

    try:
        plan = PlanState.create(step_dicts)
    except (ValueError, KeyError, TypeError) as exc:
        return ToolExecutionResult(
            success=False,
            payload={"created": False, "reason": str(exc)},
            reason=f"PlanState.create 失败: {exc}",
        )

    context.services["plan_state"] = plan
    n = len(plan.steps)
    return ToolExecutionResult(
        success=True,
        payload={
            "created": True,
            "total_steps": n,
            "message": f"计划已创建，共 {n} 步。当前执行：步骤 1。",
        },
    )


# ── update_plan ───────────────────────────────────────────────────────

UPDATE_PLAN_DESCRIPTION = (
    "更新计划中某个步骤的状态。完成当前步骤后调用此工具标记为 completed，"
    "下一步会自动变为 in_progress。"
)

UPDATE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "step_index": {
            "type": "integer",
            "description": "步骤索引（从 0 开始）",
        },
        "status": {
            "type": "string",
            "enum": ["completed", "blocked"],
            "description": "目标状态：completed 或 blocked",
        },
        "note": {
            "type": "string",
            "description": "（可选）备注信息，例如阻塞原因",
        },
    },
    "required": ["step_index", "status"],
    "additionalProperties": False,
}

_STATUS_LABEL = {"completed": "完成", "blocked": "标记为阻塞"}


async def update_plan_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """推进计划步骤状态。"""
    services = context.services or {}
    plan: PlanState | None = services.get("plan_state")
    if plan is None:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "当前没有计划"},
            reason="update_plan 在没有 plan_state 的上下文中被调用",
        )

    step_index = request.arguments.get("step_index")
    status = request.arguments.get("status")
    note = request.arguments.get("note", "")

    if not isinstance(step_index, int):
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "step_index 必须是整数"},
            reason="update_plan 参数类型错误",
        )

    if status not in ("completed", "blocked"):
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": "status 必须为 completed 或 blocked"},
            reason="update_plan status 值非法",
        )

    try:
        plan.advance(step_index, status, note=note)
    except (IndexError, PlanTransitionError) as exc:
        return ToolExecutionResult(
            success=False,
            payload={"updated": False, "reason": str(exc)},
            reason=f"PlanState.advance 失败: {exc}",
        )

    label = _STATUS_LABEL[status]

    # 判断后续状态
    current = plan.current_step()
    has_blocked = any(s.status == "blocked" for s in plan.steps)
    if current is not None:
        msg = f"步骤 {step_index + 1} 已{label}。当前执行：步骤 {current.index + 1}。"
    elif plan.has_incomplete() or has_blocked:
        # 有未完成步骤（pending/in_progress）或被阻塞步骤但无 in_progress
        msg = f"步骤 {step_index + 1} 已{label}。剩余步骤均被阻塞。"
    else:
        msg = "所有步骤已完成。"

    return ToolExecutionResult(
        success=True,
        payload={
            "updated": True,
            "step_index": step_index,
            "status": status,
            "message": msg,
        },
    )
