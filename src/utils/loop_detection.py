"""共享循环检测器 — genericRepeat / ping-pong 模式匹配。

从 TaskRuntime 抽取的纯函数组件，供 TaskRuntime 和 BaseAgent 共用。
无 I/O、无日志、无副作用——调用方决定如何响应检测结果。
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class LoopVerdict(Enum):
    OK = auto()
    WARNING = auto()
    BLOCK = auto()


@dataclass(frozen=True)
class LoopCheckResult:
    generic_repeat: LoopVerdict = LoopVerdict.OK
    generic_repeat_count: int = 0
    ping_pong: LoopVerdict = LoopVerdict.OK
    ping_pong_count: int = 0

    @property
    def should_block(self) -> bool:
        return (
            self.generic_repeat is LoopVerdict.BLOCK
            or self.ping_pong is LoopVerdict.BLOCK
        )

    @property
    def has_warning(self) -> bool:
        return (
            self.generic_repeat is LoopVerdict.WARNING
            or self.ping_pong is LoopVerdict.WARNING
        )

    @property
    def block_reason(self) -> str:
        if self.generic_repeat is LoopVerdict.BLOCK:
            return (
                "检测到无进展重复循环（同一工具与参数连续重复），"
                "已触发断路器。"
            )
        if self.ping_pong is LoopVerdict.BLOCK:
            return (
                "检测到无进展交替循环（两个工具交替重复调用），"
                "已触发断路器。"
            )
        return ""


@dataclass(frozen=True)
class LoopDetectorConfig:
    enabled: bool = True
    history_size: int = 30
    warning_threshold: int = 10
    critical_threshold: int = 20
    global_circuit_breaker_threshold: int = 30
    detector_generic_repeat: bool = True
    detector_ping_pong: bool = True


@dataclass
class LoopDetectorState:
    history: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=30))


class LoopDetector:
    """无状态检测器——状态由调用方持有的 LoopDetectorState 承载。"""

    def __init__(self, config: LoopDetectorConfig | None = None) -> None:
        self._cfg = config or LoopDetectorConfig()

    @property
    def config(self) -> LoopDetectorConfig:
        return self._cfg

    def new_state(self) -> LoopDetectorState:
        return LoopDetectorState(
            history=deque(maxlen=self._cfg.history_size),
        )

    def check(
        self,
        state: LoopDetectorState,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> LoopCheckResult:
        if not self._cfg.enabled:
            return LoopCheckResult()

        signature = (tool_name, tool_args_hash(arguments))

        gr_count = _generic_repeat_count(state.history, signature)
        gr_verdict = self._classify_generic_repeat(gr_count)

        pp_count = _ping_pong_count(state.history, signature)
        pp_verdict = self._classify_ping_pong(pp_count)

        return LoopCheckResult(
            generic_repeat=gr_verdict,
            generic_repeat_count=gr_count,
            ping_pong=pp_verdict,
            ping_pong_count=pp_count,
        )

    def record(
        self,
        state: LoopDetectorState,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        state.history.append((tool_name, tool_args_hash(arguments)))

    def _classify_generic_repeat(self, count: int) -> LoopVerdict:
        if not self._cfg.detector_generic_repeat:
            return LoopVerdict.OK
        if count >= self._cfg.global_circuit_breaker_threshold:
            return LoopVerdict.BLOCK
        if count >= self._cfg.warning_threshold:
            return LoopVerdict.WARNING
        return LoopVerdict.OK

    def _classify_ping_pong(self, count: int) -> LoopVerdict:
        if not self._cfg.detector_ping_pong:
            return LoopVerdict.OK
        if count >= self._cfg.global_circuit_breaker_threshold:
            return LoopVerdict.BLOCK
        if count >= self._cfg.warning_threshold:
            return LoopVerdict.WARNING
        return LoopVerdict.OK


# ── Pure functions ──────────────────────────────────────────────────

def tool_args_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _generic_repeat_count(
    history: deque[tuple[str, str]],
    current_signature: tuple[str, str],
) -> int:
    count = 1
    for previous_signature in reversed(history):
        if previous_signature != current_signature:
            break
        count += 1
    return count


def _ping_pong_count(
    history: deque[tuple[str, str]],
    current_signature: tuple[str, str],
) -> int:
    if len(history) < 3:
        return 0
    prev = history[-1]
    if prev == current_signature:
        return 0
    count = 1
    idx = len(history) - 1
    while idx >= 1:
        if history[idx] != prev or history[idx - 1] != current_signature:
            break
        count += 1
        idx -= 2
    return count
