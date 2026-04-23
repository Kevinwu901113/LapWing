"""add_correction 工具——记录 Kevin 对 Lapwing 行为的纠正。"""

from src.tools.types import ToolExecutionRequest, ToolExecutionContext, ToolExecutionResult, ToolSpec


async def add_correction_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext
) -> ToolExecutionResult:
    """执行纠正记录。从 services 中获取 CorrectionManager，记录并返回当前次数。"""
    rule_key = str(req.arguments.get("rule_key", "")).strip()
    details = str(req.arguments.get("details", "")).strip()

    if not rule_key:
        return ToolExecutionResult(
            success=False,
            payload={"error": "rule_key 不能为空"},
            reason="missing_rule_key",
        )

    manager = ctx.services.get("correction_manager")
    if manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "correction_manager 未注入"},
            reason="unavailable",
        )

    count = manager.add_correction(rule_key, details)
    return ToolExecutionResult(
        success=True,
        payload={"rule_key": rule_key, "count": count},
    )


ADD_CORRECTION_SPEC = ToolSpec(
    name="add_correction",
    description=(
        "记录一次 Kevin 对 Lapwing 行为的纠正。"
        "rule_key 是简短的规则标识（如「不要列清单」），details 是具体情况描述。"
        "同一规则被纠正 3 次后，会在下次 heartbeat 时触发反思。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "rule_key": {
                "type": "string",
                "description": "简短规则标识，如「不要列清单」",
            },
            "details": {
                "type": "string",
                "description": "具体违规情况描述（可选）",
            },
        },
        "required": ["rule_key"],
    },
    executor=add_correction_executor,
    capability="general",
    risk_level="low",
)
