"""Refiner 单元测试 — mock router.complete。"""

import json
from unittest.mock import AsyncMock

import pytest

from src.research.refiner import Refiner
from src.research.types import ResearchResult


def _router(response_or_exc):
    router = AsyncMock()
    if isinstance(response_or_exc, Exception):
        router.complete.side_effect = response_or_exc
    else:
        router.complete.return_value = response_or_exc
    return router


def _sources():
    return [
        {"url": "https://a.com", "title": "Source A", "content": "A 的正文内容", "is_fetched": True},
        {"url": "https://b.com", "title": "Source B", "content": "B 的内容", "is_fetched": False},
    ]


async def test_empty_sources_returns_low_confidence_no_call():
    router = _router("won't be called")
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("question", [])
    assert isinstance(result, ResearchResult)
    assert result.confidence == "low"
    assert "没有找到" in result.answer
    router.complete.assert_not_called()


async def test_normal_json_response():
    payload = {
        "answer": "综合后的答案",
        "evidence": [
            {"source_url": "https://a.com", "source_name": "Source A", "quote": "关键句子 A"},
        ],
        "confidence": "high",
        "unclear": "",
    }
    router = _router(json.dumps(payload, ensure_ascii=False))
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("question", _sources())
    assert result.answer == "综合后的答案"
    assert result.confidence == "high"
    assert len(result.evidence) == 1
    assert result.evidence[0].quote == "关键句子 A"

    # 验证 router 调用参数
    call_kwargs = router.complete.call_args.kwargs
    assert call_kwargs["purpose"] == "tool"
    assert call_kwargs["max_tokens"] == 1500
    msgs = call_kwargs["messages"]
    assert msgs[0]["role"] == "user"
    assert "question" in msgs[0]["content"]


async def test_json_with_code_fence_is_parsed():
    payload = {"answer": "解析", "evidence": [], "confidence": "medium", "unclear": ""}
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    router = _router(fenced)
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.answer == "解析"


async def test_invalid_json_falls_back_to_low_confidence():
    router = _router("这不是 JSON，只是一段文字回答")
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.confidence == "low"
    assert "无法解析" in result.unclear
    assert "这不是 JSON" in result.answer


async def test_llm_exception_returns_first_source_summary():
    router = _router(RuntimeError("API down"))
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.confidence == "low"
    assert "精炼失败" in result.unclear
    assert result.evidence[0].source_url == "https://a.com"
    assert "A 的正文内容" in result.answer


async def test_invalid_confidence_value_normalized_to_medium():
    payload = {"answer": "x", "evidence": [], "confidence": "very-high", "unclear": ""}
    router = _router(json.dumps(payload))
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert result.confidence == "medium"


async def test_evidence_quote_is_truncated():
    long_quote = "x" * 1000
    payload = {
        "answer": "a",
        "evidence": [{"source_url": "u", "source_name": "n", "quote": long_quote}],
        "confidence": "medium",
        "unclear": "",
    }
    router = _router(json.dumps(payload))
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert len(result.evidence[0].quote) == 300


async def test_malformed_evidence_entries_are_skipped():
    payload = {
        "answer": "a",
        "evidence": ["not a dict", {"source_url": "u", "source_name": "n", "quote": "q"}],
        "confidence": "high",
        "unclear": "",
    }
    router = _router(json.dumps(payload))
    refiner = Refiner(llm_router=router)
    result = await refiner.refine("q", _sources())
    assert len(result.evidence) == 1
    assert result.evidence[0].source_url == "u"
