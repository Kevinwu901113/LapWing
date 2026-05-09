"""Minimal infra circuit breaker for tool-dispatch organs."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("lapwing.core.infra_breaker")


class InfraBreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class InfraOrganState:
    state: InfraBreakerState = InfraBreakerState.CLOSED
    opened_at: float | None = None
    cooldown_until: float | None = None
    backoff_index: int = 0
    consecutive_successes: int = 0
    half_open_probe_in_flight: bool = False
    last_failure_class: str = ""
    transitions: list[dict[str, Any]] = field(default_factory=list)


class InfraCircuitBreaker:
    """Small per-organ breaker.

    It is intentionally not the older per-tool repeat breaker. This one tracks
    infrastructure organs that must be live before a delegation/tool call can
    safely enter the model loop.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        cooldown_schedule_seconds: tuple[float, ...] = (60.0, 120.0, 300.0),
        close_success_threshold: int = 3,
        now_fn=time.monotonic,
    ) -> None:
        self.enabled = enabled
        self.cooldown_schedule_seconds = cooldown_schedule_seconds or (60.0,)
        self.close_success_threshold = max(1, int(close_success_threshold))
        self._now = now_fn
        self._states: dict[str, InfraOrganState] = {}

    def should_allow(self, organ: str) -> tuple[bool, str]:
        if not self.enabled:
            return True, "disabled"
        state = self._state(organ)
        now = self._now()
        if state.state == InfraBreakerState.CLOSED:
            return True, "closed"
        if state.state == InfraBreakerState.OPEN:
            if state.cooldown_until is not None and now >= state.cooldown_until:
                self._transition(organ, state, InfraBreakerState.HALF_OPEN, "cooldown_elapsed")
            else:
                return False, "infra_breaker_open"
        if state.state == InfraBreakerState.HALF_OPEN:
            if state.half_open_probe_in_flight:
                return False, "infra_breaker_half_open_probe_in_flight"
            state.half_open_probe_in_flight = True
            return True, "half_open_probe"
        return True, state.state.value

    def record_success(self, organ: str) -> None:
        if not self.enabled:
            return
        state = self._state(organ)
        if state.state == InfraBreakerState.CLOSED:
            state.consecutive_successes = min(
                self.close_success_threshold,
                state.consecutive_successes + 1,
            )
            return
        if state.state == InfraBreakerState.HALF_OPEN:
            state.half_open_probe_in_flight = False
            state.consecutive_successes += 1
            if state.consecutive_successes >= self.close_success_threshold:
                state.opened_at = None
                state.cooldown_until = None
                state.backoff_index = 0
                state.last_failure_class = ""
                self._transition(organ, state, InfraBreakerState.CLOSED, "success_threshold_met")

    def record_failure(self, organ: str, *, failure_class: str = "tool_infra_unavailable") -> None:
        if not self.enabled:
            return
        state = self._state(organ)
        state.half_open_probe_in_flight = False
        state.consecutive_successes = 0
        state.last_failure_class = failure_class
        now = self._now()
        delay = self.cooldown_schedule_seconds[
            min(state.backoff_index, len(self.cooldown_schedule_seconds) - 1)
        ]
        state.opened_at = now
        state.cooldown_until = now + delay
        state.backoff_index = min(
            state.backoff_index + 1,
            len(self.cooldown_schedule_seconds) - 1,
        )
        self._transition(organ, state, InfraBreakerState.OPEN, failure_class)

    def snapshot(self, organ: str) -> dict[str, Any]:
        state = self._state(organ)
        return {
            "organ": organ,
            "state": state.state.value,
            "opened_at": state.opened_at,
            "cooldown_until": state.cooldown_until,
            "backoff_index": state.backoff_index,
            "consecutive_successes": state.consecutive_successes,
            "half_open_probe_in_flight": state.half_open_probe_in_flight,
            "last_failure_class": state.last_failure_class,
        }

    def transition_log(self, organ: str) -> list[dict[str, Any]]:
        return list(self._state(organ).transitions)

    def _state(self, organ: str) -> InfraOrganState:
        key = (organ or "unknown").strip() or "unknown"
        if key not in self._states:
            self._states[key] = InfraOrganState()
        return self._states[key]

    def _transition(
        self,
        organ: str,
        state: InfraOrganState,
        new_state: InfraBreakerState,
        reason: str,
    ) -> None:
        if state.state == new_state and reason != "tool_infra_unavailable":
            return
        old_state = state.state
        state.state = new_state
        state.transitions.append({
            "at": self._now(),
            "organ": organ,
            "state": new_state.value,
            "reason": reason,
        })
        logger.info(
            "infra_breaker_transition organ=%s %s->%s reason=%s",
            organ, old_state.value, new_state.value, reason,
        )
