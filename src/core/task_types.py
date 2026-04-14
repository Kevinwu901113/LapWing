"""TaskRuntime 数据类型和辅助函数。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from config.settings import (
    LOOP_DETECTION_DETECTOR_GENERIC_REPEAT,
    LOOP_DETECTION_DETECTOR_KNOWN_POLL_NO_PROGRESS,
    LOOP_DETECTION_DETECTOR_PING_PONG,
    LOOP_DETECTION_ENABLED,
    LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD,
    LOOP_DETECTION_HISTORY_SIZE,
    LOOP_DETECTION_WARNING_THRESHOLD,
    LOOP_DETECTION_CRITICAL_THRESHOLD,
)
from src.core.shell_policy import ShellRuntimePolicy
from src.tools.shell_executor import ShellResult


@dataclass(frozen=True)
class LoopDetectionConfig:
    """工具循环检测配置（对齐 OpenClaw 语义）。"""

    enabled: bool = LOOP_DETECTION_ENABLED
    history_size: int = LOOP_DETECTION_HISTORY_SIZE
    warning_threshold: int = LOOP_DETECTION_WARNING_THRESHOLD
    critical_threshold: int = LOOP_DETECTION_CRITICAL_THRESHOLD
    global_circuit_breaker_threshold: int = LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD
    detector_generic_repeat: bool = LOOP_DETECTION_DETECTOR_GENERIC_REPEAT
    detector_ping_pong: bool = LOOP_DETECTION_DETECTOR_PING_PONG
    detector_known_poll_no_progress: bool = LOOP_DETECTION_DETECTOR_KNOWN_POLL_NO_PROGRESS


@dataclass
class LoopDetectionState:
    """单次 complete_chat 生命周期内的循环检测状态。"""

    history: deque[tuple[str, str]]


@dataclass
class LoopRecoveryState:
    """Loop 恢复状态追踪。"""

    turn_count: int = 0
    transition_reason: str = "initial"

    # Reactive compact
    reactive_compact_attempts: int = 0
    MAX_REACTIVE_COMPACT: int = 2

    # Max output recovery
    max_output_recovery_count: int = 0
    MAX_OUTPUT_RECOVERY: int = 2

    # API retry
    consecutive_api_errors: int = 0
    MAX_CONSECUTIVE_API_ERRORS: int = 3

    # Result budgeting
    total_result_chars: int = 0

    def record_transition(self, reason: str) -> None:
        self.transition_reason = reason
        self.turn_count += 1

    def can_reactive_compact(self) -> bool:
        return self.reactive_compact_attempts < self.MAX_REACTIVE_COMPACT

    def can_output_recovery(self) -> bool:
        return self.max_output_recovery_count < self.MAX_OUTPUT_RECOVERY

    def can_retry_api(self) -> bool:
        return self.consecutive_api_errors < self.MAX_CONSECUTIVE_API_ERRORS

    def reset_api_errors(self) -> None:
        self.consecutive_api_errors = 0


@dataclass(frozen=True)
class RuntimeDeps:
    """tool loop 运行所需的策略与执行依赖。"""

    execute_shell: Callable[[str], Awaitable[ShellResult]]
    policy: ShellRuntimePolicy
    shell_default_cwd: str
    shell_allow_sudo: bool


@dataclass(frozen=True)
class TaskLoopStep:
    completed: bool = False
    stop: bool = False
    reason: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskLoopResult:
    completed: bool
    stopped: bool
    attempts: int
    reason: str = ""
    last_payload: dict[str, Any] | None = None


@dataclass
class NoActionBudget:
    """连续无工具调用的轮次预算。

    当 LLM 连续返回纯文本（无 tool_calls）时，消耗预算。
    有 tool_calls 时重置。预算耗尽时结束循环。
    """

    default: int = 3
    maximum: int = 8
    remaining: int = 3

    def consume(self) -> bool:
        """消耗一次预算。返回 True = 还有余量，False = 预算耗尽。"""
        self.remaining -= 1
        return self.remaining > 0

    def reset(self):
        """有工具调用时重置预算。"""
        self.remaining = self.default

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


@dataclass
class ErrorBurstGuard:
    """连续错误断路器。

    当连续 N 个工具调用都失败时，触发断路。
    成功的调用降低错误计数（渐进恢复）。
    """

    threshold: int = 3
    error_count: int = 0
    recent_errors: list[str] = field(default_factory=list)

    def record_error(self, error_msg: str) -> bool:
        """记录一次错误。返回 True = 应该断路。"""
        self.error_count += 1
        self.recent_errors.append(error_msg[:200])
        if len(self.recent_errors) > 10:
            self.recent_errors.pop(0)
        return self.error_count >= self.threshold

    def record_success(self):
        """记录一次成功。降低错误计数（渐进恢复，不清零）。"""
        self.error_count = max(0, self.error_count - 1)

    @property
    def should_break(self) -> bool:
        return self.error_count >= self.threshold

    @property
    def summary(self) -> str:
        """生成错误摘要，可注入给 LLM 帮助它理解失败模式。"""
        if not self.recent_errors:
            return ""
        return f"最近 {len(self.recent_errors)} 次错误：" + "; ".join(self.recent_errors[-3:])


def _refresh_voice_reminder(messages: list[dict]) -> None:
    """在 tool loop 轮次之间重新注入 voice reminder。"""
    try:
        from src.core.prompt_builder import inject_voice_reminder
        i = 0
        while i < len(messages):
            msg = messages[i]
            if (
                msg.get("role") == "user"
                and isinstance(msg.get("content"), str)
                and "[System Note]" in msg["content"]
            ):
                messages.pop(i)
            else:
                i += 1
        inject_voice_reminder(messages)
    except Exception:
        pass
