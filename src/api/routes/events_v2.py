"""SSE 事件推送 — 桌面端实时事件流。

事件来源：:class:`src.core.dispatcher.Dispatcher` 的 ``subscribe_all``
回调。v2.0 Step 1 之前本路由同时做"断线重连 + 历史回放"，依赖
``EventLogger.query(after_event_id=...)``。Step 1 中 EventLogger 被撤除，
这条回放通道也随之失效——Step 2/4 会在 StateMutationLog 的派生
事件流上恢复。参见 cleanup_report_step1.md 的 Step 2 TODO。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("lapwing.api.routes.events_v2")

router = APIRouter(prefix="/api/v2", tags=["events-v2"])

_dispatcher = None


def init(dispatcher=None) -> None:
    global _dispatcher
    _dispatcher = dispatcher


def _format_sse(event) -> str:
    """格式化为 SSE 消息。"""
    data = json.dumps(
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "timestamp": event.timestamp.isoformat(),
            "actor": event.actor,
            "task_id": event.task_id,
            "payload": event.payload,
        },
        ensure_ascii=False,
    )
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/events")
async def event_stream(request: Request):
    """SSE 事件流。推送通过 Dispatcher 提交的所有事件。

    Last-Event-ID 断线重连不再支持（EventLogger 已撤除）。客户端仍可
    传入该 header，服务端会忽略并只推送新事件。
    """

    async def event_generator():
        if _dispatcher is None:
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
