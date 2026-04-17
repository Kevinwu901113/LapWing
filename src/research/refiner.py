"""Refiner — 把多源搜索结果交给 LLM 精炼成结构化答案。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.research.prompts import REFINE_PROMPT
from src.research.types import Evidence, ResearchResult

logger = logging.getLogger("lapwing.research.refiner")

_MAX_TOKENS = 1500
_QUOTE_MAX = 300
_FALLBACK_ANSWER_MAX = 500
_FALLBACK_QUOTE_MAX = 300

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


class Refiner:
    """用 router.complete(purpose='tool') 综合多源结果。"""

    def __init__(self, llm_router: Any) -> None:
        self.llm_router = llm_router

    async def refine(self, question: str, sources: list[dict[str, Any]]) -> ResearchResult:
        if not sources:
            return ResearchResult(
                answer="没有找到相关信息。",
                confidence="low",
            )

        prompt = REFINE_PROMPT.format(
            question=question,
            sources=self._format_sources(sources),
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            response = await self.llm_router.complete(
                messages=messages,
                purpose="tool",
                max_tokens=_MAX_TOKENS,
            )
        except Exception as exc:
            logger.error("Refine LLM 调用失败: %s", exc)
            return self._fallback_from_first_source(
                sources, unclear=f"精炼失败（{exc}），返回原始摘要"
            )

        try:
            parsed = self._parse_json(response or "")
        except Exception as exc:
            logger.warning("Refiner JSON 解析失败: %s | response=%r", exc, (response or "")[:300])
            return ResearchResult(
                answer=(response or "")[:_FALLBACK_ANSWER_MAX].strip()
                or "精炼结果为空。",
                confidence="low",
                unclear="精炼结果无法解析为结构化数据",
            )

        return self._result_from_parsed(parsed)

    @staticmethod
    def _format_sources(sources: list[dict[str, Any]]) -> str:
        chunks = []
        for i, s in enumerate(sources, start=1):
            chunks.append(
                f"[Source {i}] {s.get('title', '')}\n"
                f"URL: {s.get('url', '')}\n"
                f"Fetched: {s.get('is_fetched', False)}\n\n"
                f"{s.get('content', '')}"
            )
        return "\n\n---\n\n".join(chunks)

    @staticmethod
    def _parse_json(text: str) -> dict:
        stripped = text.strip()
        match = _CODE_FENCE_RE.match(stripped)
        if match:
            stripped = match.group(1).strip()
        return json.loads(stripped)

    @staticmethod
    def _result_from_parsed(parsed: dict) -> ResearchResult:
        confidence = parsed.get("confidence", "medium")
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"
        evidence = []
        for ev in parsed.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            evidence.append(Evidence(
                source_url=str(ev.get("source_url", "")),
                source_name=str(ev.get("source_name", "")),
                quote=str(ev.get("quote", ""))[:_QUOTE_MAX],
            ))
        return ResearchResult(
            answer=str(parsed.get("answer", "")).strip(),
            evidence=evidence,
            confidence=confidence,
            unclear=str(parsed.get("unclear", "")),
        )

    @staticmethod
    def _fallback_from_first_source(
        sources: list[dict[str, Any]],
        *,
        unclear: str,
    ) -> ResearchResult:
        first = sources[0]
        content = str(first.get("content", ""))
        return ResearchResult(
            answer=content[:_FALLBACK_ANSWER_MAX],
            evidence=[Evidence(
                source_url=str(first.get("url", "")),
                source_name=str(first.get("title", "")),
                quote=content[:_FALLBACK_QUOTE_MAX],
            )],
            confidence="low",
            unclear=unclear,
        )
