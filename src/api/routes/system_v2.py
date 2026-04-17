"""系统信息 REST API — 桌面端状态面板。

v2.0 Step 1 起，``/events`` 端点改为查询 StateMutationLog.mutation_log.db
而不是已撤除的 events_v2.db。响应字段保持兼容：
``event_id`` / ``event_type`` / ``timestamp`` / ``actor`` / ``task_id`` /
``payload``。``actor`` 在新表里没有对应列，返回固定占位 ``"system"``；
``task_id`` 在 payload 里可能存在，由 payload 抽取；若无则返回 None。
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

logger = logging.getLogger("lapwing.api.routes.system_v2")

router = APIRouter(prefix="/api/v2/system", tags=["system-v2"])

_brain = None
_app = None


def init(brain, app) -> None:
    global _brain, _app
    _brain = brain
    _app = app


def _mutation_log():
    app = _app
    if app is None:
        return None
    brain = getattr(app.state, "brain", None)
    return getattr(brain, "_mutation_log_ref", None)


@router.get("/info")
async def get_system_info():
    """系统信息：uptime、资源使用、意识循环、通道状态。"""
    import psutil

    started_at_str = getattr(_app.state, "started_at", None) if _app else None
    uptime = 0.0
    if started_at_str:
        try:
            started_at = datetime.fromisoformat(started_at_str)
            uptime = (datetime.now(timezone.utc) - started_at).total_seconds()
        except Exception:
            pass

    cpu_pct = await asyncio.to_thread(psutil.cpu_percent, 0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    consciousness = getattr(_app.state, "consciousness", None) if _app else None
    consciousness_info = {}
    if consciousness is not None:
        consciousness_info = {
            "current_interval": getattr(consciousness, "current_interval", None),
            "idle_streak": getattr(consciousness, "idle_streak", 0),
            "next_tick_at": (
                consciousness.next_tick_at.isoformat()
                if hasattr(consciousness, "next_tick_at") and consciousness.next_tick_at
                else None
            ),
        }

    channel_manager = getattr(_app.state, "channel_manager", None) if _app else None
    channels = {"desktop": "via_websocket"}
    if channel_manager is not None:
        from src.adapters.base import ChannelType
        for ct in ChannelType:
            if ct == ChannelType.DESKTOP:
                continue
            adapter = channel_manager.adapters.get(ct)
            if adapter is not None:
                channels[ct.value] = adapter.is_connected()

    return {
        "uptime_seconds": uptime,
        "cpu_percent": cpu_pct,
        "memory": {
            "total": mem.total,
            "available": mem.available,
            "percent": mem.percent,
        },
        "disk": {
            "total": disk.total,
            "free": disk.free,
            "percent": disk.percent,
        },
        "consciousness": consciousness_info,
        "channels": channels,
    }


@router.get("/events")
async def query_events(
    event_type: str = Query(None, description="事件类型过滤"),
    task_id: str = Query(None, description="任务 ID 过滤（从 payload.task_id 提取）"),
    limit: int = Query(100, ge=1, le=1000),
):
    """事件日志查询。

    v2.0 Step 1 起：从 StateMutationLog.mutation_log.db 读取。
    ``task_id`` 过滤在后端做 payload 后置过滤（新 schema 里 task_id
    不是顶层列），仅用于桌面端兼容。
    """
    from src.logging.state_mutation_log import MutationType

    log = _mutation_log()
    if log is None:
        return {"events": []}

    # Map the query-string event_type to a MutationType member; if the caller
    # passes a name we don't recognise (or omits it), query by window and
    # filter afterwards.
    try:
        mtype = MutationType(event_type) if event_type else None
    except ValueError:
        mtype = None

    if mtype is not None:
        rows = await log.query_by_type(mtype, limit=limit)
    else:
        import time as _time
        rows = await log.query_by_window(0.0, _time.time() + 1, limit=limit)

    def _filter(mutation) -> bool:
        if task_id is not None and mutation.payload.get("task_id") != task_id:
            return False
        if event_type and not mtype and mutation.event_type != event_type:
            return False
        return True

    filtered = [m for m in rows if _filter(m)]
    return {
        "events": [
            {
                "event_id": str(m.id),
                "timestamp": datetime.fromtimestamp(m.timestamp, tz=timezone.utc).isoformat(),
                "event_type": m.event_type,
                "actor": "system",
                "task_id": m.payload.get("task_id"),
                "payload": m.payload,
            }
            for m in filtered
        ]
    }
