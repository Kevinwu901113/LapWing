"""report_incident 工具 — 让 Lapwing 自己报告发现的问题。"""

from src.tools.types import ToolExecutionRequest, ToolExecutionContext, ToolExecutionResult


async def execute_report_incident(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """Lapwing 主动报告一个问题。"""
    incident_manager = context.services.get("incident_manager")
    if incident_manager is None:
        return ToolExecutionResult(
            success=False, reason="incident_manager 未启用", payload={},
        )

    description = request.arguments.get("description", "").strip()
    severity = request.arguments.get("severity", "medium")
    related_tool = request.arguments.get("related_tool")

    if not description:
        return ToolExecutionResult(
            success=False, reason="description 不能为空", payload={},
        )

    inc_id = await incident_manager.create(
        source="self_note",
        description=description,
        context={
            "observation": description,
            "chat_id": context.chat_id,
        },
        severity=severity,
        related_tool=related_tool,
    )

    if inc_id:
        return ToolExecutionResult(
            success=True,
            reason=f"已记录问题 {inc_id}",
            payload={"incident_id": inc_id},
        )
    else:
        return ToolExecutionResult(
            success=True,
            reason="已合并到已有记录（去重）",
            payload={},
        )
