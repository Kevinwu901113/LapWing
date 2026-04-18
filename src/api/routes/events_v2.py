"""SSE 事件推送 — 桌面端实时事件流。

事件来源：v2.0 Step 4 M5 起改为订阅 :class:`StateMutationLog`。先前
（Step 1-3 转型期）路由订阅的是 :class:`Dispatcher`，但 dispatcher 的
``message.*`` 事件本来就是 mutation_log 信息的二级镜像；改为直接订阅
``mutation_log`` 让 SSE 与持久化记录的真值保持一致，省掉了
"dispatcher 不发某事件就丢" 的盲区。

断线重连仍未实现（EventLogger 已撤除）。客户端可以传 Last-Event-ID，
服务端忽略并只推送新事件。Step 5+ 会用 mutation_log 的
``after_id`` 查询补回 history-replay。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("lapwing.api.routes.events_v2")

router = APIRouter(prefix="/api/v2", tags=["events-v2"])

# Set by server.py init() at FastAPI app construction time.
# Step 4 M5: SSE no longer subscribes to dispatcher — it taps the
# mutation_log directly. The dispatcher reference is kept around as
# None so the function signature stays compatible during the M5 transition;
# call sites can drop it once Step 5 lands.
_mutation_log = None


def init(mutation_log=None) -> None:
    global _mutation_log
    _mutation_log = mutation_log


def _format_sse_mutation(mutation) -> str:
    """Format a ``Mutation`` row as an SSE message.

    Field shape preserved for backwards compatibility with the desktop
    client: ``event_id`` (mutation row id), ``event_type``, ``timestamp``
    (ISO 8601), ``payload``. ``actor`` / ``task_id`` are no longer part
    of the payload — clients that depended on them must derive the same
    info from ``payload`` (most events embed it there).
    """
    from datetime import datetime, timezone

    ts_iso = datetime.fromtimestamp(mutation.timestamp, tz=timezone.utc).isoformat()
    data = json.dumps(
        {
            "event_id": mutation.id,
            "event_type": mutation.event_type,
            "timestamp": ts_iso,
            "iteration_id": mutation.iteration_id,
            "chat_id": mutation.chat_id,
            "payload": mutation.payload,
        },
        ensure_ascii=False,
    )
    return (
        f"id: {mutation.id}\n"
        f"event: {mutation.event_type}\n"
        f"data: {data}\n\n"
    )


@router.get("/events")
async def event_stream(request: Request):
    """SSE 事件流 — 来自 StateMutationLog 的所有 mutation。"""

    async def event_generator():
        if _mutation_log is None:
            while True:
                if await request.is_disconnected():
                    break
                yield ": keepalive\n\n"
                await asyncio.sleep(30)
            return

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        def _on_mutation(mutation):
            try:
                queue.put_nowait(mutation)
            except asyncio.QueueFull:
                # Slow client — drop the event. SSE-without-replay is
                # already best-effort; logging keeps the loss visible
                # without blocking the writer.
                logger.warning("SSE queue full — dropping mutation %d", mutation.id)

        _mutation_log.subscribe(_on_mutation)

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    mutation = await asyncio.wait_for(queue.get(), timeout=30)
                    yield _format_sse_mutation(mutation)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _mutation_log.unsubscribe(_on_mutation)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
