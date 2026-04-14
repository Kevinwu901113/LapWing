"""delegate_task 工具执行器 — 通过 DelegationManager 委派子 agent。"""

from __future__ import annotations

import logging

from src.core.delegation import AgentRole, DelegationManager, DelegationTask
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.delegation_tool")

# 字符串 → AgentRole 映射（回退用，agent_name 优先）
_ROLE_MAP: dict[str, AgentRole] = {
    "researcher": AgentRole.RESEARCHER,
    "coder": AgentRole.CODER,
    "browser": AgentRole.BROWSER,
    "file_agent": AgentRole.FILE_AGENT,
    "general": AgentRole.GENERAL,
}


async def delegate_task_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """执行委托任务。"""
    raw_tasks = request.arguments.get("tasks", [])
    if not raw_tasks:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 tasks 参数"},
            reason="缺少 tasks 参数",
        )

    delegation_manager: DelegationManager | None = context.services.get("delegation_manager")
    if delegation_manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "委托系统未初始化"},
            reason="委托系统未初始化",
        )

    # 解析任务列表
    tasks: list[DelegationTask] = []
    for raw in raw_tasks[:3]:
        goal = str(raw.get("goal", "")).strip()
        ctx = str(raw.get("context", "")).strip()
        agent_name = str(raw.get("agent", "")).strip().lower() or None
        role_str = str(raw.get("role", "general")).strip().lower()
        role = _ROLE_MAP.get(role_str, AgentRole.GENERAL)

        if not goal:
            continue
        tasks.append(DelegationTask(
            goal=goal,
            context=ctx,
            role=role,
            agent_name=agent_name,
        ))

    if not tasks:
        return ToolExecutionResult(
            success=False,
            payload={"error": "没有有效的任务"},
            reason="没有有效的任务",
        )

    # 执行委托
    try:
        results = await delegation_manager.delegate(
            tasks=tasks,
            chat_id=context.chat_id,
        )
    except Exception as e:
        logger.error("委托执行失败: %s", e)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"委托执行失败: {e}"},
            reason=str(e),
        )

    # 格式化结果
    lines = []
    all_success = True
    for r in results:
        status = "完成" if r.success else "失败"
        agent_label = r.agent_name or r.role.value
        lines.append(
            f"### 子任务 {r.task_index + 1} ({agent_label}) — {status}\n"
            f"耗时: {r.duration_seconds:.1f}s | 工具调用: {r.tool_calls_count} 次\n"
            f"{r.summary}"
        )
        if not r.success:
            all_success = False

    output = "\n\n".join(lines)
    return ToolExecutionResult(
        success=all_success,
        payload={"output": output, "results_count": len(results)},
    )
