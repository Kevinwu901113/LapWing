"""Agent 注册表。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseAgent

logger = logging.getLogger("lapwing.agents.registry")


class AgentRegistry:
    """Agent 注册表。"""

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}

    def register(self, name: str, agent: "BaseAgent"):
        self._agents[name] = agent
        logger.info("Agent '%s' 已注册", name)

    def get(self, name: str) -> "BaseAgent | None":
        return self._agents.get(name)

    def list_names(self) -> list[str]:
        return list(self._agents.keys())

    def list_specs(self) -> list[dict]:
        return [
            {"name": a.spec.name, "description": a.spec.description}
            for a in self._agents.values()
        ]
