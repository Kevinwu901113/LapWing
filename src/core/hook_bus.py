"""Internal in-process hook bus.

This is not a plugin system. Hooks are local observers only: they receive
events, may record telemetry or proposals, and cannot execute tools, enqueue
around EventQueue, or grant permissions.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

logger = logging.getLogger("lapwing.core.hook_bus")

HookName = Literal[
    "on_tool_call_validate",
    "on_tool_result",
    "on_task_end",
    "on_memory_write_propose",
    "on_capability_propose",
]


@dataclass(frozen=True, slots=True)
class HookEvent:
    name: HookName
    payload: dict[str, Any] = field(default_factory=dict)


class InternalHookBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[HookEvent], Any]]] = defaultdict(list)

    def subscribe(self, name: HookName, callback: Callable[[HookEvent], Any]) -> None:
        if callback not in self._subscribers[name]:
            self._subscribers[name].append(callback)

    def unsubscribe(self, name: HookName, callback: Callable[[HookEvent], Any]) -> None:
        try:
            self._subscribers[name].remove(callback)
        except (KeyError, ValueError):
            pass

    async def emit(self, name: HookName, payload: dict[str, Any] | None = None) -> None:
        event = HookEvent(name=name, payload=dict(payload or {}))
        for callback in list(self._subscribers.get(name, ())):
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.debug("internal hook %s failed", name, exc_info=True)
