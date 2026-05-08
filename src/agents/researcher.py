"""Researcher Agent — 搜索和调研。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from src.config import get_settings
from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE
from src.logging.state_mutation_log import MutationType

from .base import BaseAgent
from .types import AgentMessage, AgentResult, AgentSpec, ResearchResult, SourceRef

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry

logger = logging.getLogger("lapwing.agents.researcher")

RESEARCHER_SYSTEM_PROMPT = """你是 Lapwing 团队的 Researcher。你用 research 工具做调研。

## 你的工具

- `research(question)`：回答单个具体问题。自动搜索 + 阅读多个网页 + 综合答案。
  返回 `{answer, evidence, confidence, unclear}`。
- `browse(url)`：想亲自看某个特定页面时用。少用——大多数问题 research 就能答。

## 你的策略

复杂调研任务先拆成多个具体问题,然后逐个 research:

  例:调研 RAG 最新进展 →
    1. research("2026 年最新的 RAG 论文有哪些")
    2. research("GraphRAG 的核心创新")
    3. research("Anthropic 在 RAG 方面的工作")
  最后综合多次结果写成报告。

如果某次 research 返回 confidence=low 或 unclear 字段非空,要么换问题再 research 一次,
要么在报告里如实说明这部分不确定。

## 你的边界

- 你是执行者,不闲聊
- 不做主观判断,只整理事实
- 每个结论都要有来源支持
- 找不到的信息直接说"没找到"
- 你没有 send_message 权限——你的输出是交给 Lapwing 的内部报告,
  由她决定怎么跟用户说

## 输出格式

直接输出事实摘要文本。不要加问候语或寒暄——上层 Lapwing 会重新组织后告诉用户。
来源 URL 不需要你手动列出,运行时会从工具调用记录中自动提取。"""


# Closed-form retrieval patterns — used only for telemetry today.
# Post-MVP: any of these could become a code-level fast path (single
# tool call, no LLM loop) once production traces show the classifier
# is reliable enough.
_FAST_PATH_HINTS: frozenset[str] = frozenset({
    "天气", "气温", "降水", "weather",
    "比分", "赛况", "score",
    "股价", "价格", "汇率", "price",
    "现在几点", "current time",
})


class Researcher(BaseAgent):
    """搜索和调研 Agent。"""

    REQUIRED_SERVICES = BaseAgent.REQUIRED_SERVICES + ("research_engine", "ambient_store")

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        mutation_log: "StateMutationLog | None",
        services: dict | None = None,
    ) -> "Researcher":
        cfg = get_settings().agent_team.researcher
        spec = AgentSpec(
            name="researcher",
            description="搜索和调研",
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            model_slot="agent_researcher",
            runtime_profile=AGENT_RESEARCHER_PROFILE,
            max_rounds=cfg.max_rounds,
            max_tokens=cfg.max_tokens,
            timeout_seconds=cfg.timeout_seconds,
        )
        return cls(spec, llm_router, tool_registry, mutation_log, services)

    async def execute(self, message: AgentMessage) -> AgentResult:
        await self._record_task_received(message)
        fast = await self._try_fast_path(
            message.content, message.freshness_hint,
        )
        if fast is not None:
            return AgentResult(
                task_id=message.task_id,
                status="done",
                result=json.dumps(fast.to_dict(), ensure_ascii=False),
                structured_result=fast.to_dict(),
                execution_trace=["fast_path"],
            )
        return await super().execute(message)

    async def _record_task_received(self, message: AgentMessage) -> None:
        if self.mutation_log is None:
            return
        try:
            await self.mutation_log.record(
                MutationType.RESEARCHER_TASK_RECEIVED,
                payload={
                    "task": (message.content or "")[:200],
                    "freshness_hint": message.freshness_hint,
                    "fast_path_candidate": self._is_closed_form_candidate(message.content),
                },
            )
        except Exception:
            logger.debug(
                "Researcher telemetry record failed", exc_info=True,
            )

    async def _try_fast_path(
        self, task: str, freshness_hint: str | None,
    ) -> ResearchResult | None:
        """MVP: never short-circuit. Telemetry above tracks how many
        tasks would be candidates so we can decide later which patterns
        are worth a single-call code-level fast path.
        """
        return None

    @staticmethod
    def _is_closed_form_candidate(task: str) -> bool:
        if not task:
            return False
        lowered = task.lower()
        return any(hint in lowered for hint in _FAST_PATH_HINTS)

    def _postprocess_result(
        self, text: str, evidence: list[dict],
    ) -> tuple[str, dict]:
        """Wrap Researcher output as ``{summary, sources}``.

        Sources are extracted from runtime-collected evidence, not from
        the LLM text — the LLM cannot fabricate URLs.
        """
        sources = self._extract_sources(evidence)
        result = ResearchResult(summary=text, sources=sources)
        structured = result.to_dict()
        return json.dumps(structured, ensure_ascii=False), structured

    @staticmethod
    def _extract_sources(evidence: list[dict]) -> list[SourceRef]:
        seen_refs: set[str] = set()
        out: list[SourceRef] = []
        for entry in evidence:
            if not isinstance(entry, dict):
                continue
            ref = entry.get("source_url") or entry.get("file_path")
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            out.append(SourceRef(
                ref=str(ref),
                title=entry.get("snippet"),
            ))
        return out
