"""Refiner — 把多源搜索结果交给 LLM 精炼成结构化答案。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.research.prompts import REFINE_PROMPT, REFINE_PROMPT_TEXT_FALLBACK
from src.research.types import Evidence, ResearchResult

logger = logging.getLogger("lapwing.research.refiner")

_MAX_TOKENS = 1500
_QUOTE_MAX = 300
_FALLBACK_ANSWER_MAX = 500
_FALLBACK_QUOTE_MAX = 300

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)

_RESULT_TOOL_NAME = "submit_research_result"
_RESULT_TOOL_DESCRIPTION = "提交研究综合结果。answer 是给用户看的答案；evidence 是带出处的关键引文；confidence 是高/中/低；unclear 是不确定或矛盾的地方。"
_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "综合答案（50-200 字），只用 sources 里出现过的事实。",
        },
        "evidence": {
            "type": "array",
            "description": "支撑 answer 的关键引文，每条标注 URL。",
            "items": {
                "type": "object",
                "properties": {
                    "source_url": {"type": "string"},
                    "source_name": {"type": "string"},
                    "quote": {"type": "string", "description": "从原文截取的关键句"},
                },
                "required": ["source_url", "source_name", "quote"],
            },
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "unclear": {
            "type": "string",
            "description": "矛盾、歧义、缺失的细节写在这里；没有就空字符串。",
        },
    },
    "required": ["answer", "confidence"],
}


class Refiner:
    """优先用 router.complete_structured 获取强制结构化的 dict；失败回退到文本 JSON 解析。"""

    def __init__(self, llm_router: Any) -> None:
        self.llm_router = llm_router

    async def refine(self, question: str, sources: list[dict[str, Any]]) -> ResearchResult:
        if not sources:
            return ResearchResult(
                answer="没有找到相关信息。",
                confidence="low",
            )

        sources_text = self._format_sources(sources)

        # 主路径：强制 tool call，直接拿 dict
        try:
            parsed = await self.llm_router.complete_structured(
                messages=[{
                    "role": "user",
                    "content": REFINE_PROMPT.format(question=question, sources=sources_text),
                }],
                result_schema=_RESULT_SCHEMA,
                result_tool_name=_RESULT_TOOL_NAME,
                result_tool_description=_RESULT_TOOL_DESCRIPTION,
                purpose="tool",
                max_tokens=_MAX_TOKENS,
            )
            return self._result_from_parsed(parsed)
        except Exception as exc:
            logger.warning("Refiner complete_structured 失败，回退到文本 JSON 模式: %s", exc)

        # Fallback 1：文本 JSON 解析
        try:
            response = await self.llm_router.complete(
                messages=[{
                    "role": "user",
                    "content": REFINE_PROMPT_TEXT_FALLBACK.format(question=question, sources=sources_text),
                }],
                purpose="tool",
                max_tokens=_MAX_TOKENS,
            )
        except Exception as exc:
            logger.error("Refine text fallback 也失败: %s", exc)
            return self._fallback_from_first_source(
                sources, unclear=f"精炼失败（{exc}），返回原始摘要"
            )

        try:
            parsed = self._parse_json(response or "")
            return self._result_from_parsed(parsed)
        except Exception as exc:
            logger.warning("Refiner JSON 解析失败: %s | response=%r", exc, (response or "")[:300])
            return ResearchResult(
                answer=(response or "")[:_FALLBACK_ANSWER_MAX].strip()
                or "精炼结果为空。",
                confidence="low",
                unclear="精炼结果无法解析为结构化数据",
            )

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
