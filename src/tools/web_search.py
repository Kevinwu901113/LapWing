"""DuckDuckGo 搜索封装 — 提供异步搜索接口。"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger("lapwing.tools.web_search")


async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """搜索并返回结果列表。

    Args:
        query: 搜索关键词
        max_results: 最多返回条数

    Returns:
        结果列表，每条包含 title、url、snippet 字段。
        搜索失败时返回空列表。
    """
    try:
        results = await asyncio.to_thread(_sync_search, query, max_results)
        logger.info(f"[web_search] query={query!r} → {len(results)} 条结果")
        return results
    except Exception as e:
        logger.warning(f"[web_search] 搜索失败: {e}")
        return []


def _sync_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """同步执行 DuckDuckGo 搜索（在线程池中调用）。"""
    from duckduckgo_search import DDGS

    results: list[dict[str, Any]] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return results
