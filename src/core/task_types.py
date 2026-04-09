"""TaskRuntime 数据类型和辅助函数。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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
