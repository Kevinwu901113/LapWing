"""Agent 基类：所有子 Agent 继承此类。

每个 Agent 是 Brain 内的协程（不是独立进程），
通过 AgentCommand 接收指令，通过 yield AgentEmit 报告状态，
完成后发送 AgentNotify。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncGenerator

from .agent_protocol import (
    AgentCommand,
    AgentEmit,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
    EmitCallback,
)

if TYPE_CHECKING:
    from .task_runtime import TaskRuntime

logger = logging.getLogger("lapwing.agent_base")


class BaseAgent(ABC):
    """子 Agent 基类。"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._cancel_requested = False

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        ...

    @abstractmethod
    async def _execute_task(
        self,
        command: AgentCommand,
        task_runtime: TaskRuntime,
        emit: EmitCallback,
    ) -> AgentNotify:
        ...

    async def execute(
        self,
        command: AgentCommand,
        task_runtime: TaskRuntime,
    ) -> AsyncGenerator[AgentEmit | AgentNotify, None]:
        """执行指令，yield 状态更新和最终结果。"""
        self._cancel_requested = False
        emits: list[AgentEmit] = []

        def emit_callback(
            state: AgentEmitState,
            note: str | None = None,
            progress: float | None = None,
            payload: dict | None = None,
        ):
            emits.append(AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=state,
                progress=progress,
                note=note,
                payload=payload,
            ))

        yield AgentEmit(
            agent_name=self.name,
            ref_id=command.id,
            state=AgentEmitState.QUEUED,
            note=f"Task received: {command.task_description[:100]}",
        )

        yield AgentEmit(
            agent_name=self.name,
            ref_id=command.id,
            state=AgentEmitState.WORKING,
        )

        try:
            notify = await asyncio.wait_for(
                self._execute_task(command, task_runtime, emit_callback),
                timeout=command.timeout_seconds,
            )

            for e in emits:
                yield e

            yield notify

        except asyncio.TimeoutError:
            yield AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=AgentEmitState.FAILED,
                note=f"Task timed out after {command.timeout_seconds}s",
            )
            yield AgentNotify(
                agent_name=self.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline=f"任务超时：{command.task_description[:50]}",
                detail=f"超过 {command.timeout_seconds} 秒未完成",
                ref_command_id=command.id,
            )

        except asyncio.CancelledError:
            yield AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=AgentEmitState.CANCELLED,
                note="Task cancelled",
            )

        except Exception as e:
            logger.exception("Agent '%s' task failed", self.name)
            yield AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=AgentEmitState.FAILED,
                note=str(e),
            )
            yield AgentNotify(
                agent_name=self.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline=f"任务失败：{command.task_description[:50]}",
                detail=str(e),
                ref_command_id=command.id,
            )

    async def cancel(self) -> None:
        """请求取消。设置标志供 _execute_task 轮询检查。"""
        self._cancel_requested = True

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel_requested
