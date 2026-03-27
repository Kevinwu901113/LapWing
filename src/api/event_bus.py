"""桌面端事件总线。"""

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("lapwing.api.event_bus")


class DesktopEventBus:
    """为桌面端 SSE 提供本地事件发布能力。"""

    def __init__(self, latency_monitor: Any | None = None) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._listeners: list = []
        self._lock = asyncio.Lock()
        self._latency_monitor = latency_monitor

    def add_listener(self, listener) -> None:
        self._listeners.append(listener)

    def set_latency_monitor(self, latency_monitor: Any | None) -> None:
        self._latency_monitor = latency_monitor

    async def publish(self, event_type: str, payload: dict) -> None:
        event = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        if self._latency_monitor is not None:
            try:
                self._latency_monitor.record_event_published(event)
            except Exception as exc:
                logger.warning("记录事件发布时间失败: %s", exc)

        async with self._lock:
            subscribers = list(self._subscribers)
            listeners = list(self._listeners)

        for listener in listeners:
            try:
                result = listener(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.warning("事件监听器执行失败: %s", exc)

        for queue in subscribers:
            queue.put_nowait(event)

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
