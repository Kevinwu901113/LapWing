"""web_search 工具单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.web_search import search, _search_cache, _cache_put, _cache_get


def _no_tavily():
    """让 Tavily 不参与的 patch 上下文（模拟无 API key）。"""
    return patch("src.tools.web_search.TAVILY_API_KEY", "")


@pytest.mark.asyncio
class TestWebSearch:

    def setup_method(self):
        _search_cache.clear()

    async def test_returns_list_of_dicts(self):
        """正常搜索返回 dict 列表。"""
        mock_results = [
            {"title": "Python 官网", "url": "https://python.org", "snippet": "Python 编程语言官方网站"},
            {"title": "PyPI", "url": "https://pypi.org", "snippet": "Python 包索引"},
        ]

        with _no_tavily(), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=mock_results)):
            results = await search("Python", max_results=5)

        assert isinstance(results, list)
        assert len(results) == 2

    async def test_maps_fields_correctly(self):
        """搜索结果字段含 title/url/snippet。"""
        mock_results = [
            {"title": "示例标题", "url": "https://example.com", "snippet": "这是摘要内容"},
        ]

        with _no_tavily(), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=mock_results)):
            results = await search("测试", max_results=5)

        assert len(results) == 1
        assert results[0]["title"] == "示例标题"
        assert results[0]["url"] == "https://example.com"
        assert results[0]["snippet"] == "这是摘要内容"

    async def test_falls_back_to_bing_when_ddg_empty(self):
        """DDG 无结果时回退 Bing。"""
        bing_results = [
            {"title": "Bing 结果", "url": "https://bing.com", "snippet": "摘要"},
        ]

        with _no_tavily(), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=[])), \
             patch("src.tools.web_search._bing_search", new=AsyncMock(return_value=bing_results)):
            results = await search("测试")

        assert len(results) == 1
        assert results[0]["title"] == "Bing 结果"

    async def test_returns_empty_list_when_all_fail(self):
        """所有引擎都失败时返回空列表。"""
        with _no_tavily(), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=[])), \
             patch("src.tools.web_search._bing_search", new=AsyncMock(return_value=[])):
            results = await search("不存在的查询xyz123")

        assert results == []


@pytest.mark.asyncio
class TestTavilySearch:

    def setup_method(self):
        _search_cache.clear()

    async def test_tavily_primary_when_key_set(self):
        """TAVILY_API_KEY 有值时 Tavily 作为主引擎。"""
        tavily_results = [
            {"title": "Tavily Result", "url": "https://tavily.com", "snippet": "摘要",
             "published_date": "2026-01-01", "relevance_score": 0.95},
        ]

        with patch("src.tools.web_search.TAVILY_API_KEY", "test-key"), \
             patch("src.tools.web_search.SEARCH_PROVIDER", "auto"), \
             patch("src.tools.web_search._tavily_search", new=AsyncMock(return_value=tavily_results)):
            results = await search("Python", max_results=5)

        assert len(results) == 1
        assert results[0]["title"] == "Tavily Result"
        assert results[0].get("published_date") == "2026-01-01"
        assert results[0].get("relevance_score") == 0.95

    async def test_tavily_fallback_to_ddg(self):
        """Tavily 无结果时回退到 DDG。"""
        ddg_results = [
            {"title": "DDG Result", "url": "https://ddg.com", "snippet": "摘要"},
        ]

        with patch("src.tools.web_search.TAVILY_API_KEY", "test-key"), \
             patch("src.tools.web_search.SEARCH_PROVIDER", "auto"), \
             patch("src.tools.web_search._tavily_search", new=AsyncMock(return_value=[])), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=ddg_results)):
            results = await search("Python")

        assert results[0]["title"] == "DDG Result"

    async def test_ddg_only_mode(self):
        """SEARCH_PROVIDER=ddg 时跳过 Tavily。"""
        ddg_results = [{"title": "DDG", "url": "https://ddg.com", "snippet": "摘要"}]
        mock_tavily = AsyncMock(return_value=[{"title": "Tavily", "url": "...", "snippet": "..."}])

        with patch("src.tools.web_search.SEARCH_PROVIDER", "ddg"), \
             patch("src.tools.web_search._tavily_search", new=mock_tavily), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=ddg_results)):
            results = await search("test")

        mock_tavily.assert_not_awaited()
        assert results[0]["title"] == "DDG"

    async def test_tavily_skipped_when_no_key_auto_mode(self):
        """auto 模式下无 TAVILY_API_KEY 时跳过 Tavily。"""
        ddg_results = [{"title": "DDG", "url": "https://ddg.com", "snippet": "摘要"}]
        mock_tavily = AsyncMock(return_value=[])

        with patch("src.tools.web_search.TAVILY_API_KEY", ""), \
             patch("src.tools.web_search.SEARCH_PROVIDER", "auto"), \
             patch("src.tools.web_search._tavily_search", new=mock_tavily), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=ddg_results)):
            results = await search("test")

        mock_tavily.assert_not_awaited()
        assert results[0]["title"] == "DDG"


class TestSearchCache:

    def setup_method(self):
        _search_cache.clear()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_engines(self):
        """缓存命中时不调用搜索引擎。"""
        with patch("src.tools.web_search.SEARCH_CACHE_TTL_SECONDS", 300):
            _cache_put("cached query", 5, [{"title": "Cached", "url": "https://cached.com", "snippet": "..."}])

        mock_tavily = AsyncMock(return_value=[])
        mock_ddg = AsyncMock(return_value=[])

        with patch("src.tools.web_search.SEARCH_CACHE_TTL_SECONDS", 300), \
             patch("src.tools.web_search.TAVILY_API_KEY", "test-key"), \
             patch("src.tools.web_search._tavily_search", new=mock_tavily), \
             patch("src.tools.web_search._ddg_search", new=mock_ddg):
            results = await search("cached query", max_results=5)

        assert results[0]["title"] == "Cached"
        mock_tavily.assert_not_awaited()
        mock_ddg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_miss_when_disabled(self):
        """TTL=0 时禁用缓存。"""
        with patch("src.tools.web_search.SEARCH_CACHE_TTL_SECONDS", 0):
            _cache_put("query", 5, [{"title": "Old", "url": "...", "snippet": "..."}])

        ddg_results = [{"title": "Fresh", "url": "https://fresh.com", "snippet": "new"}]

        with patch("src.tools.web_search.SEARCH_CACHE_TTL_SECONDS", 0), \
             _no_tavily(), \
             patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=ddg_results)):
            results = await search("query", max_results=5)

        assert results[0]["title"] == "Fresh"

    def test_cache_get_returns_none_when_empty(self):
        """空缓存返回 None。"""
        with patch("src.tools.web_search.SEARCH_CACHE_TTL_SECONDS", 300):
            assert _cache_get("nonexistent", 5) is None
