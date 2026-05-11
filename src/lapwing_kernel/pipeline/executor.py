"""ActionExecutor — the Kernel action pipeline.

All Resource Actions enter through this pipeline. No caller may bypass and
call adapter internals directly. kernel.py only wires this; business logic
lives here.

See docs/architecture/lapwing_v1_blueprint.md §4.3.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

from ..policy import PolicyDecider, PolicyDecision
from ..primitives.action import Action
from ..primitives.event import Event
from ..primitives.interrupt import DEFAULT_INTERRUPT_EXPIRY, Interrupt
from ..primitives.observation import Observation
from .registry import ResourceRegistry


class InterruptStoreProtocol(Protocol):
    """Persistence facade for Interrupt. Real impl in Slice D."""

    def persist(self, interrupt: Interrupt) -> None: ...
    def get(self, interrupt_id: str) -> Interrupt | None: ...
    def resolve(self, interrupt_id: str, owner_payload: dict[str, Any]) -> None: ...
    def cancel(self, interrupt_id: str, *, reason: str) -> None: ...


class EventLogProtocol(Protocol):
    """Append-only event log. Real impl in Slice F."""

    def append(self, event: Event) -> None: ...


class RedactorProtocol(Protocol):
    """Defense-in-depth Observation redactor. Real impl in P0-Redaction."""

    def redact_observation(self, obs: Observation) -> Observation: ...


class _NullRedactor:
    """Pass-through redactor for Slice A. Replaced by SecretRedactor in PR-02."""

    def redact_observation(self, obs: Observation) -> Observation:
        return obs


class ActionExecutor:
    """The Kernel action pipeline.

    Stages per execute():
      1. Policy decision (ALLOW / BLOCK / INTERRUPT)
      2. Resolve (resource, profile) from ResourceRegistry
      3. Log action start
      4. Adapter execute
      5. Defense-in-depth redaction
      6. Log outcome
    """

    def __init__(
        self,
        resource_registry: ResourceRegistry,
        interrupt_store: InterruptStoreProtocol,
        event_log: EventLogProtocol,
        policy: PolicyDecider,
        redactor: RedactorProtocol | None = None,
    ):
        self._registry = resource_registry
        self._interrupts = interrupt_store
        self._events = event_log
        self._policy = policy
        self._redactor = redactor or _NullRedactor()

    async def execute(self, action: Action) -> Observation:
        decision = self._policy.decide(action)

        if decision == PolicyDecision.BLOCK:
            self._events.append(
                Event.new(
                    actor=action.actor,
                    type="policy.blocked",
                    resource=action.resource,
                    summary=f"{action.resource}.{action.verb} blocked by policy",
                    refs={"action_id": action.id},
                    outcome="blocked",
                )
            )
            return Observation.failure(
                action.id,
                action.resource,
                status="blocked_by_policy",
                error="policy.block",
                summary=f"{action.resource}.{action.verb} blocked",
            )

        if decision == PolicyDecision.INTERRUPT:
            return self._create_policy_interrupt(action)

        # ALLOW
        resource = self._registry.get(action.resource, profile=action.resource_profile)

        if not resource.supports(action.verb):
            return Observation.failure(
                action.id,
                action.resource,
                status="failed",
                error=f"unsupported_verb:{action.verb}",
            )

        self._events.append(
            Event.new(
                actor=action.actor,
                type=f"{action.resource}.{action.verb}",
                resource=action.resource,
                summary=f"executing {action.resource}.{action.verb}",
                refs={"action_id": action.id},
            )
        )

        try:
            observation = await resource.execute(action)
        except Exception as exc:
            self._events.append(
                Event.new(
                    actor=action.actor,
                    type=f"{action.resource}.failed",
                    resource=action.resource,
                    summary=str(exc)[:200],
                    outcome="failed",
                    refs={"action_id": action.id},
                )
            )
            return Observation.failure(
                action.id,
                action.resource,
                status="failed",
                error=type(exc).__name__,
                summary=str(exc)[:200],
            )

        observation = self._redactor.redact_observation(observation)

        outcome_refs: dict[str, str] = {
            "action_id": action.id,
            "observation_id": observation.id,
        }
        if observation.interrupt_id:
            outcome_refs["interrupt_id"] = observation.interrupt_id

        self._events.append(
            Event.new(
                actor=action.actor,
                type=f"{action.resource}.{observation.status}",
                resource=action.resource,
                summary=observation.summary or "",
                outcome=observation.status,
                refs=outcome_refs,
            )
        )

        return observation

    def _create_policy_interrupt(self, action: Action) -> Observation:
        """Policy-initiated INTERRUPT (e.g. credential first-use).

        Register a continuation_ref and persist a resumable Interrupt. Caller
        (agent worker) is responsible for awaiting via
        ContinuationRegistry.wait_for_resume(ref).
        """
        from .continuation_registry import ContinuationRegistry

        kind = f"policy.{action.resource}.{action.verb}"
        continuation_ref = ContinuationRegistry.instance().register(action.task_ref)
        expires_in = DEFAULT_INTERRUPT_EXPIRY.get(kind, timedelta(hours=24))

        interrupt = Interrupt.new(
            kind=kind,
            actor_required="owner",
            resource=action.resource,
            resource_ref=None,
            continuation_ref=continuation_ref,
            summary=f"policy interrupt for {action.resource}.{action.verb}",
            payload_redacted={
                "resource": action.resource,
                "resource_profile": action.resource_profile,
                "verb": action.verb,
            },
            expires_in=expires_in,
        )
        self._interrupts.persist(interrupt)

        self._events.append(
            Event.new(
                actor="system",
                type="interrupt.created",
                resource=action.resource,
                summary=interrupt.summary,
                outcome="interrupted",
                refs={"action_id": action.id, "interrupt_id": interrupt.id},
            )
        )

        return Observation.interrupted(
            action.id,
            action.resource,
            interrupt_id=interrupt.id,
            summary=interrupt.summary,
        )

    async def resume(self, interrupt_id: str, owner_payload: dict[str, Any]) -> dict[str, Any]:
        """Owner has resolved an interrupt. Release the suspended continuation
        and return immediately. The final Observation is produced by the
        original agent worker in its own coroutine, NOT awaited here.

        Critical ordering (blueprint §4.3, GPT final pass):
          1. Validate interrupt exists + is pending + resumable
          2. Check continuation_ref is still alive (process restart between
             Interrupt creation and resume = continuation lost)
          3. ONLY if alive → persist resolved + release future
             If lost → mark cancelled with reason, write EventLog, return error
             NEVER mark resolved without an awaiter to wake.

        Returns a small status dict, NOT an Observation. Desktop /approve
        endpoint must not block awaiting the final Observation.
        """
        from .continuation_registry import ContinuationRegistry

        interrupt = self._interrupts.get(interrupt_id)
        if interrupt is None:
            raise KeyError(f"Interrupt {interrupt_id} not found")
        if interrupt.status != "pending":
            raise ValueError(
                f"Interrupt {interrupt_id} is {interrupt.status}, not pending"
            )
        if interrupt.non_resumable or interrupt.continuation_ref is None:
            return {
                "status": "error",
                "interrupt_id": interrupt_id,
                "reason": "non_resumable_interrupt",
            }

        registry = ContinuationRegistry.instance()

        if not registry.has(interrupt.continuation_ref):
            self._interrupts.cancel(
                interrupt_id,
                reason="continuation_lost_after_restart",
            )
            self._events.append(
                Event.new(
                    actor="system",
                    type="interrupt.continuation_lost",
                    resource=interrupt.resource,
                    summary=(
                        f"continuation {interrupt.continuation_ref} lost; "
                        f"likely kernel restart between interrupt creation and resolve"
                    ),
                    outcome="cancelled",
                    refs={"interrupt_id": interrupt.id},
                )
            )
            return {
                "status": "error",
                "interrupt_id": interrupt_id,
                "reason": "continuation_lost_after_restart",
            }

        self._interrupts.resolve(interrupt_id, owner_payload)
        self._events.append(
            Event.new(
                actor="owner",
                type="interrupt.resolved",
                resource=interrupt.resource,
                summary=f"owner resolved {interrupt.kind}",
                refs={"interrupt_id": interrupt.id},
            )
        )
        registry.resume(interrupt.continuation_ref, owner_payload)

        return {
            "status": "resumed",
            "interrupt_id": interrupt_id,
            "continuation_ref": interrupt.continuation_ref,
        }
