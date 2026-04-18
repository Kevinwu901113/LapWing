"""Dispatcher — 内存 pub/sub 事件总线，给 SSE / 订阅者做实时广播。

v2.0 Step 1 起：本模块不再负责持久化。所有"状态变更"记录职责已迁移到
``src.logging.state_mutation_log.StateMutationLog``。Dispatcher 保留
仅因为桌面端 SSE（``/api/v2/events``）和部分子系统（consciousness /
reminder / agent.*）仍把它当作实时事件广播的唯一通道；这些事件类型
逐步迁移到 StateMutationLog 为源头、Dispatcher 为派生的模型——见
cleanup_report_step1.md 的 debt 清单。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("lapwing.core.dispatcher")


@dataclass
class Event:
    """消息总线上流动的事件对象。仅用于运行时广播，不再持久化。"""

    event_id: str
    timestamp: datetime
    event_type: str
    actor: str
    task_id: str | None
    source: str
    trust_level: str
    correlation_id: str
    payload: dict = field(default_factory=dict)


class Dispatcher:
    """纯内存 pub/sub。串行处理保证事件顺序。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
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
            event = self._make_event(
                event_type,
                actor=actor,
                task_id=task_id,
                source=source,
                trust_level=trust_level,
                correlation_id=correlation_id,
                payload=payload,
            )
            await self._notify(event)
            return event.event_id

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """订阅特定事件类型。"""
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
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("事件处理器异常 (%s): %s", event.event_type, exc)

        for queue in list(self._global_queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @staticmethod
    def _make_event(
        event_type: str,
        *,
        actor: str = "system",
        task_id: str | None = None,
        source: str = "",
        trust_level: str = "",
        correlation_id: str | None = None,
        payload: dict | None = None,
    ) -> Event:
        event_id = uuid.uuid4().hex[:16]
        return Event(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            actor=actor,
            task_id=task_id,
            source=source,
            trust_level=trust_level,
            correlation_id=correlation_id or event_id,
            payload=payload or {},
        )
