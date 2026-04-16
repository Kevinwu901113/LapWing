"""Phase 0 最小工具集：用于测试 MiniMax M2.7 的基础工具使用能力。"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.tools.registry import ToolRegistry
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.phase0")

_NOTES_DIR = Path("data/memory/notes")


async def recall_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """语义记忆检索。Phase 0 退化为关键词搜索 notes 目录。"""
    query = str(request.arguments.get("query", "")).strip()
    if not query:
        return ToolExecutionResult(
            success=False, reason="缺少 query 参数", payload={"error": "缺少 query"}
        )

    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    hits: list[dict] = []
    keywords = query.lower().split()

    for note_file in sorted(_NOTES_DIR.glob("*.md"), reverse=True):
        try:
            text = note_file.read_text(encoding="utf-8")
        except Exception:
            continue
        # 简单关键词匹配
        lower_text = text.lower()
        if any(kw in lower_text for kw in keywords):
            # 提取正文（跳过 frontmatter）
            body = text
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    body = parts[2].strip()
            hits.append({
                "file": note_file.name,
                "content": body[:500],
            })
        if len(hits) >= 5:
            break

    if not hits:
        return ToolExecutionResult(
            success=True,
            reason="没有找到相关的记忆。",
            payload={"query": query, "results": [], "message": "没有找到相关记忆。"},
        )

    return ToolExecutionResult(
        success=True,
        reason=f"找到 {len(hits)} 条相关记忆",
        payload={"query": query, "results": hits},
    )


async def write_note_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """写一条笔记到 notes 目录。"""
    content = str(request.arguments.get("content", "")).strip()
    if not content:
        return ToolExecutionResult(
            success=False, reason="缺少 content 参数", payload={"error": "缺少 content"}
        )

    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(ZoneInfo("Asia/Taipei"))
    note_id = f"note_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    filename = f"{note_id}.md"

    frontmatter = (
        f"---\n"
        f"id: \"{note_id}\"\n"
        f"created_at: \"{ts.isoformat()}\"\n"
        f"---\n\n"
    )
    (_NOTES_DIR / filename).write_text(frontmatter + content, encoding="utf-8")

    return ToolExecutionResult(
        success=True,
        reason="笔记已保存",
        payload={"id": note_id, "message": "已记住。"},
    )


async def web_search_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """搜索（复用现有 Tavily/DDG 接口）。"""
    from src.tools import web_search

    query = str(request.arguments.get("query", "")).strip()
    if not query:
        return ToolExecutionResult(
            success=False, reason="缺少 query 参数", payload={"error": "缺少 query"}
        )

    try:
        results = await web_search.search(query, max_results=5)
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            reason=f"搜索失败: {exc}",
            payload={"query": query, "error": str(exc)},
        )

    light_results = []
    for item in results[:5]:
        light_results.append({
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "snippet": str(item.get("snippet", ""))[:200],
        })

    return ToolExecutionResult(
        success=True,
        reason=f"搜到 {len(light_results)} 条结果",
        payload={"query": query, "count": len(light_results), "results": light_results},
    )


async def get_time_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """返回当前台北时间。"""
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    time_str = (
        f"{now.year}年{now.month}月{now.day}日 "
        f"{weekday_names[now.weekday()]} "
        f"{now.strftime('%H:%M:%S')}（台北时间）"
    )
    return ToolExecutionResult(
        success=True,
        reason=time_str,
        payload={"time": time_str, "iso": now.isoformat()},
    )


async def send_message_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """主动给 Kevin 发消息（通过 channel_manager）。"""
    content = str(request.arguments.get("content", "")).strip()
    if not content:
        return ToolExecutionResult(
            success=False, reason="缺少 content 参数", payload={"error": "缺少 content"}
        )

    channel_manager = context.services.get("channel_manager")
    if channel_manager is None:
        return ToolExecutionResult(
            success=False,
            reason="消息通道不可用",
            payload={"error": "channel_manager 未注入"},
        )

    try:
        await channel_manager.send_to_owner(content)
    except Exception as exc:
        return ToolExecutionResult(
            success=False,
            reason=f"发送失败: {exc}",
            payload={"error": str(exc)},
        )

    return ToolExecutionResult(
        success=True,
        reason="消息已发送",
        payload={"message": "已发送给 Kevin。"},
    )


def build_phase0_tool_registry() -> ToolRegistry:
    """Phase 0B：最小工具集（5 个工具）。"""
    registry = ToolRegistry()

    registry.register(ToolSpec(
        name="recall",
        description="回忆与某个主题相关的记忆。输入一段描述，返回最相关的记忆内容。",
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "你想回忆的内容"},
            },
            "required": ["query"],
        },
        executor=recall_executor,
        capability="memory",
        risk_level="low",
    ))

    registry.register(ToolSpec(
        name="write_note",
        description="写一条笔记或记忆。内容会被保存，以后可以通过 recall 找到。",
        json_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记住的内容"},
            },
            "required": ["content"],
        },
        executor=write_note_executor,
        capability="memory",
        risk_level="low",
    ))

    registry.register(ToolSpec(
        name="web_search",
        description="在网上搜索信息。返回搜索结果摘要。",
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
        executor=web_search_executor,
        capability="web",
        risk_level="low",
    ))

    registry.register(ToolSpec(
        name="get_time",
        description="获取当前时间。",
        json_schema={"type": "object", "properties": {}},
        executor=get_time_executor,
        capability="general",
        risk_level="low",
    ))

    registry.register(ToolSpec(
        name="send_message",
        description="主动给 Kevin 发一条消息（不是回复当前对话，而是你主动找他）。",
        json_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要发送的内容"},
            },
            "required": ["content"],
        },
        executor=send_message_executor,
        capability="general",
        risk_level="low",
    ))

    return registry
