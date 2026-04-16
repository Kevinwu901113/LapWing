"""read_soul / edit_soul 工具实现。"""

from __future__ import annotations

from src.tools.types import ToolExecutionRequest, ToolExecutionResult


# SoulManager 实例由 register_soul_tools() 注入
_soul_manager = None


def register_soul_tools(registry, soul_manager) -> None:
    """注册 soul 相关工具到 registry。"""
    global _soul_manager
    _soul_manager = soul_manager

    from src.tools.types import ToolSpec

    registry.register(
        ToolSpec(
            name="read_soul",
            description="读取你的人格定义文件（soul.md）的当前完整内容。",
            json_schema={"type": "object", "properties": {}},
            executor=read_soul_executor,
            capability="identity",
            risk_level="low",
        )
    )

    registry.register(
        ToolSpec(
            name="edit_soul",
            description=(
                "修改你的人格定义文件（soul.md）。"
                "必须先 read_soul 获取当前内容。每 24 小时最多修改一次。"
                "传入完整的新内容（不是 diff）。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "new_content": {
                        "type": "string",
                        "description": "修改后的 soul.md 完整内容",
                    }
                },
                "required": ["new_content"],
            },
            executor=edit_soul_executor,
            capability="identity",
            risk_level="medium",
        )
    )


async def read_soul_executor(
    req: ToolExecutionRequest, ctx
) -> ToolExecutionResult:
    """读取 soul.md 当前全文。"""
    if _soul_manager is None:
        return ToolExecutionResult(
            success=False, payload={}, reason="SoulManager 不可用"
        )

    content = _soul_manager.read()
    return ToolExecutionResult(
        success=True,
        payload={"content": content},
        reason="ok",
    )


async def edit_soul_executor(
    req: ToolExecutionRequest, ctx
) -> ToolExecutionResult:
    """编辑 soul.md。必须先 read_soul 获取当前内容，修改后传入完整新内容。"""
    new_content = req.arguments.get("new_content", "")
    if not new_content.strip():
        return ToolExecutionResult(
            success=False,
            payload={},
            reason="new_content 不能为空。请先 read_soul 获取当前内容，修改后传入。",
        )

    if _soul_manager is None:
        return ToolExecutionResult(
            success=False, payload={}, reason="SoulManager 不可用"
        )

    result = _soul_manager.edit(
        new_content=new_content,
        actor="lapwing",
        trigger="tool_call",
    )

    return ToolExecutionResult(
        success=result["success"],
        payload={"diff_summary": result["diff_summary"]},
        reason=result["reason"],
    )
