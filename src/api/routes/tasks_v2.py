"""任务 REST API — Phase 5。

任务列表、详情、关联消息查询。
"""

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("lapwing.api.routes.tasks_v2")

router = APIRouter(prefix="/api/v2/tasks", tags=["tasks-v2"])

_task_view_store = None
_event_logger = None


def init(task_view_store, event_logger=None) -> None:
    global _task_view_store, _event_logger
    _task_view_store = task_view_store
    _event_logger = event_logger


@router.get("")
async def list_tasks(
    status: str = Query(None, description="按状态筛选"),
    limit: int = Query(50, ge=1, le=500),
):
    """任务列表。按状态筛选。"""
    if _task_view_store is None:
        return {"tasks": [], "count": 0}

    tasks = await _task_view_store.list_tasks(status=status, limit=limit)
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/{task_id}")
async def get_task(task_id: str):
    """任务详情。"""
    if _task_view_store is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task = await _task_view_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/messages")
async def get_task_messages(task_id: str):
    """任务关联的 Agent 通信消息（从事件日志查询）。"""
    if _event_logger is None:
        return {"task_id": task_id, "messages": []}

    events = await _event_logger.query(task_id=task_id, limit=500)

    messages = []
    for e in events:
        if e.event_type.startswith("agent."):
            messages.append({
                "event_id": e.event_id,
                "event_type": e.event_type,
                "timestamp": e.timestamp.isoformat(),
                "actor": e.actor,
                "payload": e.payload,
            })

    return {"task_id": task_id, "messages": messages}
