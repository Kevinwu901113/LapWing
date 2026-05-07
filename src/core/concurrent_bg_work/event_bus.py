from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.events import Event, PRIORITY_TOOL_COMPLETE, PRIORITY_AGENT_URGENT, PRIORITY_INNER_TICK
from src.core.concurrent_bg_work.types import (
    AgentEvent,
    AgentEventType,
    AgentNeedsInputPayload,
    AgentTaskSnapshot,
    NotifyPolicy,
    SalienceLevel,
    TaskStatus,
)
from src.logging.state_mutation_log import MutationType

logger = logging.getLogger("lapwing.core.concurrent_bg_work.event_bus")

CRITICAL_ALLOWED_TYPES = {
    AgentEventType.AGENT_FAILED,
    AgentEventType.AGENT_BUDGET_EXHAUSTED,
    AgentEventType.AGENT_NEEDS_INPUT,
}

SILENT_OVERRIDE_TYPES = {
    AgentEventType.AGENT_FAILED,
    AgentEventType.AGENT_BUDGET_EXHAUSTED,
    AgentEventType.AGENT_NEEDS_INPUT,
}


@dataclass(frozen=True, slots=True)
class AgentTaskResultEvent(Event):
    task_id: str = ""
    task_snapshot: AgentTaskSnapshot | None = None
    triggering_event: AgentEvent | None = None
    effective_salience: SalienceLevel = SalienceLevel.NORMAL
    priority: int = PRIORITY_TOOL_COMPLETE
    kind: str = "agent_task_result"


@dataclass(frozen=True, slots=True)
class AgentNeedsInputEvent(Event):
    task_id: str = ""
    payload: AgentNeedsInputPayload | None = None
    timeout_at: datetime | None = None
    triggering_event: AgentEvent | None = None
    effective_salience: SalienceLevel = SalienceLevel.HIGH
    priority: int = PRIORITY_TOOL_COMPLETE
    kind: str = "agent_needs_input"


@dataclass(frozen=True, slots=True)
class AgentProgressUrgencyEvent(Event):
    task_id: str = ""
    summary: str = ""
    elapsed_seconds: float = 0.0
    triggering_event: AgentEvent | None = None
    effective_salience: SalienceLevel = SalienceLevel.NORMAL
    priority: int = PRIORITY_TOOL_COMPLETE
    kind: str = "agent_progress_urgency"


