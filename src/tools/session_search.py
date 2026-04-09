"""session_search 工具执行器 — FTS5 全文搜索历史对话记录。"""

from __future__ import annotations

import logging

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.session_search")


async def session_search_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """搜索历史对话记录（FTS5 全文检索）。"""
    query = str(request.arguments.get("query", "")).strip()
    days_back = request.arguments.get("days_back")

    if not query:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 query 参数"},
            reason="缺少 query 参数",
        )

    memory = context.memory
    if memory is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "记忆系统未初始化"},
            reason="记忆系统未初始化",
        )

    try:
        results = await memory.search_history(
            query,
            chat_id=context.chat_id or None,
            limit=10,
            days_back=int(days_back) if days_back is not None else None,
        )
    except Exception as e:
        logger.error("session_search 执行失败: %s", e)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"搜索失败: {e}"},
            reason=str(e),
        )

    if not results:
        return ToolExecutionResult(
            success=True,
            payload={"output": f"未找到包含 '{query}' 的对话记录。"},
        )

    # 格式化搜索结果
    lines = [f"找到 {len(results)} 条匹配的对话记录：\n"]
    for i, r in enumerate(results, 1):
        role = "Kevin" if r["role"] == "user" else "Lapwing"
        ts = r["timestamp"][:16] if r["timestamp"] else "未知时间"
        content_preview = r["content"][:200]
        if len(r["content"]) > 200:
            content_preview += "..."
        lines.append(f"[{i}] {ts} {role}: {content_preview}")

        # 附带上下文
        for ctx_msg in r.get("context", []):
            ctx_role = "Kevin" if ctx_msg["role"] == "user" else "Lapwing"
            ctx_content = ctx_msg["content"][:100]
            if len(ctx_msg["content"]) > 100:
                ctx_content += "..."
            lines.append(f"    ↳ {ctx_role}: {ctx_content}")
        lines.append("")

    output = "\n".join(lines)
    return ToolExecutionResult(success=True, payload={"output": output})
