"""Refiner 单元测试 — mock router.complete_structured (主路径) + router.complete (fallback)。"""

import json
from unittest.mock import AsyncMock

import pytest

from src.research.refiner import Refiner
from src.research.types import ResearchResult


def _router(*, structured=None, complete=None, structured_exc=None, complete_exc=None):
    """构造一个带可控 complete_structured / complete 的 mock router。"""
    router = AsyncMock()
    if structured_exc is not None:
        router.complete_structured = AsyncMock(side_effect=structured_exc)
    else:
        router.complete_structured = AsyncMock(return_value=structured)
    if complete_exc is not None:
        router.complete = AsyncMock(side_effect=complete_exc)
    else:
        router.complete = AsyncMock(return_value=complete)
    return router


def _sources():
    return [
        {"url": "https://a.com", "title": "Source A", "content": "A 的正文内容", "is_fetched": True},
        {"url": "https://b.com", "title": "Source B", "content": "B 的内容", "is_fetched": False},
    ]


# ── 主路径：complete_structured 成功 ──────────────────────────────────────────


async def test_empty_sources_returns_low_confidence_no_call():
    router = _router(structured={"won't": "be called"})
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("question", [])
    assert isinstance(result, ResearchResult)
    assert result.confidence == 0.3
    assert "没有找到" in result.answer
    router.complete_structured.assert_not_called()
    router.complete.assert_not_called()


async def test_structured_path_normal():
    router = _router(structured={
        "answer": "综合后的答案",
        "evidence": [
            {"source_url": "https://a.com", "source_name": "Source A", "quote": "关键句子 A"},
        ],
        "confidence": "high",
        "unclear": "",
    })
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("question", _sources())
    assert result.answer == "综合后的答案"
    assert result.confidence == 0.9
    assert len(result.evidence) == 1
    assert result.evidence[0].quote == "关键句子 A"

    # complete_structured 被调用，complete 没被调用
    router.complete_structured.assert_awaited_once()
    router.complete.assert_not_called()
    call_kwargs = router.complete_structured.call_args.kwargs
    assert call_kwargs["purpose"] == "tool"
    assert call_kwargs["max_tokens"] == 1500
    assert call_kwargs["result_tool_name"] == "submit_research_result"


async def test_structured_invalid_confidence_normalized():
    router = _router(structured={
        "answer": "x",
        "evidence": [],
        "confidence": "very-high",
        "unclear": "",
    })
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.confidence == 0.6


async def test_structured_evidence_quote_truncated():
    router = _router(structured={
        "answer": "a",
        "evidence": [{"source_url": "u", "source_name": "n", "quote": "x" * 1000}],
        "confidence": "medium",
        "unclear": "",
    })
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert len(result.evidence[0].quote) == 300


async def test_structured_malformed_evidence_skipped():
    router = _router(structured={
        "answer": "a",
        "evidence": ["not a dict", {"source_url": "u", "source_name": "n", "quote": "q"}],
        "confidence": "high",
        "unclear": "",
    })
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert len(result.evidence) == 1
    assert result.evidence[0].source_url == "u"


# ── Fallback 1：complete_structured 失败 → 文本 JSON 解析 ─────────────────────


async def test_structured_fails_falls_back_to_text_json():
    router = _router(
        structured_exc=RuntimeError("forced tool not supported"),
        complete=json.dumps({
            "answer": "fallback ok",
            "evidence": [],
            "confidence": "medium",
            "unclear": "",
        }, ensure_ascii=False),
    )
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.answer == "fallback ok"
    router.complete_structured.assert_awaited_once()
    router.complete.assert_awaited_once()


async def test_text_fallback_handles_code_fence():
    payload = {"answer": "fenced", "evidence": [], "confidence": "medium", "unclear": ""}
    router = _router(
        structured_exc=RuntimeError("nope"),
        complete="```json\n" + json.dumps(payload) + "\n```",
    )
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.answer == "fenced"


async def test_text_fallback_invalid_json_returns_low_confidence():
    router = _router(
        structured_exc=RuntimeError("nope"),
        complete="这不是 JSON 只是一段文字回答",
    )
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.confidence == 0.3
    assert "无法解析" in result.unclear
    assert "这不是 JSON" in result.answer


# ── Fallback 2：两层都失败 → 第一个 source 摘要 ──────────────────────────────


async def test_both_paths_fail_returns_first_source_summary():
    router = _router(
        structured_exc=RuntimeError("structured down"),
        complete_exc=RuntimeError("text also down"),
    )
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.confidence == 0.3
    assert "精炼失败" in result.unclear
    assert result.evidence[0].source_url == "https://a.com"
    assert "A 的正文内容" in result.answer
