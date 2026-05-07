from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from src.core.concurrent_bg_work.types import CognitiveStateView, SalienceLevel, TaskStatus


class TurnBatcher:
    DEFAULT_WINDOWS_MS = {
        "ordinary_user_message": 1800,
        "agent_result_burst": 700,
        "owner_message": 250,
        "operator_emergency": 0,
        "agent_progress_urgency": 500,
        "inner_tick": 1000,
    }
    HIGH_SALIENCE_FLUSH_CAP_MS = 250
    USER_DEBOUNCE_MAX_EXTENSIONS = 3

    def __init__(self, queue, task_store=None):
        self._queue = queue
        self._task_store = task_store

    async def batch_for_first_event(self, first_event) -> list[Any]:
        window = self._window_for(first_event)
        if window == 0:
            return [first_event]
        start = _monotonic_ms()
        deadline = start + window
        batch = [first_event]
        extensions = 0
        while _monotonic_ms() < deadline:
            if self._queue_has_high_salience_event():
                deadline = min(deadline, start + self.HIGH_SALIENCE_FLUSH_CAP_MS)
            remaining = max(0, deadline - _monotonic_ms())
            if remaining <= 0:
                break
            try:
                await asyncio.sleep(min(remaining / 1000.0, 0.01))
            except asyncio.CancelledError:
                raise
            next_event = self._queue.get_nowait()
            if next_event is None:
                continue
            if (
                _is_ordinary_message(next_event)
                and _same_chat_owner(next_event, batch)
                and extensions < self.USER_DEBOUNCE_MAX_EXTENSIONS
                and not _batch_has_high_salience(batch)
            ):
                deadline = _monotonic_ms() + self.DEFAULT_WINDOWS_MS["ordinary_user_message"]
                extensions += 1
            batch.append(next_event)
            if _batch_has_high_salience(batch):
                deadline = min(deadline, _monotonic_ms() + 100)
        return batch

    async def build_state_view(self, *, batch: list[Any], turn_id: str, chat_id: str | None = None) -> CognitiveStateView:
        in_flight = []
        recent = []
        recovery_notice = None
        if self._task_store is not None:
            in_flight = await self._task_store.list_tasks(
                chat_id=chat_id,
                statuses=[
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                    TaskStatus.WAITING_RESOURCE,
                    TaskStatus.WAITING_INPUT,
                    TaskStatus.RESUMING,
                ],
                limit=20,
            )
            recent = await self._task_store.list_tasks(
                chat_id=chat_id,
                statuses=[TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED],
                include_recently_completed=True,
                limit=10,
            )
            recovery_notice = self._task_store.pending_recovery_notice
        return CognitiveStateView(
            chat_id=chat_id,
            turn_id=turn_id,
            snapshot_at=datetime.now(timezone.utc),
            pending_events=batch,
            in_flight_tasks=in_flight,
            recently_completed=recent,
            recovery_notice=recovery_notice,
            busy_hint=_busy_hint(in_flight),
        )

    def _window_for(self, event) -> int:
        if getattr(event, "kind", "") == "owner_message":
            return self.DEFAULT_WINDOWS_MS["owner_message"]
        if getattr(event, "kind", "") == "agent_task_result":
            return self.DEFAULT_WINDOWS_MS["agent_result_burst"]
        if getattr(event, "kind", "") == "inner_tick":
            return self.DEFAULT_WINDOWS_MS["inner_tick"]
        if _is_high_salience(event):
            return self.HIGH_SALIENCE_FLUSH_CAP_MS
        return self.DEFAULT_WINDOWS_MS["ordinary_user_message"]

    def _queue_has_high_salience_event(self) -> bool:
        helper = getattr(self._queue, "has_high_salience_event", None)
        if helper is not None:
            return bool(helper())
        return False


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _is_ordinary_message(event) -> bool:
    return getattr(event, "kind", "") == "user_message"


def _same_chat_owner(event, batch: list[Any]) -> bool:
    chat = getattr(event, "chat_id", None)
    user = getattr(event, "user_id", None)
    return all(getattr(item, "chat_id", chat) == chat and getattr(item, "user_id", user) == user for item in batch)


def _is_high_salience(event) -> bool:
    salience = getattr(event, "effective_salience", None) or getattr(event, "salience", None)
    if isinstance(salience, SalienceLevel):
        return salience in {SalienceLevel.HIGH, SalienceLevel.CRITICAL}
    return str(salience).lower() in {"high", "critical"}


def _batch_has_high_salience(batch: list[Any]) -> bool:
    return any(_is_high_salience(item) for item in batch)


def _busy_hint(in_flight: list[Any]) -> str:
    if not in_flight:
        return "idle"
    if any(getattr(item, "status", None) in {TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.RESUMING} for item in in_flight):
        return "waiting_on_background"
    return "idle"
