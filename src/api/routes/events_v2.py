"""SSE 事件推送 — Phase 5。

基于 Dispatcher 全局订阅，支持 Last-Event-ID 断线重连。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("lapwing.api.routes.events_v2")

router = APIRouter(prefix="/api/v2", tags=["events-v2"])

_dispatcher = None
_event_logger = None


def init(dispatcher=None, event_logger=None) -> None:
    global _dispatcher, _event_logger
    _dispatcher = dispatcher
    _event_logger = event_logger


def _format_sse(event) -> str:
    """格式化为 SSE 消息。"""
    data = json.dumps({
        "event_id": event.event_id,
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat(),
        "actor": event.actor,
        "task_id": event.task_id,
        "payload": event.payload,
    }, ensure_ascii=False)

    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/events")
async def event_stream(request: Request):
    """
    SSE 事件流。推送通过 Dispatcher 提交的所有事件。
    支持断线重连（通过 Last-Event-ID header）。
    """
    last_event_id = request.headers.get("Last-Event-ID")

    async def event_generator():
        # 如果有 Last-Event-ID，先回放错过的事件
        if last_event_id and _event_logger is not None:
            try:
                missed = await _event_logger.query(
                    after_event_id=last_event_id, limit=100
                )
                for event in missed:
                    yield _format_sse(event)
            except Exception as exc:
                logger.warning("SSE 回放失败: %s", exc)

        # 订阅新事件
        if _dispatcher is None:
            # 没有 Dispatcher，仅发 keep-alive
            while True:
                if await request.is_disconnected():
                    break
                yield ": keepalive\n\n"
                await asyncio.sleep(30)
            return

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        _dispatcher.subscribe_all(queue)

        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield _format_sse(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _dispatcher.unsubscribe_all(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
