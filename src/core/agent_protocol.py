"""Agent 通信协议数据类型。

子 Agent 与 Brain 之间交换的消息结构，以及状态枚举。
此模块不依赖任何其他 Lapwing 代码，仅使用标准库。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AgentUrgency(str, Enum):
    """紧急程度。"""
    IMMEDIATE = "immediate"
    SOON = "soon"
    LATER = "later"


class AgentNotifyKind(str, Enum):
    """通知类型。"""
    RESULT = "result"
    PROGRESS = "progress"
    ERROR = "error"
    QUESTION = "question"


class AgentCommandIntent(str, Enum):
    """指令意图。"""
    EXECUTE = "execute"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    CONTEXT = "context"


class AgentCommandPriority(str, Enum):
    """指令优先级。"""
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class AgentEmitState(str, Enum):
    """Agent 状态。"""
    QUEUED = "queued"
    WORKING = "working"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class GuidanceOption:
    """单条行动方案。"""
    label: str
    steps: list[str]
    rationale: str | None = None
    risk: str = "low"


@dataclass
class AgentGuidance:
    """Brain 向子 Agent 提供的执行建议。"""
    options: list[GuidanceOption]
    persona_hints: dict[str, str] | None = None


@dataclass
class AgentNotify:
    """子 Agent -> Brain：报告事件。"""
    agent_name: str
    kind: AgentNotifyKind
    urgency: AgentUrgency
    headline: str
    detail: str | None = None
    payload: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    ref_command_id: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class AgentCommand:
    """Brain -> 子 Agent：下达指令。"""
    target_agent: str
    intent: AgentCommandIntent
    task_description: str
    priority: AgentCommandPriority = AgentCommandPriority.NORMAL
    interrupt: bool = False
    guidance: AgentGuidance | None = None
    context: dict[str, Any] | None = None
    max_steps: int = 20
    timeout_seconds: float = 300
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)


@dataclass
class AgentEmit:
    """双向状态更新。"""
    agent_name: str
    ref_id: str
    state: AgentEmitState
    progress: float | None = None
    note: str | None = None
    payload: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)


# 状态回调类型别名：(state, note, progress, payload) -> None
EmitCallback = Callable[[AgentEmitState, str | None, float | None, dict | None], None]
