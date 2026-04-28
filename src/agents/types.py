"""Agent Team 数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.runtime_profiles import RuntimeProfile


@dataclass
class LegacyAgentSpec:
    """Agent 的配置描述（legacy）。

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


# Backward-compat alias: existing modules (Researcher/Coder/registry/base_agent)
# continue importing ``AgentSpec`` from this module during the transition.
AgentSpec = LegacyAgentSpec


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
    # Hint about how fresh the answer needs to be — "realtime" (live
    # facts: weather/score/price), "recent" (recent facts: news), or
    # "anytime" (stable facts: concepts/history). None = unspecified,
    # let the agent decide. Carried from delegate_to_researcher's
    # tool args; reserved for future fast-path routing.
    freshness_hint: str | None = None


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
    budget_status: str = ""
    # Optional structured payload — Researcher populates this with
    # ``{"summary": str, "sources": [...]}`` so consumers can read the
    # parsed shape directly without re-parsing ``result``. ``result``
    # still holds the same data as a JSON string for backward
    # compatibility with code that treats AgentResult.result as text.
    structured_result: dict | None = None


@dataclass(frozen=True)
class SourceRef:
    """Researcher 返回的来源引用。"""
    ref: str                          # url / tool_ref / internal id
    title: str | None = None
    retrieved_at: datetime | None = None


@dataclass
class ResearchResult:
    """Researcher 的标准返回结构。

    ``summary`` is the LLM-written narrative; ``sources`` is collected
    by the runtime from tool traces (not by the LLM), so the LLM
    cannot fabricate citations.
    """
    summary: str
    sources: list[SourceRef] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "sources": [
                {"ref": s.ref, "title": s.title}
                for s in self.sources
            ],
        }
