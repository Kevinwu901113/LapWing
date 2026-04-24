"""Circuit breaker for repeated tool failures.

Tracks failures by key (typically tool_name:args_hash). After consecutive
failures, imposes an escalating cooldown before allowing retries.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Sequence

logger = logging.getLogger("lapwing.utils.circuit_breaker")

_DEFAULT_COOLDOWNS: tuple[int, ...] = (600, 1800, 7200)


class CircuitBreaker:
    def __init__(
        self,
        cooldown_sequence: Sequence[int] = _DEFAULT_COOLDOWNS,
        on_open: Callable[[str, int], None] | None = None,
    ) -> None:
        self._cooldowns = tuple(cooldown_sequence)
        self._failures: dict[str, list[float]] = {}
        self._last_success: dict[str, float] = {}
        self._on_open = on_open
        self._notified: set[str] = set()

    def should_allow(self, key: str) -> tuple[bool, str]:
        failures = self._failures.get(key)
        if not failures:
            return True, ""

        count = len(failures)
        if count == 0:
            return True, ""

        cooldown_idx = min(count - 1, len(self._cooldowns) - 1)
        cooldown = self._cooldowns[cooldown_idx]
        last_failure = failures[-1]
        elapsed = time.time() - last_failure

        if elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            reason = f"circuit_breaker: {count} failures, retry in {remaining}s"
            return False, reason

        return True, ""

    def record_failure(self, key: str) -> None:
        if key not in self._failures:
            self._failures[key] = []
        self._failures[key].append(time.time())
        count = len(self._failures[key])
        logger.debug("Circuit breaker: failure recorded for %s (count=%d)",
                      key, count)
        if self._on_open is not None and count >= 2 and key not in self._notified:
            self._notified.add(key)
            try:
                self._on_open(key, count)
            except Exception:
                logger.debug("on_open callback failed for %s", key, exc_info=True)

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._notified.discard(key)
        self._last_success[key] = time.time()

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._notified.discard(key)

    def reset_all(self) -> None:
        self._failures.clear()
        self._last_success.clear()
        self._notified.clear()

    @property
    def open_circuits(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._failures.items() if v}
