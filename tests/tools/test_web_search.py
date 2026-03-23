"""web_search 工具单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.web_search import web_search, SearchResult


@pytest.mark.asyncio
class TestWebSearch:

    async def test_returns_list_of_search_results(self):
        """正常搜索返回 SearchResult 列表。"""
        mock_raw = [
            {"title": "Python 官网", "href": "https://python.org", "body": "Python 编程语言官方网站"},
            {"title": "PyPI", "href": "https://pypi.org", "body": "Python 包索引"},
        ]

        mock_ddgs = AsyncMock()
        mock_ddgs.atext = AsyncMock(return_value=mock_raw)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ddgs)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("src.tools.web_search.AsyncDDGS", return_value=mock_ctx):
            results = await web_search("Python", max_results=5)

        assert isinstance(results, list)
        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)

    async def test_maps_fields_correctly(self):
        """SearchResult 字段正确映射（title/href/body → title/url/snippet）。"""
        mock_raw = [
            {"title": "示例标题", "href": "https://example.com", "body": "这是摘要内容"},
        ]

        mock_ddgs = AsyncMock()
        mock_ddgs.atext = AsyncMock(return_value=mock_raw)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ddgs)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("src.tools.web_search.AsyncDDGS", return_value=mock_ctx):
            results = await web_search("测试", max_results=5)

        assert len(results) == 1
        assert results[0].title == "示例标题"
        assert results[0].url == "https://example.com"
        assert results[0].snippet == "这是摘要内容"

    async def test_max_results_forwarded_to_library(self):
        """max_results 参数被正确传递给底层库。"""
        mock_raw = [
            {"title": f"结果{i}", "href": f"https://example.com/{i}", "body": f"摘要{i}"}
            for i in range(3)
        ]

        mock_ddgs = AsyncMock()
        mock_ddgs.atext = AsyncMock(return_value=mock_raw)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ddgs)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("src.tools.web_search.AsyncDDGS", return_value=mock_ctx):
            results = await web_search("测试", max_results=3)

        # 验证 atext 被调用时传入了正确的 max_results 参数
        mock_ddgs.atext.assert_called_once_with("测试", max_results=3)
        assert len(results) == 3

    async def test_slices_results_when_library_over_returns(self):
        """当库返回超过 max_results 的结果时，截断到 max_results 条。"""
        mock_raw = [
            {"title": f"结果{i}", "href": f"https://example.com/{i}", "body": f"摘要{i}"}
            for i in range(5)
        ]

        mock_ddgs = AsyncMock()
        mock_ddgs.atext = AsyncMock(return_value=mock_raw)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ddgs)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("src.tools.web_search.AsyncDDGS", return_value=mock_ctx):
            results = await web_search("测试", max_results=3)

        assert len(results) == 3

    async def test_returns_empty_list_on_exception(self):
        """AsyncDDGS 抛异常时返回空列表。"""
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("网络连接失败"))
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("src.tools.web_search.AsyncDDGS", return_value=mock_ctx):
            results = await web_search("测试")

        assert results == []

    async def test_returns_empty_list_for_empty_results(self):
        """搜索结果为空时返回空列表。"""
        mock_ddgs = AsyncMock()
        mock_ddgs.atext = AsyncMock(return_value=[])

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ddgs)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("src.tools.web_search.AsyncDDGS", return_value=mock_ctx):
            results = await web_search("不存在的查询词xyz123")

        assert results == []
