"""任务读模型：从 task.* 事件构建统一任务视图。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_TASK_STATUS_BY_EVENT = {
    "task.started": "started",
    "task.executing": "executing",
    "task.verifying": "verifying",
    "task.completed": "completed",
    "task.failed": "failed",
    "task.blocked": "blocked",
}
_FINAL_STATUSES = {"completed", "failed", "blocked"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)

    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class TaskRecord:
    task_id: str
    chat_id: str
    status: str
    phase: str
    text: str
    tool_name: str | None = None
    round: int | None = None
    command: str | None = None
    reason: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    failed_at: str | None = None
    blocked_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


class TaskViewStore:
    """维护任务投影（in-memory）。"""

    def __init__(self, max_events_per_task: int = 100) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()
        self._max_events_per_task = max_events_per_task

    async def ingest_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        status = _TASK_STATUS_BY_EVENT.get(event_type)
        if status is None:
            return

        payload = event.get("payload")
        if not isinstance(payload, dict):
            return

        task_id = str(payload.get("task_id", "")).strip()
        chat_id = str(payload.get("chat_id", "")).strip()
        if not task_id or not chat_id:
            return

        timestamp = str(event.get("timestamp") or _now_iso())
        phase = str(payload.get("phase", "")).strip() or status
        text = str(payload.get("text", "")).strip()
        tool_name = payload.get("tool_name")
        round_raw = payload.get("round")
        command = payload.get("command")
        reason = payload.get("reason")

        event_item = {
            "type": event_type,
            "timestamp": timestamp,
            "phase": phase,
            "text": text,
            "tool_name": tool_name,
            "round": round_raw,
            "command": command,
            "reason": reason,
        }

        async with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                record = TaskRecord(
                    task_id=task_id,
                    chat_id=chat_id,
                    status=status,
                    phase=phase,
                    text=text,
                    started_at=timestamp if status == "started" else None,
                    updated_at=timestamp,
                )
                self._tasks[task_id] = record

            record.chat_id = chat_id or record.chat_id
            record.phase = phase
            record.text = text or record.text
            record.tool_name = str(tool_name) if tool_name is not None else record.tool_name
            if isinstance(round_raw, int):
                record.round = round_raw
            elif isinstance(round_raw, str) and round_raw.isdigit():
                record.round = int(round_raw)
            record.command = str(command) if command is not None else record.command
            record.reason = str(reason) if reason is not None else record.reason

            # 乱序容错：终态不被非终态覆盖
            if not (record.status in _FINAL_STATUSES and status not in _FINAL_STATUSES):
                record.status = status

            if status == "started" and record.started_at is None:
                record.started_at = timestamp
            if status == "completed":
                record.completed_at = timestamp
            if status == "failed":
                record.failed_at = timestamp
            if status == "blocked":
                record.blocked_at = timestamp

            if (
                record.updated_at is None
                or _parse_timestamp(timestamp) >= _parse_timestamp(record.updated_at)
            ):
                record.updated_at = timestamp

            record.events.append(event_item)
            if len(record.events) > self._max_events_per_task:
                record.events = record.events[-self._max_events_per_task:]

    async def list_tasks(
        self,
        *,
        chat_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            records = list(self._tasks.values())

        if chat_id:
            records = [record for record in records if record.chat_id == chat_id]
        if status:
            records = [record for record in records if record.status == status]

        records.sort(
            key=lambda record: _parse_timestamp(record.updated_at),
            reverse=True,
        )

        capped = records[: max(limit, 1)]
        return [self._to_summary(record) for record in capped]

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            return self._to_detail(record)

    def _to_summary(self, record: TaskRecord) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "chat_id": record.chat_id,
            "status": record.status,
            "phase": record.phase,
            "text": record.text,
            "tool_name": record.tool_name,
            "round": record.round,
            "command": record.command,
            "reason": record.reason,
            "started_at": record.started_at,
            "updated_at": record.updated_at,
            "completed_at": record.completed_at,
            "failed_at": record.failed_at,
            "blocked_at": record.blocked_at,
        }

    def _to_detail(self, record: TaskRecord) -> dict[str, Any]:
        detail = self._to_summary(record)
        detail["events"] = list(record.events)
        return detail
