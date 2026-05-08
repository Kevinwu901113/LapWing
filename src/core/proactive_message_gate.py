"""Rate limiting + quiet-hours gate for proactive send_message calls.

Direct assistant replies use bare text and never go through send_message.
This gate fires only on proactive/background paths: inner ticks,
reminders, agent compose_proactive flows. Any caller invoking
send_message in those contexts must consult the gate before delivery.

Three decisions:

- ``allow``  — send proceeds.
- ``defer``  — send refused with a soft reason (quiet hours, min interval
              not yet elapsed). The caller may queue and retry later.
- ``deny``   — send refused with a hard reason (daily cap reached). No
              retry; the message is dropped for this window.

Urgent categories (configurable) bypass the gate when
``allow_urgent_bypass`` is true. Anything in the bypass list still
records a history entry — so the daily counter advances and the next
non-urgent send sees the spend.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Iterable

logger = logging.getLogger("lapwing.core.proactive_message_gate")


@dataclass(frozen=True)
class ProactiveGateDecision:
    decision: str          # "allow" | "defer" | "deny"
    reason: str            # human-readable explanation
    bypassed: bool = False # True when an urgent category short-circuited the gate


@dataclass(frozen=True)
class ProactiveGateContext:
    """Live same-chat state for hard proactive suppression."""

    target_chat_id: str | None = None
    latest_user_message_at: datetime | float | int | None = None
    latest_assistant_reply_at: datetime | float | int | None = None
    pending_user_message: bool = False
    active_user_turn: bool = False
    queued_user_input: bool = False
    active_user_task: bool = False
    stuck_user_turn: bool = False

    def hard_denial_reason(self) -> str | None:
        user_at = _as_timestamp(self.latest_user_message_at)
        assistant_at = _as_timestamp(self.latest_assistant_reply_at)
        if user_at is not None and (assistant_at is None or user_at > assistant_at):
            return "unanswered_user_message"
        if self.stuck_user_turn:
            return "stuck_user_turn"
        if self.pending_user_message:
            return "pending_user_message"
        if self.active_user_turn:
            return "active_user_turn"
        if self.queued_user_input:
            return "queued_user_input"
        if self.active_user_task:
            return "active_user_task"
        return None


def _as_timestamp(value: datetime | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_hhmm(value: str) -> dtime:
    """Parse 'HH:MM' (24h) into a datetime.time. Empty → midnight."""
    if not value:
        return dtime(0, 0)
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"quiet_hours value must be HH:MM, got {value!r}")
    h, m = int(parts[0]), int(parts[1])
    return dtime(h, m)


def _in_quiet_window(now: datetime, start: dtime, end: dtime) -> bool:
    """Quiet hours window. Handles wrap (e.g. 23:00 → 08:00)."""
    if start == end:
        return False  # zero-width window
    cur = now.time()
    if start < end:
        # Same-day window, e.g. 13:00 → 14:00
        return start <= cur < end
    # Wrapping window, e.g. 23:00 → 08:00 (covers late night + early morning)
    return cur >= start or cur < end


class ProactiveMessageGate:
    """Stateful gate. Holds a deque of recent send timestamps."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_per_day: int = 3,
        min_minutes_between: int = 90,
        quiet_hours_start: str = "23:00",
        quiet_hours_end: str = "08:00",
        allow_urgent_bypass: bool = True,
        urgent_bypass_categories: Iterable[str] | None = None,
        clock=None,
    ):
        self.enabled = bool(enabled)
        self.max_per_day = int(max_per_day)
        self.min_minutes_between = int(min_minutes_between)
        self.quiet_start = _parse_hhmm(quiet_hours_start)
        self.quiet_end = _parse_hhmm(quiet_hours_end)
        self.allow_urgent_bypass = bool(allow_urgent_bypass)
        self.bypass_categories = frozenset(
            str(c).strip().lower()
            for c in (urgent_bypass_categories or [])
            if str(c).strip()
        )
        self._clock = clock or datetime.now
        self._lock = threading.Lock()
        self._history: deque[datetime] = deque(maxlen=max(self.max_per_day * 4, 16))

    @classmethod
    def from_settings(cls, cfg, *, clock=None) -> "ProactiveMessageGate":
        """Build from a ProactiveMessagesConfig pydantic model."""
        return cls(
            enabled=cfg.enabled,
            max_per_day=cfg.max_per_day,
            min_minutes_between=cfg.min_minutes_between,
            quiet_hours_start=cfg.quiet_hours_start,
            quiet_hours_end=cfg.quiet_hours_end,
            allow_urgent_bypass=cfg.allow_urgent_bypass,
            urgent_bypass_categories=cfg.urgent_bypass_categories,
            clock=clock,
        )

    def evaluate(
        self,
        *,
        category: str | None = None,
        urgent: bool = False,
        context: ProactiveGateContext | None = None,
        reserve: bool = True,
    ) -> ProactiveGateDecision:
        """Decide whether a proactive send may proceed right now.

        ``category`` is the message classification (e.g. "reminder_due").
        ``urgent`` is an explicit override flag the caller may set.
        Either one triggers the urgent-bypass path when allowed.
        """
        hard_reason = context.hard_denial_reason() if context is not None else None
        if hard_reason is not None:
            reason = hard_reason
            if context is not None and context.target_chat_id:
                reason = f"{hard_reason}:target_chat_id={context.target_chat_id}"
            decision = ProactiveGateDecision(decision="deny", reason=reason)
            self._log_and_record(
                decision,
                urgent=bool(urgent),
                target_chat_id=context.target_chat_id if context else None,
            )
            return decision

        if not self.enabled:
            decision = ProactiveGateDecision(
                decision="allow", reason="proactive_messages.enabled=false"
            )
            self._log_and_record(
                decision,
                urgent=False,
                target_chat_id=context.target_chat_id if context else None,
            )
            return decision

        cat_norm = (category or "").strip().lower()
        is_urgent = bool(urgent) or (cat_norm in self.bypass_categories)
        if is_urgent and self.allow_urgent_bypass:
            decision = ProactiveGateDecision(
                decision="allow",
                reason=f"urgent_bypass:category={cat_norm or '<flag>'}",
                bypassed=True,
            )
            self._log_and_record(
                decision,
                urgent=True,
                target_chat_id=context.target_chat_id if context else None,
                record_bypass=reserve,
            )
            return decision

        with self._lock:
            now = self._clock()
            self._evict_stale(now)

            # Quiet hours
            if _in_quiet_window(now, self.quiet_start, self.quiet_end):
                decision = ProactiveGateDecision(
                    decision="defer",
                    reason=(
                        f"quiet_hours [{self.quiet_start.strftime('%H:%M')}-"
                        f"{self.quiet_end.strftime('%H:%M')}]"
                    ),
                )
                self._log_and_record(
                    decision,
                    urgent=False,
                    target_chat_id=context.target_chat_id if context else None,
                )
                return decision

            # Daily cap (rolling 24h)
            if len(self._history) >= self.max_per_day:
                decision = ProactiveGateDecision(
                    decision="deny",
                    reason=(
                        f"daily_cap_reached:max_per_day={self.max_per_day} "
                        f"(rolling 24h)"
                    ),
                )
                self._log_and_record(
                    decision,
                    urgent=False,
                    target_chat_id=context.target_chat_id if context else None,
                )
                return decision

            # Min spacing
            if self._history and self.min_minutes_between > 0:
                gap = now - self._history[-1]
                min_gap = timedelta(minutes=self.min_minutes_between)
                if gap < min_gap:
                    remaining = min_gap - gap
                    secs = int(remaining.total_seconds())
                    decision = ProactiveGateDecision(
                        decision="defer",
                        reason=(
                            f"min_interval_not_elapsed:"
                            f"min_minutes_between={self.min_minutes_between} "
                            f"remaining_seconds={secs}"
                        ),
                    )
                    self._log_and_record(
                        decision,
                        urgent=False,
                        target_chat_id=context.target_chat_id if context else None,
                    )
                    return decision

            decision = ProactiveGateDecision(
                decision="allow",
                reason="within_budget",
            )
            self._log_and_record(
                decision,
                urgent=False,
                target_chat_id=context.target_chat_id if context else None,
            )
            if reserve:
                self._history.append(now)
            return decision

    def record_send(self, when: datetime | None = None) -> None:
        """Manually record a send. Used when the gate ran in observe mode
        elsewhere or when an urgent bypass should still spend the budget.
        """
        with self._lock:
            self._history.append(when or self._clock())

    def remaining_today(self) -> int:
        with self._lock:
            self._evict_stale(self._clock())
            return max(0, self.max_per_day - len(self._history))

    def _evict_stale(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=24)
        while self._history and self._history[0] < cutoff:
            self._history.popleft()

    def _log_and_record(
        self,
        decision: ProactiveGateDecision,
        *,
        urgent: bool,
        target_chat_id: str | None = None,
        record_bypass: bool = True,
    ) -> None:
        # Log every decision — auditability is part of the contract
        # (commit 9 builds on this with a structured PROACTIVE_MESSAGE_DECISION
        # mutation log entry; the human-readable line is always emitted here).
        logger.info(
            "[proactive_gate] decision=%s urgent=%s reason=%s target_chat_id=%s",
            decision.decision, urgent, decision.reason, target_chat_id or "",
        )
        if decision.bypassed and self.allow_urgent_bypass and record_bypass:
            # Urgent bypass spends budget too — keeps the cap honest if the
            # LLM tries to backdoor by always claiming urgency.
            self._history.append(self._clock())
