"""数据相关 API 端点：对话、记忆、兴趣、任务流、提醒、学习。"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.data")

router = APIRouter(tags=["data"])

# 由 server.py init() 注入
_brain = None
_journal_dir: Path | None = None


def init(brain, *, journal_dir: Path) -> None:
    global _brain, _journal_dir
    _brain = brain
    _journal_dir = journal_dir


def _read_learning_entries(directory: Path) -> list[dict]:
    if not directory.exists():
        return []

    items: list[dict] = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        stat = path.stat()
        items.append(
            {
                "filename": path.name,
                "date": path.stem,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "content": path.read_text(encoding="utf-8"),
            }
        )
    return items


@router.get("/api/chat/history")
async def get_chat_history(
    chat_id: str = Query(...),
    limit: int = Query(50, ge=1, le=500),
    before: str | None = Query(None),
):
    """获取统一对话历史（跨通道，支持游标分页）。"""
    messages = await _brain.memory.get_messages(chat_id, limit=limit, before=before)
    return {"messages": messages, "has_more": len(messages) == limit}


@router.get("/api/chats")
async def get_chats():
    chat_ids = await _brain.memory.get_all_chat_ids()
    items = []
    for chat_id in chat_ids:
        last_interaction = await _brain.memory.get_last_interaction(chat_id)
        items.append(
            {
                "chat_id": chat_id,
                "last_interaction": last_interaction.isoformat() if last_interaction else None,
            }
        )
    items.sort(key=lambda item: item["last_interaction"] or "", reverse=True)
    return items


@router.get("/api/interests")
async def get_interests(chat_id: str = Query(...)):
    # Phase 1: interest tracking removed
    return {"chat_id": chat_id, "items": []}


@router.get("/api/memory")
async def get_memory(chat_id: str = Query(...)):
    # Phase 1: user_facts removed
    return {"chat_id": chat_id, "items": []}


@router.get("/api/memory/health")
async def get_memory_health():
    # Phase 1: memory_index removed
    return {"score": 0, "total": 0, "dimensions": {}}


@router.get("/api/task-flows")
async def list_task_flows(chat_id: str | None = None):
    flow_manager = getattr(_brain, "task_flow_manager", None)
    if flow_manager is None:
        return {"flows": []}
    flows = flow_manager.list_active(chat_id)
    return {"flows": [f.to_dict() for f in flows]}


@router.post("/api/task-flows/{flow_id}/cancel")
async def cancel_task_flow(flow_id: str):
    flow_manager = getattr(_brain, "task_flow_manager", None)
    if flow_manager is None:
        raise HTTPException(status_code=404, detail="task flow manager not available")
    if not flow_manager.cancel_flow(flow_id):
        raise HTTPException(status_code=404, detail=f"flow {flow_id} not found or already finished")
    return {"status": "cancel_intent_set"}


@router.get("/api/reminders")
async def list_reminders_endpoint(chat_id: str = Query(...)):
    reminders = await _brain.memory.list_reminders(chat_id)
    return {"reminders": reminders}


@router.delete("/api/reminders/{reminder_id}")
async def cancel_reminder(reminder_id: int, chat_id: str = Query(...)):
    success = await _brain.memory.cancel_reminder(chat_id, reminder_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"reminder {reminder_id} not found")
    scheduler = getattr(_brain, "reminder_scheduler", None)
    if scheduler is not None and hasattr(scheduler, "notify_cancel"):
        scheduler.notify_cancel(reminder_id)
    return {"status": "cancelled"}


@router.get("/api/learnings")
async def get_learnings():
    items = await asyncio.to_thread(_read_learning_entries, _journal_dir)
    return {"items": items}


# ── 记忆编辑 / 知识笔记编辑 (M-SERVER-5) ───────────────────────────────


class MemoryEditRequest(BaseModel):
    path: str
    content: str


class KnowledgeEditRequest(BaseModel):
    content: str


@router.post("/api/memory/edit")
async def edit_memory(body: MemoryEditRequest):
    """编辑记忆文件内容（相对于 data/memory/ 或 data/evolution/）。"""
    from config.settings import MEMORY_DIR, EVOLUTION_DIR

    # 拒绝绝对路径和显式遍历
    if body.path.startswith("/") or ".." in body.path.split("/"):
        raise HTTPException(status_code=400, detail="路径不合法")

    memory_root = MEMORY_DIR.resolve()
    evolution_root = EVOLUTION_DIR.resolve()

    for root in [memory_root, evolution_root]:
        candidate = (root / body.path).resolve()
        # 严格校验：解析后必须仍在允许的根目录内
        if not candidate.is_relative_to(root):
            continue
        if candidate.exists() and candidate.is_file():
            candidate.write_text(body.content, encoding="utf-8")
            return {"ok": True, "path": str(candidate)}

    raise HTTPException(status_code=404, detail="文件不存在")


@router.put("/api/knowledge/notes/{topic}")
async def edit_knowledge_note(topic: str, body: KnowledgeEditRequest):
    """编辑知识笔记内容。"""
    from config.settings import DATA_DIR
    knowledge_dir = DATA_DIR / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    path = knowledge_dir / f"{topic}.md"
    if not path.resolve().is_relative_to(knowledge_dir.resolve()):
        return JSONResponse(status_code=403, content={"error": "path traversal blocked"})
    path.write_text(body.content, encoding="utf-8")
    return {"ok": True}
