"""系统信息 REST API — Phase 5。

系统资源、uptime、意识循环状态、通道状态、事件日志查询。
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

logger = logging.getLogger("lapwing.api.routes.system_v2")

router = APIRouter(prefix="/api/v2/system", tags=["system-v2"])

_brain = None
_app = None
_event_logger = None


def init(brain, app, event_logger=None) -> None:
    global _brain, _app, _event_logger
    _brain = brain
    _app = app
    _event_logger = event_logger


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
    task_id: str = Query(None, description="任务 ID 过滤"),
    limit: int = Query(100, ge=1, le=1000),
):
    """事件日志查询。"""
    if _event_logger is None:
        return {"events": []}

    events = await _event_logger.query(
        event_type=event_type,
        task_id=task_id,
        limit=limit,
    )
    return {
        "events": [
            {
                "event_id": e.event_id,
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.event_type,
                "actor": e.actor,
                "task_id": e.task_id,
                "payload": e.payload,
            }
            for e in events
        ]
    }
