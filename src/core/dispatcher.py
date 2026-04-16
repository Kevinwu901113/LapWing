"""Dispatcher — 所有状态变更的唯一入口。

串行处理，防止并发状态冲突。Phase 1 新基础设施。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable, Any

from src.core.event_logger_v2 import Event, EventLogger

logger = logging.getLogger("lapwing.core.dispatcher")


class Dispatcher:
    """所有状态变更的唯一入口。串行处理。"""

    def __init__(self, event_logger: EventLogger):
        self._lock = asyncio.Lock()
        self._event_logger = event_logger
        self._handlers: dict[str, list[Callable]] = {}
        self._global_queues: list[asyncio.Queue] = []

    async def submit(
        self,
        event_type: str,
        payload: dict,
        *,
        actor: str = "system",
        task_id: str | None = None,
        source: str = "",
        trust_level: str = "",
        correlation_id: str | None = None,
    ) -> str:
        """提交一个事件。串行执行。返回 event_id。"""
        async with self._lock:
            event = self._event_logger.make_event(
                event_type,
                actor=actor,
                task_id=task_id,
                source=source,
                trust_level=trust_level,
                correlation_id=correlation_id,
                payload=payload,
            )

            # 持久化
            await self._event_logger.log(event)

            # 通知订阅者
            await self._notify(event)

            return event.event_id

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """订阅事件类型。"""
        self._handlers.setdefault(event_type, []).append(handler)

    def subscribe_all(self, queue: asyncio.Queue) -> None:
        """SSE 用：订阅所有事件。"""
        self._global_queues.append(queue)

    def unsubscribe_all(self, queue: asyncio.Queue) -> None:
        """取消全局订阅。"""
        try:
            self._global_queues.remove(queue)
        except ValueError:
            pass

    async def _notify(self, event: Event) -> None:
        """通知所有订阅者（类型订阅 + 全局订阅）。"""
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("事件处理器异常 (%s): %s", event.event_type, exc)

        # 全局订阅（SSE）
        for queue in list(self._global_queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
