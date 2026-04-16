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
        """返回除 team_lead 外的 Agent 名称（供 Team Lead prompt 参考）。"""
        return [n for n in self._agents if n != "team_lead"]

    def list_specs(self) -> list[dict]:
        """返回除 team_lead 外的 Agent 描述。"""
        return [
            {"name": a.spec.name, "description": a.spec.description}
            for n, a in self._agents.items()
            if n != "team_lead"
        ]
