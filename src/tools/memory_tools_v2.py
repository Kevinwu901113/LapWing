"""memory_tools_v2 — Phase 3 记忆工具集。

包含 9 个工具执行器 + register_memory_tools_v2() 注册函数。
依赖通过 ctx.services 注入：
  - "note_store"          NoteStore 实例
  - "vector_store"        MemoryVectorStore 实例（��选）
"""

from __future__ import annotations

import asyncio
import logging

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.memory_tools_v2")

# 单条内容最大字符数
_MAX_CONTENT_CHARS = 500
# 所有结果合计最大字符数
_TOTAL_BUDGET = 2000


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


async def recall_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    """语义记忆检索。"""
    vector_store = ctx.services.get("vector_store")
    if vector_store is None:
        return ToolExecutionResult(success=False, payload={"error": "向量记忆库不可用"}, reason="vector_store 未配置")

    query: str = req.arguments.get("query", "").strip()
    top_k: int = int(req.arguments.get("top_k", 5))

    try:
        raw_results = await vector_store.recall(query, top_k=top_k)
    except Exception as e:
        logger.error("recall 失败: %s", e)
        return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))

    results = []
    total_chars = 0

    for r in raw_results:
        # 截断单条内容
        content = r.content[:_MAX_CONTENT_CHARS]
        total_chars += len(content)
        if total_chars > _TOTAL_BUDGET:
            break
        results.append({
            "note_id": r.note_id,
            "content": content,
            "score": round(r.score, 4),
            "note_type": r.note_type,
            "trust": r.trust,
            "created_at": r.created_at,
        })

    return ToolExecutionResult(
        success=True,
        payload={"results": results, "count": len(results), "query": query},
    )


# ---------------------------------------------------------------------------
# write_note
# ---------------------------------------------------------------------------


async def write_note_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    """写笔记到 NoteStore，并可选触发异步向量嵌入。"""
    note_store = ctx.services.get("note_store")
    content: str = req.arguments.get("content", "")

    if not content.strip():
        return ToolExecutionResult(success=False, payload={"error": "内容不能为空"}, reason="content 为空")

    note_type: str = req.arguments.get("note_type", "observation")
    path: str | None = req.arguments.get("path") or None

    try:
        info = note_store.write(content=content, note_type=note_type, path=path)
    except Exception as e:
        logger.error("write_note 失败: %s", e)
        return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))

    # 异步触发向量嵌入（fire-and-forget）
    vector_store = ctx.services.get("vector_store")
    if vector_store is not None:
        asyncio.create_task(_embed_note(vector_store, info["note_id"], content, note_type))

    return ToolExecutionResult(
        success=True,
        payload={"note_id": info["note_id"], "file_path": info["file_path"]},
    )


async def _embed_note(vector_store, note_id: str, content: str, note_type: str) -> None:
    """后台嵌入任务。"""
    try:
        await vector_store.add(
            note_id=note_id,
            content=content,
            metadata={"note_type": note_type},
        )
    except Exception as e:
        logger.warning("向量嵌入失败 note_id=%s: %s", note_id, e)


# ---------------------------------------------------------------------------
# edit_note
# ---------------------------------------------------------------------------


async def edit_note_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    """编辑已有笔记。"""
    note_store = ctx.services.get("note_store")
    note_id: str = req.arguments.get("note_id", "").strip()
    content: str = req.arguments.get("content", "")

    try:
        result = note_store.edit(note_id, content)
    except Exception as e:
        logger.error("edit_note 失败: %s", e)
        return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))

    if not result.get("success"):
        reason = result.get("reason", "编辑失败")
        return ToolExecutionResult(success=False, payload={"error": reason}, reason=reason)

    return ToolExecutionResult(success=True, payload={"note_id": note_id})


# ---------------------------------------------------------------------------
# read_note
# ---------------------------------------------------------------------------


async def read_note_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    """读取笔记完整内容。"""
    note_store = ctx.services.get("note_store")
    note_id: str = req.arguments.get("note_id", "").strip()

    try:
        result = note_store.read(note_id)
    except Exception as e:
        logger.error("read_note 失败: %s", e)
        return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))

    if result is None:
        return ToolExecutionResult(success=False, payload={"error": f"笔记不存在: {note_id}"}, reason="笔记不存在")

    return ToolExecutionResult(
        success=True,
        payload={
            "note_id": note_id,
            "content": result["content"],
            "meta": result["meta"],
            "file_path": result["file_path"],
        },
    )


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------


