"""ResearchEngine 单元测试 — mock 所有依赖。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.research.engine import ResearchEngine
from src.research.types import Evidence, ResearchResult


def _engine(*, scope_decision="both",
            tavily_results=None, tavily_exc=None,
            bocha_results=None, bocha_exc=None,
            fetch_results=None, fetch_exc=None,
            refine_result=None):
    scope_router = MagicMock()
    scope_router.decide = AsyncMock(return_value=scope_decision)

    tavily = MagicMock()
    if tavily_exc is not None:
        tavily.search = AsyncMock(side_effect=tavily_exc)
    else:
        tavily.search = AsyncMock(return_value=tavily_results or [])

    bocha = MagicMock()
    if bocha_exc is not None:
        bocha.search = AsyncMock(side_effect=bocha_exc)
    else:
        bocha.search = AsyncMock(return_value=bocha_results or [])

    fetcher = MagicMock()
    if fetch_exc is not None:
        fetcher.fetch = AsyncMock(side_effect=fetch_exc)
    elif fetch_results is not None:
        fetcher.fetch = AsyncMock(side_effect=list(fetch_results))
    else:
        fetcher.fetch = AsyncMock(return_value=None)

    refiner = MagicMock()
    refiner.refine = AsyncMock(return_value=refine_result or ResearchResult(answer="default"))

    engine = ResearchEngine(
        scope_router=scope_router,
        tavily_backend=tavily,
        bocha_backend=bocha,
        fetcher=fetcher,
        refiner=refiner,
    )
    return engine, scope_router, tavily, bocha, fetcher, refiner


async def test_scope_auto_calls_router():
    engine, scope_router, tavily, bocha, _, refiner = _engine(
        scope_decision="cn",
        bocha_results=[{"url": "https://cn.com", "title": "t", "snippet": "s", "score": 1.0, "source": "bocha"}],
        fetch_results=["fetched body"],
        refine_result=ResearchResult(answer="ok"),
    )
    result = await engine.research("中文问题", scope="auto")
    scope_router.decide.assert_awaited_once_with("中文问题")
    tavily.search.assert_not_called()
    bocha.search.assert_awaited_once()
    assert result.search_backend_used == ["bocha"]


async def test_explicit_global_only_calls_tavily():
    engine, _, tavily, bocha, _, _ = _engine(
        tavily_results=[{"url": "https://g.com", "title": "t", "snippet": "s", "score": 0.9, "source": "tavily"}],
        fetch_results=["body"],
    )
    result = await engine.research("query", scope="global")
    tavily.search.assert_awaited_once()
    bocha.search.assert_not_called()
    assert result.search_backend_used == ["tavily"]


async def test_both_scope_calls_both_backends_in_parallel():
    engine, _, tavily, bocha, _, refiner = _engine(
        tavily_results=[{"url": "https://a.com", "title": "ta", "snippet": "sa", "score": 0.5, "source": "tavily"}],
        bocha_results=[{"url": "https://b.com", "title": "tb", "snippet": "sb", "score": 0.9, "source": "bocha"}],
        fetch_results=["a body", "b body"],
    )
    await engine.research("q", scope="both")
    tavily.search.assert_awaited_once()
    bocha.search.assert_awaited_once()
    # refine 时 sources 应按 backend 内归一化分排序：Tavily 权重高于 Bocha
    sources = refiner.refine.call_args.args[1]
    assert sources[0]["url"] == "https://a.com"
    assert sources[1]["url"] == "https://b.com"


async def test_backend_scores_are_normalized_before_ranking():
    engine, _, _, _, _, refiner = _engine(
        scope_decision="both",
        tavily_results=[
            {"url": "https://t1.com", "title": "t1", "snippet": "s", "score": 0.6, "source": "tavily"}
        ],
        bocha_results=[
            {"url": "https://b1.com", "title": "b1", "snippet": "s", "score": 0.5, "source": "bocha"},
            {"url": "https://b2.com", "title": "b2", "snippet": "s", "score": 0.25, "source": "bocha"},
        ],
        fetch_results=["t body", "b1 body", "b2 body"],
    )
    await engine.research("q", scope="both")
    sources = refiner.refine.call_args.args[1]
    assert [source["url"] for source in sources] == [
        "https://t1.com",
        "https://b1.com",
        "https://b2.com",
    ]


async def test_one_backend_failure_other_continues():
    engine, _, _, bocha, _, refiner = _engine(
        tavily_exc=RuntimeError("tavily down"),
        bocha_results=[{"url": "https://b.com", "title": "t", "snippet": "s", "score": 1.0, "source": "bocha"}],
        fetch_results=["body"],
    )
    result = await engine.research("q", scope="both")
    # 仍能拿到 bocha 的结果
    refiner.refine.assert_awaited_once()
    assert result.search_backend_used == ["tavily", "bocha"]


async def test_no_search_results_returns_low_confidence():
    engine, _, _, _, _, refiner = _engine(
        tavily_results=[],
        bocha_results=[],
    )
    result = await engine.research("q", scope="both")
    assert result.confidence == 0.3
    assert "没有找到" in result.answer
    refiner.refine.assert_not_called()


async def test_fetch_failure_falls_back_to_snippet():
    engine, _, _, _, _, refiner = _engine(
        scope_decision="global",
        tavily_results=[{"url": "https://x.com", "title": "t", "snippet": "snippet content", "score": 1.0, "source": "tavily"}],
        fetch_exc=RuntimeError("fetch failed"),
    )
    await engine.research("q")
    sources = refiner.refine.call_args.args[1]
    assert sources[0]["is_fetched"] is False
    assert sources[0]["content"] == "snippet content"


async def test_dedup_by_url():
    """tavily 和 bocha 返回同一 URL 时只保留一份。"""
    engine, _, _, _, _, refiner = _engine(
        tavily_results=[{"url": "https://same.com", "title": "ta", "snippet": "sa", "score": 0.9, "source": "tavily"}],
        bocha_results=[{"url": "https://same.com", "title": "tb", "snippet": "sb", "score": 1.0, "source": "bocha"}],
        fetch_results=["body"],
    )
    await engine.research("q", scope="both")
    sources = refiner.refine.call_args.args[1]
    assert len(sources) == 1
    # 第一个加入的 (tavily) 胜出
    assert sources[0]["title"] == "ta"


async def test_top_k_limits_fetches():
    """超过 K 个候选时只 fetch 前 K 个。"""
    candidates = [
        {"url": f"https://r{i}.com", "title": f"t{i}", "snippet": f"s{i}", "score": 1.0 - i * 0.01, "source": "tavily"}
        for i in range(10)
    ]
    engine, _, _, _, fetcher, refiner = _engine(
        scope_decision="global",
        tavily_results=candidates,
        fetch_results=[f"body {i}" for i in range(3)],
    )
    await engine.research("q")
    assert fetcher.fetch.await_count == 3
    sources = refiner.refine.call_args.args[1]
    assert len(sources) == 3


async def test_result_carries_backends_used():
    engine, _, _, _, _, _ = _engine(
        scope_decision="cn",
        bocha_results=[{"url": "https://b.com", "title": "t", "snippet": "s", "score": 1.0, "source": "bocha"}],
        fetch_results=["body"],
        refine_result=ResearchResult(answer="x", confidence="high"),
    )
    result = await engine.research("q")
    assert result.search_backend_used == ["bocha"]
    assert result.confidence == 0.9


async def test_overall_research_timeout(monkeypatch):
    """research() 超过 _RESEARCH_OVERALL_TIMEOUT 时返回 low-confidence 超时结果。"""
    import asyncio as _asyncio
    from src.research import engine as engine_module

    monkeypatch.setattr(engine_module, "_RESEARCH_OVERALL_TIMEOUT", 0.3)

    scope_router = MagicMock()
    scope_router.decide = AsyncMock(return_value="global")
    tavily = MagicMock()

    async def hang_search(*args, **kwargs):
        await _asyncio.sleep(5.0)
        return []

    tavily.search = AsyncMock(side_effect=hang_search)
    bocha = MagicMock()
    bocha.search = AsyncMock(return_value=[])
    fetcher = MagicMock()
    fetcher.fetch = AsyncMock(return_value=None)
    refiner = MagicMock()
    refiner.refine = AsyncMock(return_value=ResearchResult(answer="x"))

    eng = engine_module.ResearchEngine(
        scope_router=scope_router,
        tavily_backend=tavily,
        bocha_backend=bocha,
        fetcher=fetcher,
        refiner=refiner,
    )
    result = await eng.research("q")
    assert result.confidence == 0.3
    assert "查询超时" in result.answer
    assert result.unclear == "查询超时"
