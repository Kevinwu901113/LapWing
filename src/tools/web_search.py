"""网页搜索工具，封装 duckduckgo-search。"""

import logging
from dataclasses import dataclass
from duckduckgo_search import AsyncDDGS

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """单条搜索结果。"""
    title: str
    url: str
    snippet: str


async def web_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """执行网页搜索，返回结构化结果。失败时返回空列表。"""
    try:
        async with AsyncDDGS() as ddgs:
            raw = await ddgs.atext(query, max_results=max_results) or []
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            )
            for r in raw
        ][:max_results]
    except Exception as e:
        logger.warning(f"网页搜索失败: {e}")
        return []