async def list_notes_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    """列出笔记目录结构。"""
    note_store = ctx.services.get("note_store")
    path: str | None = req.arguments.get("path") or None

    try:
        entries = note_store.list_notes(path=path)
    except Exception as e:
        logger.error("list_notes 失败: %s", e)
        return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))

    return ToolExecutionResult(
        success=True,
        payload={"entries": entries, "count": len(entries), "path": path or "/"},
    )


# ---------------------------------------------------------------------------
# move_note
# ---------------------------------------------------------------------------


async def move_note_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    """移动笔记到新目录。"""
    note_store = ctx.services.get("note_store")
    note_id: str = req.arguments.get("note_id", "").strip()
    new_path: str = req.arguments.get("new_path", "").strip()

    try:
        result = note_store.move(note_id, new_path)
    except Exception as e:
        logger.error("move_note 失败: %s", e)
        return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))

    if not result.get("success"):
        reason = result.get("reason", "移动失败")
        return ToolExecutionResult(success=False, payload={"error": reason}, reason=reason)

    return ToolExecutionResult(
        success=True,
        payload={"note_id": note_id, "new_path": result.get("new_path", "")},
    )


# ---------------------------------------------------------------------------
# search_notes
# ---------------------------------------------------------------------------


async def search_notes_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    """关键词搜索笔记。"""
    note_store = ctx.services.get("note_store")
    keyword: str = req.arguments.get("keyword", "").strip()

    if not keyword:
        return ToolExecutionResult(success=False, payload={"error": "缺少 keyword"}, reason="keyword 为空")

    try:
        results = note_store.search_keyword(keyword)
    except Exception as e:
        logger.error("search_notes 失败: %s", e)
        return ToolExecutionResult(success=False, payload={"error": str(e)}, reason=str(e))

    return ToolExecutionResult(
        success=True,
        payload={"results": results, "count": len(results), "keyword": keyword},
    )


# ---------------------------------------------------------------------------
# 注册函数
# ---------------------------------------------------------------------------


def register_memory_tools_v2(registry) -> None:
    """注册所有 9 个 Phase 3 记忆工具。"""
    tools = [
        ToolSpec(
            name="recall",
            description="回忆与某个主题相关的记忆。输入一段描述，返回最相关的记忆。",
            json_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "你想回忆的内容"},
                    "top_k": {"type": "integer", "description": "返回几条（默认5）", "default": 5},
                },
                "required": ["query"],
            },
            executor=recall_executor,
            capability="memory",
            risk_level="low",
        ),
        ToolSpec(
            name="write_note",
            description="写一条笔记或记忆。可选指定类型和存放路径。",
            json_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要记住的内容"},
                    "note_type": {
                        "type": "string",
                        "enum": ["observation", "reflection", "fact", "summary"],
                        "default": "observation",
                    },
                    "path": {"type": "string", "description": "存放目录（如 people/kevin）。可选。"},
                },
                "required": ["content"],
            },
            executor=write_note_executor,
            capability="memory",
            risk_level="low",
        ),
        ToolSpec(
            name="edit_note",
            description="编辑一条已有笔记的内容。",
            json_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "笔记 ID 或文件路径"},
                    "content": {"type": "string", "description": "新的完整内容"},
                },
                "required": ["note_id", "content"],
            },
            executor=edit_note_executor,
            capability="memory",
            risk_level="low",
        ),
        ToolSpec(
            name="read_note",
            description="读取一条笔记的完整内容。",
            json_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "笔记 ID 或文件路径"},
                },
                "required": ["note_id"],
            },
            executor=read_note_executor,
            capability="memory",
            risk_level="low",
        ),
        ToolSpec(
            name="list_notes",
            description="查看笔记目录结构。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "子目录路径。不填则看根目录。"},
                },
            },
            executor=list_notes_executor,
            capability="memory",
            risk_level="low",
        ),
        ToolSpec(
            name="move_note",
            description="把一条笔记移到新目录。",
            json_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "笔记 ID 或文件路径"},
                    "new_path": {"type": "string", "description": "目标目录（如 people/friends）"},
                },
                "required": ["note_id", "new_path"],
            },
            executor=move_note_executor,
            capability="memory",
            risk_level="low",
        ),
        ToolSpec(
            name="search_notes",
            description="用关键词搜索笔记。",
            json_schema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["keyword"],
            },
            executor=search_notes_executor,
            capability="memory",
            risk_level="low",
        ),
    ]
    for tool in tools:
        registry.register(tool)
