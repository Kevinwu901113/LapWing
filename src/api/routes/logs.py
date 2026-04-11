"""事件日志查询 API。"""

import time

from fastapi import APIRouter, Query

from src.logging.event_logger import CATEGORIES, get_event_logger

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/events")
async def get_events(
    category: str | None = Query(None, description="事件类别"),
    event_type: str | None = Query(None, description="事件子类型"),
    minutes: int = Query(60, description="最近 N 分钟"),
    limit: int = Query(100, description="最大条数", le=500),
):
    """查询事件日志。"""
    event_logger = get_event_logger()
    since = time.time() - (minutes * 60)
    events = event_logger.query(
        category=category,
        event_type=event_type,
        since=since,
        limit=limit,
    )
    return {"events": events, "total": len(events)}


@router.get("/categories")
async def get_categories():
    """返回所有事件类别及其配置。"""
    return {"categories": CATEGORIES}
