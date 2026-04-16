"""Lapwing 状态 REST API — Phase 5。

推断当前状态（idle / thinking / working），返回任务和意识循环信息。
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger("lapwing.api.routes.status_v2")

router = APIRouter(prefix="/api/v2/status", tags=["status-v2"])

_brain = None
_app = None  # FastAPI app instance


def init(brain, app) -> None:
    global _brain, _app
    _brain = brain
    _app = app


@router.get("")
async def get_status():
    """Lapwing 当前状态。"""
    # 活跃任务
    task_view_store = getattr(_app.state, "task_view_store", None) if _app else None
    active_tasks = []
    if task_view_store is not None:
        active_tasks = await task_view_store.list_tasks(status="running", limit=50)

    current_task = active_tasks[0] if active_tasks else None

    # 推断 state
    consciousness = getattr(_app.state, "consciousness", None) if _app else None
    if current_task and current_task.get("status") == "running":
        state = "working"
    elif consciousness is not None and hasattr(consciousness, "_thinking") and consciousness._thinking:
        state = "thinking"
    else:
        state = "idle"

    # 最近交互
    last_msg_ts = None
    if _brain is not None:
        try:
            last_interaction = await _brain.memory.get_last_interaction("desktop_kevin")
            if last_interaction is None:
                # 尝试 OWNER_IDS
                from config.settings import OWNER_IDS
                if OWNER_IDS:
                    owner_id = next(iter(OWNER_IDS))
                    last_interaction = await _brain.memory.get_last_interaction(owner_id)
            if last_interaction is not None:
                last_msg_ts = last_interaction.isoformat()
        except Exception:
            pass

    # 意识循环下次 tick
    next_tick = None
    if consciousness is not None and hasattr(consciousness, "next_tick_at"):
        try:
            nta = consciousness.next_tick_at
            if nta is not None:
                next_tick = nta.isoformat()
        except Exception:
            pass

    # 活跃 Agent 列表
    active_agents = list(set(
        t.get("assigned_to", "")
        for t in active_tasks
        if t.get("assigned_to")
    ))

    return {
        "state": state,
        "current_task_id": current_task.get("task_id") if current_task else None,
        "current_task_request": current_task.get("request") if current_task else None,
        "last_interaction": last_msg_ts,
        "heartbeat_next": next_tick,
        "active_agents": active_agents,
    }
