"""任务 REST API — 桌面端任务面板。

v2.0 Step 1 起，``/{task_id}/messages`` 端点暂时返回空列表：原本基于
EventLogger 对 ``agent.*`` 历史事件的查询已随 EventLogger 撤除而失效。
Step 6 会在 Agent Team 重构时给这个端点配上新的后端。保留路径与响应
形状以维持桌面端兼容。
"""

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("lapwing.api.routes.tasks_v2")

router = APIRouter(prefix="/api/v2/tasks", tags=["tasks-v2"])

_task_view_store = None


def init(task_view_store) -> None:
    global _task_view_store
    _task_view_store = task_view_store


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
    """任务关联的 Agent 通信消息。

    v2.0 Step 1 起暂时返回空列表；Step 6 Agent Team 重构后将改由
    StateMutationLog 派生的 agent-*.mutation 事件回填。
    """
    return {"task_id": task_id, "messages": []}
