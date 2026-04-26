"""Tools for Lapwing's focus lifecycle."""

from __future__ import annotations

import time
from typing import Any

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)


async def close_focus_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    manager = (context.services or {}).get("focus_manager")
    if manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"closed": False, "reason": "FocusManager 未挂载"},
            reason="close_focus 在没有 focus_manager 的上下文中被调用",
        )
    if context.focus_id is None:
        return ToolExecutionResult(
            success=True,
            payload={"closed": False, "message": "当前没有活跃焦点。"},
        )
    await manager.deactivate(context.focus_id)
    return ToolExecutionResult(
        success=True,
        payload={"closed": True, "message": "焦点已关闭。"},
    )


async def recall_focus_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    query = str(request.arguments.get("query", "")).strip()
    if not query:
        return ToolExecutionResult(
            success=False,
            payload={"results": [], "message": "query 不能为空"},
            reason="recall_focus 缺少 query",
        )
    manager = (context.services or {}).get("focus_manager")
    if manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"results": [], "reason": "FocusManager 未挂载"},
            reason="recall_focus 在没有 focus_manager 的上下文中被调用",
        )
    focuses = await manager.search_history(query, n=3)
    if not focuses:
        return ToolExecutionResult(
            success=True,
            payload={"results": [], "message": "没有找到相关的历史焦点。"},
        )
    lines: list[str] = []
    payload_rows: list[dict[str, Any]] = []
    for focus in focuses:
        ago = _format_time_ago(focus.last_active_at)
        summary = focus.summary or "未命名焦点"
        lines.append(f"- {summary}（{ago}前，{focus.entry_count} 条交互）")
        payload_rows.append({
            "focus_id": focus.id,
            "summary": summary,
            "entry_count": focus.entry_count,
            "last_active_at": focus.last_active_at,
        })
    return ToolExecutionResult(
        success=True,
        payload={
            "results": payload_rows,
            "message": "找到以下相关焦点：\n" + "\n".join(lines),
        },
    )


def register_focus_tools(registry: Any) -> None:
    registry.register(CLOSE_FOCUS_SPEC)
    registry.register(RECALL_FOCUS_SPEC)


def _format_time_ago(timestamp: float) -> str:
    delta = max(0.0, time.time() - timestamp)
    if delta < 60:
        return "不到1分钟"
    if delta < 3600:
        return f"{int(delta // 60)}分钟"
    if delta < 86400:
        return f"{int(delta // 3600)}小时"
    return f"{int(delta // 86400)}天"


CLOSE_FOCUS_SPEC = ToolSpec(
    name="close_focus",
    description="关闭当前焦点。当你认为当前话题已经告一段落时使用。",
    json_schema={"type": "object", "properties": {}, "additionalProperties": False},
    executor=close_focus_executor,
    capability="focus",
    capabilities=("general",),
    risk_level="low",
)


RECALL_FOCUS_SPEC = ToolSpec(
    name="recall_focus",
    description="回忆之前聊过的话题。提供关键词，我会搜索相关的历史焦点。",
    json_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "想找什么话题",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    executor=recall_focus_executor,
    capability="focus",
    capabilities=("general", "memory"),
    risk_level="low",
)
