"""web_search 工具单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.web_search import search


@pytest.mark.asyncio
class TestWebSearch:

    async def test_returns_list_of_dicts(self):
        """正常搜索返回 dict 列表。"""
        mock_results = [
            {"title": "Python 官网", "url": "https://python.org", "snippet": "Python 编程语言官方网站"},
            {"title": "PyPI", "url": "https://pypi.org", "snippet": "Python 包索引"},
        ]

        with patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=mock_results)):
            results = await search("Python", max_results=5)

        assert isinstance(results, list)
        assert len(results) == 2

    async def test_maps_fields_correctly(self):
        """搜索结果字段含 title/url/snippet。"""
        mock_results = [
            {"title": "示例标题", "url": "https://example.com", "snippet": "这是摘要内容"},
        ]

        with patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=mock_results)):
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

        with patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=[])), \
             patch("src.tools.web_search._bing_search", new=AsyncMock(return_value=bing_results)):
            results = await search("测试")

        assert len(results) == 1
        assert results[0]["title"] == "Bing 结果"

    async def test_returns_empty_list_when_both_fail(self):
        """DDG 和 Bing 都失败时返回空列表。"""
        with patch("src.tools.web_search._ddg_search", new=AsyncMock(return_value=[])), \
             patch("src.tools.web_search._bing_search", new=AsyncMock(return_value=[])):
            results = await search("不存在的查询xyz123")

        assert results == []
