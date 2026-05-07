from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.agents.types import AgentMessage
from src.core.concurrent_bg_work.event_bus import new_agent_event
from src.core.concurrent_bg_work.types import AgentEventType, SalienceLevel, TaskStatus

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
            await self.event_bus.emit(new_agent_event(
                task_id=self.task_id,
                chat_id=self.chat_id,
                type=AgentEventType.AGENT_COMPLETED,
                summary=(getattr(result, "result", "") or "Task completed.")[:500],
                sequence=3,
            ))
        else:
            reason = getattr(result, "reason", None) or getattr(result, "error_detail", None) or "Task failed."
            await self.event_bus.emit(new_agent_event(
                task_id=self.task_id,
                chat_id=self.chat_id,
                type=AgentEventType.AGENT_FAILED,
                summary=str(reason)[:500],
                sequence=3,
                salience=SalienceLevel.HIGH,
            ))
