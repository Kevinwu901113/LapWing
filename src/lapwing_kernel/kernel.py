"""Kernel — composition root only. No business logic.

Kernel is NOT Lapwing. Kernel is the OS-like substrate Lapwing runs on. If
this class exceeds ~150 lines or contains business logic, the design has
failed.

See docs/architecture/lapwing_v1_blueprint.md §4.1.
"""
from __future__ import annotations

from typing import Any

from .identity import ResidentIdentity
from .pipeline.executor import (
    ActionExecutor,
    EventLogProtocol,
    InterruptStoreProtocol,
    RedactorProtocol,
)
from .pipeline.registry import ResourceRegistry
from .policy import PolicyDecider
from .primitives.action import Action
from .primitives.observation import Observation


class Kernel:
    """Composition root only. No business logic.

    See blueprint §4.1: kernel.py is ≤150 lines and does only wiring.

    Forbidden here:
      - browser business logic
      - credential business logic
      - resume business logic
      - redaction implementation
      - agent dispatch
      - model fallback
    """

    def __init__(
        self,
        identity: ResidentIdentity,
        resource_registry: ResourceRegistry,
        interrupt_store: InterruptStoreProtocol,
        event_log: EventLogProtocol,
        policy: PolicyDecider,
        redactor: RedactorProtocol | None = None,
        model_slots: Any | None = None,
    ):
        self.identity = identity
        self.resources = resource_registry
        self.interrupts = interrupt_store
        self.events = event_log
        self.policy = policy
        self.redactor = redactor
        self.model_slots = model_slots

        # ActionExecutor is the pipeline; kernel does NOT execute actions itself
        self.executor = ActionExecutor(
            resource_registry=resource_registry,
            interrupt_store=interrupt_store,
            event_log=event_log,
            policy=policy,
            redactor=redactor,
        )

    async def execute(self, action: Action) -> Observation:
        """Thin facade. Delegates to ActionExecutor."""
        return await self.executor.execute(action)

    async def resume(self, interrupt_id: str, owner_payload: dict[str, Any]) -> dict[str, Any]:
        """Thin facade. Returns small status dict, NOT an Observation.

        See ActionExecutor.resume for the check-continuation-first ordering.
        """
        return await self.executor.resume(interrupt_id, owner_payload)
