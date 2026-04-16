"""经验技能管理工具 — experience_skill_list / experience_skill_view / experience_skill_manage。"""

from __future__ import annotations

import logging

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.experience_skill_tools")


async def _execute_experience_skill_list(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """列出所有经验技能（Tier 1）。"""
    esm = context.services.get("experience_skill_manager")
    if esm is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "经验技能系统不可用"},
            reason="esm_unavailable",
        )

    listing = esm.list_skills()
    return ToolExecutionResult(
        success=True,
        payload={"output": listing},
    )


async def _execute_experience_skill_view(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """查看经验技能详情（Tier 2/3）。"""
    esm = context.services.get("experience_skill_manager")
    if esm is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "经验技能系统不可用"},
            reason="esm_unavailable",
        )

    name = str(request.arguments.get("name", "")).strip()
    if not name:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 name 参数"},
            reason="missing_name",
        )

    reference = request.arguments.get("reference")
    if reference:
        reference = str(reference).strip()

    content = esm.view_skill(name, reference=reference or None)
    return ToolExecutionResult(
        success=True,
        payload={"output": content},
    )


async def _execute_experience_skill_manage(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """管理经验技能：创建/更新/删除。"""
    esm = context.services.get("experience_skill_manager")
    if esm is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "经验技能系统不可用"},
            reason="esm_unavailable",
        )

    action = str(request.arguments.get("action", "")).strip()
    name = str(request.arguments.get("name", "")).strip()

    if not action or not name:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 action 或 name 参数"},
            reason="missing_params",
        )

    result = esm.manage_skill(
        action=action,
        name=name,
        content=request.arguments.get("content"),
        old_text=request.arguments.get("old_text"),
        new_text=request.arguments.get("new_text"),
    )
    return ToolExecutionResult(
        success=True,
        payload={"output": result},
    )


EXPERIENCE_SKILL_EXECUTORS = {
    "experience_skill_list": _execute_experience_skill_list,
    "experience_skill_view": _execute_experience_skill_view,
    "experience_skill_manage": _execute_experience_skill_manage,
}
