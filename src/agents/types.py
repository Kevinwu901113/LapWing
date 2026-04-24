"""Agent Team 数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.runtime_profiles import RuntimeProfile


@dataclass
class AgentSpec:
    """Agent 的配置描述。

    Step 6 起工具限制首选 ``runtime_profile``（对齐 TaskRuntime 主循环使用的
    ``RuntimeProfile``）；``tools`` 白名单仅作为遗留字段供 test fixtures
    继续工作——生产代码必须用 profile。"""

    name: str
    description: str
    system_prompt: str
    model_slot: str              # LLMRouter slot: "agent_execution" etc.
    tools: list[str] = field(default_factory=list)
    runtime_profile: "RuntimeProfile | None" = None
    max_rounds: int = 15
    max_tokens: int = 30000
    timeout_seconds: int = 180


@dataclass
class AgentMessage:
    """Agent 之间的一条消息。"""
    from_agent: str
    to_agent: str
    task_id: str
    content: str
    message_type: str            # "request" / "response" / "update"
    context_digest: str = ""
    parent_task_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AgentResult:
    """Agent 执行任务的结果。"""
    task_id: str
    status: str                  # "done" / "failed" / "blocked"
    result: str
    artifacts: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    reason: str = ""
    attempted_actions: list[str] = field(default_factory=list)
    error_detail: str | None = None
    execution_trace: list[str] = field(default_factory=list)
