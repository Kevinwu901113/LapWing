"""Behavior correction manager with persistence and threshold cooldowns."""

from __future__ import annotations

import logging
import time
from typing import Callable

from src.feedback.correction_store import CorrectionStore

logger = logging.getLogger("lapwing.feedback.correction_manager")


class CorrectionManager:
    """Manage repeated behavior corrections and tool circuit-break events."""

    def __init__(
        self,
        store: CorrectionStore | None = None,
        threshold: int = 3,
        on_threshold: Callable[[str, int, str], None] | None = None,
        on_circuit_break: Callable[[str, int], None] | None = None,
        circuit_break_cooldown_seconds: int = 600,
        threshold_cooldown_hours: int = 24,
    ) -> None:
        self._store = store or CorrectionStore()
        self._threshold = threshold
        self._on_threshold = on_threshold
        self._threshold_cooldown_hours = threshold_cooldown_hours
        self._on_circuit_break = on_circuit_break
        self._circuit_break_cooldown = circuit_break_cooldown_seconds
        self._circuit_last_fire: dict[str, float] = {}

    def add_correction(self, rule_key: str, details: str = "") -> int:
        entry = self._store.increment(rule_key, details)
        logger.info("[correction] rule=%r count=%d details=%r", rule_key, entry.count, details)
        if self._on_threshold and self._store.should_fire_threshold(
            entry,
            self._threshold,
            cooldown_hours=self._threshold_cooldown_hours,
        ):
            all_details = self._store.all_details(rule_key)
            try:
                self._on_threshold(rule_key, entry.count, all_details)
            finally:
                self._store.mark_threshold_fired(rule_key)
        return entry.count

    def format_for_prompt(self, max_entries: int = 5) -> str:
        entries = self._store.top(max_entries)
        if not entries:
            return ""
        lines = ["## 最近的行为纠正（按频次）", ""]
        from src.core.time_utils import now

        current = now()
        for entry in entries:
            days_ago = max((current - entry.last_seen_at).days, 0)
            time_label = f"{days_ago}天前" if days_ago > 0 else "今天"
            preview = entry.last_details[:80] if entry.last_details else ""
            lines.append(f"- **{entry.rule_key}** ({entry.count}次, {time_label}): {preview}")
        return "\n".join(lines) + "\n"

    def get_violations(self) -> dict[str, int]:
        return {entry.rule_key: entry.count for entry in self._store.top(100)}

    def reset(self, rule_key: str) -> None:
        self._store.reset(rule_key)

    def on_circuit_break(self, tool_name: str, repeat_count: int) -> None:
        now_ts = time.monotonic()
        last = self._circuit_last_fire.get(tool_name, 0.0)
        if now_ts - last < self._circuit_break_cooldown:
            logger.debug(
                "[correction] circuit_break for %r suppressed (cooldown %.0fs remaining)",
                tool_name,
                self._circuit_break_cooldown - (now_ts - last),
            )
            return
        self._circuit_last_fire[tool_name] = now_ts
        logger.info("[correction] circuit_break tool=%r repeat=%d", tool_name, repeat_count)
        if self._on_circuit_break:
            self._on_circuit_break(tool_name, repeat_count)
