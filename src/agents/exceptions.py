"""Agent runtime exceptions."""

from __future__ import annotations

from collections.abc import Iterable


class AgentSpawnError(RuntimeError):
    """Raised when an agent cannot be created with the supplied services."""

    def __init__(self, agent_name: str, missing_services: Iterable[str]):
        self.agent_name = agent_name
        self.missing_services = tuple(missing_services)
        missing = ", ".join(self.missing_services)
        super().__init__(
            f"agent_services_unavailable for {agent_name}: {missing}"
        )
