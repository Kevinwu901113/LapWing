"""Agent 注册表：管理可用 Agent 及其能力。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_base import BaseAgent

logger = logging.getLogger("lapwing.agent_registry")


@dataclass
class AgentCapability:
    """Agent 能力声明。"""
    name: str
    description: str
    tools_required: list[str]


@dataclass
class AgentRegistration:
    """Agent 注册信息。"""
    agent: BaseAgent
    capabilities: list[AgentCapability]
    status: str = "idle"
    current_command_id: str | None = None
    error_count: int = 0
    max_consecutive_errors: int = 3


class AgentRegistry:
    """Agent 注册表。"""

    def __init__(self):
        self._agents: dict[str, AgentRegistration] = {}

    def register(self, agent: BaseAgent, capabilities: list[AgentCapability]) -> None:
        if agent.name in self._agents:
            logger.warning("Agent '%s' already registered, replacing", agent.name)
        self._agents[agent.name] = AgentRegistration(agent=agent, capabilities=capabilities)
        logger.info("Registered agent '%s' with %d capabilities", agent.name, len(capabilities))

    def unregister(self, name: str) -> None:
        if name in self._agents:
            del self._agents[name]
            logger.info("Unregistered agent '%s'", name)

    def get(self, name: str) -> AgentRegistration | None:
        return self._agents.get(name)

    def find_by_capability(self, capability_name: str) -> list[AgentRegistration]:
        results = []
        for reg in self._agents.values():
            if reg.status == "disabled":
                continue
            for cap in reg.capabilities:
                if cap.name == capability_name:
                    results.append(reg)
                    break
        return results

    def find_best_for_task(
        self, task_description: str, required_tools: list[str] | None = None,
    ) -> AgentRegistration | None:
        candidates = []
        for reg in self._agents.values():
            if reg.status in ("disabled", "error"):
                continue
            if required_tools:
                agent_tools = set()
                for cap in reg.capabilities:
                    agent_tools.update(cap.tools_required)
                if not set(required_tools).issubset(agent_tools):
                    continue
            candidates.append(reg)
        if not candidates:
            return None
        idle = [c for c in candidates if c.status == "idle"]
        return idle[0] if idle else candidates[0]

    def set_status(self, name: str, status: str, command_id: str | None = None) -> None:
        reg = self._agents.get(name)
        if reg:
            reg.status = status
            reg.current_command_id = command_id

    def list_agents(self) -> list[dict]:
        result = []
        for name, reg in self._agents.items():
            result.append({
                "name": name,
                "status": reg.status,
                "capabilities": [c.name for c in reg.capabilities],
                "current_command_id": reg.current_command_id,
            })
        return result

    @property
    def available_count(self) -> int:
        return sum(1 for r in self._agents.values() if r.status not in ("disabled", "error"))
