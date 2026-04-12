"""Agent 调度器：Brain 和 Agent 之间的桥梁。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from .agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentCommandPriority,
    AgentEmit,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
)

if TYPE_CHECKING:
    from .agent_registry import AgentRegistry
    from .task_runtime import TaskRuntime

logger = logging.getLogger("lapwing.agent_dispatcher")


class AgentDispatcher:
    """Agent 调度器。职责：接收 Brain 的委派请求，选择 Agent，执行，收集结果。"""

    def __init__(
        self,
        registry: AgentRegistry,
        task_runtime: TaskRuntime,
        on_progress: Callable[[str, AgentEmit], Awaitable[None]] | None = None,
        on_result: Callable[[str, AgentNotify], Awaitable[None]] | None = None,
    ):
        self.registry = registry
        self.task_runtime = task_runtime
        self._on_progress = on_progress
        self._on_result = on_result

    async def dispatch(
        self,
        task_description: str,
        target_agent: str | None = None,
        priority: AgentCommandPriority = AgentCommandPriority.NORMAL,
        context: dict | None = None,
        chat_id: str | None = None,
        max_steps: int = 20,
        timeout: float = 300,
    ) -> AgentNotify | None:
        # 选择 Agent
        if target_agent:
            reg = self.registry.get(target_agent)
            if not reg:
                return AgentNotify(
                    agent_name=target_agent,
                    kind=AgentNotifyKind.ERROR,
                    urgency=AgentUrgency.SOON,
                    headline=f"Agent '{target_agent}' not found",
                )
        else:
            reg = self.registry.find_best_for_task(task_description)
            if not reg:
                return AgentNotify(
                    agent_name="dispatcher",
                    kind=AgentNotifyKind.ERROR,
                    urgency=AgentUrgency.SOON,
                    headline="没有找到合适的 Agent 来执行这个任务",
                    detail=f"任务：{task_description}",
                )

        agent = reg.agent

        command = AgentCommand(
            target_agent=agent.name,
            intent=AgentCommandIntent.EXECUTE,
            task_description=task_description,
            priority=priority,
            context=context,
            max_steps=max_steps,
            timeout_seconds=timeout,
        )

        self.registry.set_status(agent.name, "busy", command.id)

        final_notify = None
        try:
            async for event in agent.execute(command, self.task_runtime):
                if isinstance(event, AgentEmit):
                    logger.debug("Agent '%s' emit: %s - %s", agent.name, event.state, event.note)
                    if self._on_progress and chat_id:
                        await self._on_progress(chat_id, event)
                elif isinstance(event, AgentNotify):
                    final_notify = event
                    if self._on_result and chat_id:
                        await self._on_result(chat_id, event)
        except Exception as e:
            logger.exception("Dispatcher error for agent '%s'", agent.name)
            final_notify = AgentNotify(
                agent_name=agent.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline=f"调度失败：{str(e)[:100]}",
                ref_command_id=command.id,
            )
        finally:
            self.registry.set_status(agent.name, "idle")

        return final_notify

    async def cancel_agent(self, agent_name: str) -> bool:
        reg = self.registry.get(agent_name)
        if reg and reg.agent:
            await reg.agent.cancel()
            self.registry.set_status(agent_name, "idle")
            return True
        return False

    def get_active_tasks(self) -> list[dict]:
        result = []
        for name, reg in self.registry._agents.items():
            if reg.status == "busy" and reg.current_command_id:
                result.append({
                    "agent_name": name,
                    "command_id": reg.current_command_id,
                    "status": reg.status,
                })
        return result
