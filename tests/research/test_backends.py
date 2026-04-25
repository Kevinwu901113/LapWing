"""TavilyBackend / BochaBackend 单元测试 — mock httpx。"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.research.backends.bocha import BochaBackend
from src.research.backends.tavily import TavilyBackend


# ── Tavily ────────────────────────────────────────────────────────────────────


def _mock_async_client(response_json: dict, status: int = 200, raises: Exception | None = None):
    """构造一个 httpx.AsyncClient 的 mock，post 返回指定 json 或抛异常。"""
    response = MagicMock()
    response.json.return_value = response_json
    response.raise_for_status = MagicMock()
    if status >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=MagicMock(status_code=status)
        )

    client = AsyncMock()
    if raises is not None:
        client.post.side_effect = raises
    else:
        client.post.return_value = response

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx, client


async def test_tavily_returns_empty_when_no_api_key():
    backend = TavilyBackend(api_key="")
    assert await backend.search("anything") == []


async def test_tavily_search_parses_results():
    payload = {
        "results": [
            {
                "url": "https://a.com",
                "title": "Title A",
                "content": "Content A " * 100,
                "score": 0.95,
            },
            {
                "url": "https://b.com",
                "title": "Title B",
                "content": "Short B",
                "score": 0.80,
            },
        ]
    }
    ctx, client = _mock_async_client(payload)
    with patch("httpx.AsyncClient", return_value=ctx):
        backend = TavilyBackend(api_key="key", country="china")
        results = await backend.search("query", max_results=2)

    assert len(results) == 2
    assert results[0]["url"] == "https://a.com"
    assert results[0]["title"] == "Title A"
    assert results[0]["score"] == 0.95
    assert results[0]["source"] == "tavily"
    # snippet 截断到 500 字
    assert len(results[0]["snippet"]) <= 500
    assert results[1]["snippet"] == "Short B"

    # 验证请求 payload 结构
    call_kwargs = client.post.call_args.kwargs
    assert call_kwargs["json"]["query"] == "query"
    assert call_kwargs["json"]["max_results"] == 2
    assert call_kwargs["json"]["country"] == "china"
    assert call_kwargs["json"]["api_key"] == "key"


async def test_tavily_returns_empty_on_exception():
    ctx, _ = _mock_async_client({}, raises=httpx.ConnectError("boom"))
    with patch("httpx.AsyncClient", return_value=ctx):
        backend = TavilyBackend(api_key="key")
        assert await backend.search("query") == []


async def test_tavily_handles_missing_score():
    payload = {"results": [{"url": "u", "title": "t", "content": "c"}]}
    ctx, _ = _mock_async_client(payload)
    with patch("httpx.AsyncClient", return_value=ctx):
        backend = TavilyBackend(api_key="key")
        results = await backend.search("q")
    assert results[0]["score"] == 0


# ── Bocha ─────────────────────────────────────────────────────────────────────


async def test_bocha_returns_empty_when_no_api_key():
    backend = BochaBackend(api_key="")
    assert await backend.search("anything") == []


async def test_bocha_search_parses_results():
    payload = {
        "code": 200,
        "msg": "ok",
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "中文标题 1",
                        "url": "https://cn.com/1",
                        "snippet": "短摘要 1",
                        "summary": "完整摘要 1，比 snippet 更长更详细。",
                        "siteName": "示例站",
                        "datePublished": "2026-04-15",
                    },
                    {
                        "name": "中文标题 2",
                        "url": "https://cn.com/2",
                        "snippet": "只有 snippet",
                        "siteName": "另一站",
                    },
                ]
            }
        },
    }
    ctx, client = _mock_async_client(payload)
    with patch("httpx.AsyncClient", return_value=ctx):
        backend = BochaBackend(api_key="bocha-key")
        results = await backend.search("查询", max_results=2)

    assert len(results) == 2
    assert results[0]["url"] == "https://cn.com/1"
    assert results[0]["title"] == "中文标题 1"
    # 优先用 summary
    assert "完整摘要" in results[0]["snippet"]
    assert results[0]["source"] == "bocha"
    assert results[0]["score"] > results[1]["score"]
    # 没有 summary 时用 snippet
    assert results[1]["snippet"] == "只有 snippet"

    # 验证请求结构
    call_kwargs = client.post.call_args.kwargs
    assert call_kwargs["json"]["query"] == "查询"
    assert call_kwargs["json"]["count"] == 2
    assert call_kwargs["json"]["summary"] is True
    assert call_kwargs["headers"]["Authorization"] == "Bearer bocha-key"


async def test_bocha_handles_missing_data():
    """博查偶尔返回结构缺失，不应抛异常。"""
    ctx, _ = _mock_async_client({"code": 500, "msg": "error"})
    with patch("httpx.AsyncClient", return_value=ctx):
        backend = BochaBackend(api_key="key")
        assert await backend.search("q") == []


async def test_bocha_returns_empty_on_exception():
    ctx, _ = _mock_async_client({}, raises=httpx.TimeoutException("t/o"))
    with patch("httpx.AsyncClient", return_value=ctx):
        backend = BochaBackend(api_key="key")
        assert await backend.search("q") == []
