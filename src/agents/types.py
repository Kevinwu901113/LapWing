"""Agent Team 数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentSpec:
    """Agent 的配置描述。"""
    name: str
    description: str
    system_prompt: str
    model_slot: str              # LLMRouter slot: "agent_execution" etc.
    tools: list[str]             # 可用工具名称列表
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
