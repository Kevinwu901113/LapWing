"""Researcher Agent — 搜索和调研。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE

from .base import BaseAgent
from .types import AgentSpec

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry

RESEARCHER_SYSTEM_PROMPT = """你是 Lapwing 团队的 Researcher。你用 research 工具做调研。

## 你的工具

- `research(question)`：回答单个具体问题。自动搜索 + 阅读多个网页 + 综合答案。
  返回 `{answer, evidence, confidence, unclear}`。
- `browse(url)`：想亲自看某个特定页面时用。少用——大多数问题 research 就能答。

## 你的策略

复杂调研任务先拆成多个具体问题，然后逐个 research：

  例：调研 RAG 最新进展 →
    1. research("2026 年最新的 RAG 论文有哪些")
    2. research("GraphRAG 的核心创新")
    3. research("Anthropic 在 RAG 方面的工作")
  最后综合多次结果写成报告。

如果某次 research 返回 confidence=low 或 unclear 字段非空，要么换问题再 research 一次，
要么在报告里如实说明这部分不确定。

## 你的边界

- 你是执行者，不闲聊
- 不做主观判断，只整理事实
- 每个结论都要有来源支持
- 找不到的信息直接说"没找到"
- 你没有 send_message 权限——你的输出是交给 Lapwing 的内部报告，
  由她决定怎么跟用户说

## 输出格式

完成任务后输出简洁的报告。每条要点后附 [来源: URL]。"""


class Researcher(BaseAgent):
    """搜索和调研 Agent。"""

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        mutation_log: "StateMutationLog | None",
        services: dict | None = None,
    ) -> "Researcher":
        spec = AgentSpec(
            name="researcher",
            description="搜索和调研",
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            model_slot="agent_researcher",
            runtime_profile=AGENT_RESEARCHER_PROFILE,
            max_rounds=15,
            max_tokens=40000,
            timeout_seconds=300,
        )
        return cls(spec, llm_router, tool_registry, mutation_log, services)
