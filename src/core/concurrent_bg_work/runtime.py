from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from src.agents.types import AgentMessage
from src.core.concurrent_bg_work.event_bus import new_agent_event
from src.core.concurrent_bg_work.types import (
    AgentEventType,
    AgentNeedsInputPayload,
    AgentRuntimeCheckpoint,
    SalienceLevel,
    TaskStatus,
)

logger = logging.getLogger("lapwing.core.concurrent_bg_work.runtime")


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError()


@dataclass(slots=True)
class AgentRuntime:
    task_id: str
    agent_registry: object
    event_bus: object
    store: object
    spec_id: str
    chat_id: str
    owner_user_id: str
    objective: str
    expected_output: str | None
    services: dict
    cancellation_token: CancellationToken

    async def run(self) -> None:
        await self.store.update_status(self.task_id, TaskStatus.RUNNING)
        await self.event_bus.emit(new_agent_event(
            task_id=self.task_id,
            chat_id=self.chat_id,
            type=AgentEventType.AGENT_STARTED,
            summary=f"Started {self.spec_id}: {self.objective[:160]}",
            sequence=1,
        ))
        self.cancellation_token.raise_if_cancelled()
        agent = await self.agent_registry.get_or_create_instance(
            self.spec_id,
            services_override=self.services,
        )
        if agent is None:
            await self.event_bus.emit(new_agent_event(
                task_id=self.task_id,
                chat_id=self.chat_id,
                type=AgentEventType.AGENT_FAILED,
                summary=f"Agent spec '{self.spec_id}' is unavailable.",
                sequence=2,
                salience=SalienceLevel.HIGH,
            ))
            return
        content = self.objective
        if self.expected_output:
            content = f"{content}\n\nExpected output: {self.expected_output}"
        if self.services.get("checkpoint_answer") is not None:
            content = f"{content}\n\nLapwing supplied missing input: {self.services['checkpoint_answer']}"
        message = AgentMessage(
            from_agent="lapwing",
            to_agent=self.spec_id,
            task_id=self.task_id,
            content=content,
            context_digest="",
            message_type="request",
        )
        try:
            result = await agent.execute(message)
            self.cancellation_token.raise_if_cancelled()
        except asyncio.CancelledError:
            await self.event_bus.emit(new_agent_event(
                task_id=self.task_id,
                chat_id=self.chat_id,
                type=AgentEventType.AGENT_CANCELLED,
                summary=f"Cancelled {self.objective[:160]}",
                sequence=3,
                salience=SalienceLevel.HIGH,
            ))
            raise
        except Exception as exc:
            logger.exception("background agent runtime failed")
            await self.event_bus.emit(new_agent_event(
                task_id=self.task_id,
                chat_id=self.chat_id,
                type=AgentEventType.AGENT_FAILED,
                summary=f"Agent runtime failed: {exc}",
                sequence=3,
                salience=SalienceLevel.HIGH,
            ))
            return
        if getattr(result, "status", "") == "done":
            if await self._task_is_terminal():
                return
            await self.event_bus.emit(new_agent_event(
                task_id=self.task_id,
                chat_id=self.chat_id,
                type=AgentEventType.AGENT_COMPLETED,
                summary=(getattr(result, "result", "") or "Task completed.")[:500],
                sequence=3,
            ))
        elif getattr(result, "status", "") in {"needs_input", "waiting_input"}:
            await self._checkpoint_needs_input(result)
        else:
            if await self._task_is_terminal():
                return
            reason = getattr(result, "reason", None) or getattr(result, "error_detail", None) or "Task failed."
            await self.event_bus.emit(new_agent_event(
                task_id=self.task_id,
                chat_id=self.chat_id,
                type=AgentEventType.AGENT_FAILED,
                summary=str(reason)[:500],
                sequence=3,
                salience=SalienceLevel.HIGH,
            ))

    async def _task_is_terminal(self) -> bool:
        record = await self.store.read(self.task_id)
        return bool(record and record.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.WAITING_INPUT,
        })

    async def _checkpoint_needs_input(self, result) -> None:
        structured = getattr(result, "structured_result", None)
        data = structured if isinstance(structured, dict) else {}
        question = (
            data.get("question_for_lapwing")
            or data.get("question")
            or getattr(result, "reason", None)
            or getattr(result, "result", None)
            or "Background agent needs input."
        )
        timeout_at = data.get("timeout_at")
        if isinstance(timeout_at, str):
            try:
                timeout_at = datetime.fromisoformat(timeout_at)
            except ValueError:
                timeout_at = None
        payload = AgentNeedsInputPayload(
            question_for_lapwing=str(question),
            question_for_owner=data.get("question_for_owner"),
            expected_answer_shape=data.get("expected_answer_shape"),
            blocking=bool(data.get("blocking", True)),
            timeout_at=timeout_at,
        )
        checkpoint = AgentRuntimeCheckpoint(
            checkpoint_id=f"checkpoint_{self.task_id}_{uuid.uuid4().hex[:8]}",
            task_id=self.task_id,
            created_at=datetime.now(timezone.utc),
            conversation_state={
                "objective": self.objective,
                "expected_output": self.expected_output,
            },
            scratchpad_summary=str(getattr(result, "result", "") or "")[:2000],
            pending_question=payload,
            tool_context={},
            workspace_snapshot_ref=None,
            rounds_consumed=int(data.get("rounds_consumed") or 0),
        )
        await self.store.save_checkpoint(checkpoint)
        await self.event_bus.emit(new_agent_event(
            task_id=self.task_id,
            chat_id=self.chat_id,
            type=AgentEventType.AGENT_NEEDS_INPUT,
            summary=payload.question_for_lapwing[:500],
            sequence=3,
            payload={
                "question_for_lapwing": payload.question_for_lapwing,
                "question_for_owner": payload.question_for_owner,
                "expected_answer_shape": payload.expected_answer_shape,
                "blocking": payload.blocking,
                "timeout_at": payload.timeout_at.isoformat() if payload.timeout_at else None,
                "checkpoint_id": checkpoint.checkpoint_id,
            },
            salience=SalienceLevel.HIGH,
        ))