class AgentEventBus:
    def __init__(
        self,
        *,
        task_store,
        mutation_log=None,
        desktop_sink=None,
        cognitive_sink=None,
        event_queue=None,
    ) -> None:
        self._task_store = task_store
        self._mutation_log = mutation_log
        self._desktop_sink = desktop_sink
        self._cognitive_sink = cognitive_sink
        self._event_queue = event_queue
        self._last_progress_push: dict[str, datetime] = {}

    async def emit(self, event: AgentEvent) -> None:
        event = self._validate_and_normalize_salience(event)
        await self._task_store.append_event(event)
        task = await self._task_store.read(event.task_id)
        if task is None:
            return
        effective = event.salience or task.salience
        await self._record_mutation(event, task)
        await self._send_desktop(event, task)
        if self._should_notify_cognitive(event, task, effective):
            await self._push_cognitive(event, effective)

    def _validate_and_normalize_salience(self, event: AgentEvent) -> AgentEvent:
        if event.salience == SalienceLevel.CRITICAL and event.type not in CRITICAL_ALLOWED_TYPES:
            logger.warning("critical_misuse: %s", event)
            event.salience = SalienceLevel.HIGH
        return event

    async def _record_mutation(self, event: AgentEvent, task) -> None:
        if self._mutation_log is None:
            return
        mapping = {
            AgentEventType.AGENT_STARTED: MutationType.AGENT_STARTED,
            AgentEventType.AGENT_COMPLETED: MutationType.AGENT_COMPLETED,
            AgentEventType.AGENT_FAILED: MutationType.AGENT_FAILED,
            AgentEventType.AGENT_TOOL_CALL: MutationType.AGENT_TOOL_CALL,
            AgentEventType.AGENT_BUDGET_EXHAUSTED: MutationType.AGENT_BUDGET_EXHAUSTED,
            AgentEventType.AGENT_CANCELLED: MutationType.AGENT_CANCELLED,
            AgentEventType.AGENT_NEEDS_INPUT: MutationType.AGENT_NEEDS_INPUT,
        }
        mutation_type = mapping.get(event.type)
        if mutation_type is None:
            return
        try:
            await self._mutation_log.record(mutation_type, event.payload, chat_id=event.chat_id)
        except Exception:
            logger.warning("agent event mutation emit failed", exc_info=True)

    async def _send_desktop(self, event: AgentEvent, task) -> None:
        if self._desktop_sink is None:
            return
        result = self._desktop_sink.send(event, task)
        if hasattr(result, "__await__"):
            await result

    def _should_notify_cognitive(self, event: AgentEvent, task, effective: SalienceLevel) -> bool:
        if task.notify_policy == NotifyPolicy.SILENT:
            return event.type in SILENT_OVERRIDE_TYPES
        if event.type in {
            AgentEventType.AGENT_COMPLETED,
            AgentEventType.AGENT_FAILED,
            AgentEventType.AGENT_BUDGET_EXHAUSTED,
            AgentEventType.AGENT_NEEDS_INPUT,
        }:
            return True
        if event.type == AgentEventType.AGENT_PROGRESS_SUMMARY:
            last = self._last_progress_push.get(event.task_id)
            now = event.occurred_at
            if last is None or (now - last).total_seconds() > 30:
                self._last_progress_push[event.task_id] = now
                return True
        return False

    async def _push_cognitive(self, event: AgentEvent, effective: SalienceLevel) -> None:
        if self._cognitive_sink is not None:
            result = self._cognitive_sink.push(event, effective)
            if hasattr(result, "__await__"):
                await result
            return
        if self._event_queue is None:
            return
        if event.type == AgentEventType.AGENT_PROGRESS_SUMMARY:
            await self._event_queue.put(AgentProgressUrgencyEvent(
                task_id=event.task_id,
                summary=event.summary_for_lapwing,
                elapsed_seconds=float(event.payload.get("elapsed_seconds") or 0.0),
                triggering_event=event,
                effective_salience=effective,
            ))
            return

        if event.type == AgentEventType.AGENT_NEEDS_INPUT:
            payload = _needs_input_payload_from_event(event)
            await self._event_queue.put(AgentNeedsInputEvent(
                task_id=event.task_id,
                payload=payload,
                timeout_at=payload.timeout_at,
                triggering_event=event,
                effective_salience=effective,
                priority=PRIORITY_AGENT_URGENT,
            ))
            return

        if event.type in {AgentEventType.AGENT_FAILED, AgentEventType.AGENT_BUDGET_EXHAUSTED}:
            prio = PRIORITY_AGENT_URGENT
        elif event.type == AgentEventType.AGENT_CANCELLED:
            prio = PRIORITY_INNER_TICK
        else:
            prio = PRIORITY_INNER_TICK

        snapshot = await self._snapshot_for_event(event)
        if snapshot is not None:
            await self._event_queue.put(AgentTaskResultEvent(
                task_id=event.task_id,
                task_snapshot=snapshot,
                triggering_event=event,
                effective_salience=effective,
                priority=prio,
            ))

    async def _snapshot_for_event(self, event: AgentEvent) -> AgentTaskSnapshot | None:
        snapshots = await self._task_store.list_tasks(
            chat_id=event.chat_id,
            statuses=[
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_RESOURCE,
                TaskStatus.WAITING_INPUT,
                TaskStatus.RESUMING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ],
            include_recently_completed=True,
            limit=50,
        )
        for snapshot in snapshots:
            if snapshot.task_id == event.task_id:
                return snapshot
        return snapshots[0] if snapshots else None


def new_agent_event(
    *,
    task_id: str,
    chat_id: str,
    type: AgentEventType,
    summary: str,
    sequence: int,
    payload: dict[str, Any] | None = None,
    salience: SalienceLevel | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=f"agent_evt_{task_id}_{sequence}",
        task_id=task_id,
        chat_id=chat_id,
        type=type,
        occurred_at=datetime.now(timezone.utc),
        summary_for_lapwing=summary,
        summary_for_owner=None,
        raw_payload_ref=None,
        salience=salience,
        payload=payload or {},
        sequence_in_task=sequence,
    )


def _needs_input_payload_from_event(event: AgentEvent) -> AgentNeedsInputPayload:
    raw = event.payload or {}
    timeout_raw = raw.get("timeout_at")
    timeout_at = None
    if isinstance(timeout_raw, datetime):
        timeout_at = timeout_raw
    elif timeout_raw:
        try:
            timeout_at = datetime.fromisoformat(str(timeout_raw))
        except ValueError:
            timeout_at = None
    return AgentNeedsInputPayload(
        question_for_lapwing=str(
            raw.get("question_for_lapwing")
            or raw.get("question")
            or event.summary_for_lapwing
        ),
        question_for_owner=raw.get("question_for_owner"),
        expected_answer_shape=raw.get("expected_answer_shape"),
        blocking=bool(raw.get("blocking", True)),
        timeout_at=timeout_at,
    )
