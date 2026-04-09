"""数据相关 API 端点：对话、记忆、兴趣、任务流、提醒、学习。"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.data")

router = APIRouter(tags=["data"])

# 由 server.py init() 注入
_brain = None
_journal_dir: Path | None = None


class MemoryDeleteRequest(BaseModel):
    chat_id: str
    fact_key: str


def init(brain, *, journal_dir: Path) -> None:
    global _brain, _journal_dir
    _brain = brain
    _journal_dir = journal_dir


def _visible_user_facts(facts: list[dict]) -> list[dict]:
    return [
        fact for fact in facts
        if not str(fact.get("fact_key", "")).startswith("memory_summary_")
    ]


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
    items = await _brain.memory.get_top_interests(chat_id, limit=10)
    return {"chat_id": chat_id, "items": items}


@router.get("/api/memory")
async def get_memory(chat_id: str = Query(...)):
    facts = _visible_user_facts(await _brain.memory.get_user_facts(chat_id))
    items = [
        {
            "index": index,
            "fact_key": fact["fact_key"],
            "fact_value": fact["fact_value"],
            "updated_at": fact.get("updated_at"),
        }
        for index, fact in enumerate(facts, start=1)
    ]
    return {"chat_id": chat_id, "items": items}


@router.post("/api/memory/delete")
async def delete_memory(payload: MemoryDeleteRequest):
    success = await _brain.memory.delete_user_fact(payload.chat_id, payload.fact_key)
    return {"success": success}


@router.get("/api/memory/health")
async def get_memory_health():
    memory_index = getattr(_brain, "memory_index", None)
    if memory_index is None:
        return {"score": 0, "total": 0, "dimensions": {}}
    return memory_index.health_score()


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
