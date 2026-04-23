"""记录和追踪 Lapwing 被纠正的行为模式，以及工具断路事件。"""

import time
import logging
from typing import Callable

logger = logging.getLogger("lapwing.core.correction_manager")


class CorrectionManager:
    """管理行为纠正记录和工具断路事件。

    - add_correction：记录 Kevin 对 Lapwing 的一次纠正；同一规则达到阈值时触发回调。
    - on_circuit_break：工具断路器触发时调用；每个工具有独立冷却期（默认 10 分钟）。
    """

    def __init__(
        self,
        threshold: int = 3,
        on_threshold: Callable[[str, int, str], None] | None = None,
        on_circuit_break: Callable[[str, int], None] | None = None,
        circuit_break_cooldown_seconds: int = 600,
    ):
        # rule_key → 累计纠正次数
        self._violations: dict[str, int] = {}
        # rule_key → 历史详情列表
        self._details: dict[str, list[str]] = {}
        # 触发 urgency 的阈值
        self._threshold = threshold
        # 纠正达到阈值时的回调，签名：(rule_key, count, all_details) → None
        self._on_threshold = on_threshold
        # 断路器触发时的回调，签名：(tool_name, repeat_count) → None
        self._on_circuit_break = on_circuit_break
        # 每个工具的断路器冷却时间（秒）
        self._circuit_break_cooldown = circuit_break_cooldown_seconds
        # tool_name → 上次触发时间戳（monotonic）
        self._circuit_last_fire: dict[str, float] = {}

    def add_correction(self, rule_key: str, details: str = "") -> int:
        """记录一次纠正。返回当前累计次数。达到阈值时调用 on_threshold 回调。"""
        count = self._violations.get(rule_key, 0) + 1
        self._violations[rule_key] = count
        if details:
            self._details.setdefault(rule_key, []).append(details)
        logger.info("[correction] rule=%r count=%d details=%r", rule_key, count, details)
        if count >= self._threshold and self._on_threshold:
            all_details = "; ".join(self._details.get(rule_key, []))
            self._on_threshold(rule_key, count, all_details)
        return count

    def on_circuit_break(self, tool_name: str, repeat_count: int) -> None:
        """工具断路器触发时调用。每个工具有独立冷却期，期间重复触发会被抑制。"""
        now = time.monotonic()
        last = self._circuit_last_fire.get(tool_name, 0.0)
        if now - last < self._circuit_break_cooldown:
            logger.debug(
                "[correction] circuit_break for %r suppressed (cooldown %.0fs remaining)",
                tool_name,
                self._circuit_break_cooldown - (now - last),
            )
            return
        self._circuit_last_fire[tool_name] = now
        logger.info("[correction] circuit_break tool=%r repeat=%d", tool_name, repeat_count)
        if self._on_circuit_break:
            self._on_circuit_break(tool_name, repeat_count)

    def get_violations(self) -> dict[str, int]:
        """返回所有规则的纠正次数快照。"""
        return dict(self._violations)

    def reset(self, rule_key: str) -> None:
        """清除某个规则的纠正记录。"""
        self._violations.pop(rule_key, None)
        self._details.pop(rule_key, None)
