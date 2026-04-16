"""Researcher Agent — 搜索和调研。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseAgent
from .types import AgentSpec

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry

RESEARCHER_SYSTEM_PROMPT = """你是 Lapwing 团队的 Researcher。你擅长搜索、调研、整理信息。

## 你的职责

1. 根据任务需求，用搜索工具查找信息
2. 必要时抓取网页内容深入阅读
3. 整理成简洁的摘要
4. 在结果中标注信息来源（URL）

## 你的边界

- 你是执行者，不闲聊
- 不做主观判断，只整理事实
- 每个结论都要有来源支持
- 找不到的信息直接说"没找到"

## 输出格式

完成任务后，输出简洁的摘要，每条要点后附上 [来源: URL]。"""


class Researcher(BaseAgent):
    """搜索和调研 Agent。"""

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        dispatcher: "Dispatcher",
        services: dict | None = None,
    ) -> "Researcher":
        spec = AgentSpec(
            name="researcher",
            description="搜索和调研",
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            model_slot="agent_execution",
            tools=["web_search", "web_fetch"],
            max_rounds=15,
            max_tokens=40000,
            timeout_seconds=300,
        )
        return cls(spec, llm_router, tool_registry, dispatcher, services)
