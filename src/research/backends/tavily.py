"""Tavily 搜索后端 — 海外内容首选。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.research.backends.base import SearchBackend

logger = logging.getLogger("lapwing.research.backends.tavily")

_API_URL = "https://api.tavily.com/search"
_TIMEOUT = 10.0
_SNIPPET_MAX = 500


class TavilyBackend(SearchBackend):
    """Tavily REST API 后端。"""

    def __init__(self, api_key: str, country: str = "china") -> None:
        self.api_key = api_key
        self.country = country

    @staticmethod
    async def _do_search(payload: dict) -> dict:
        from src.utils.retry import async_retry

        @async_retry(max_attempts=3)
        async def _request(p):
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(_API_URL, json=p)
                response.raise_for_status()
                return response.json()

        return await _request(payload)

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        if not self.api_key:
            logger.debug("Tavily api_key 为空，跳过")
            return []

        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "country": self.country,
            "search_depth": "basic",
        }

        try:
            data = await self._do_search(payload)
        except Exception as exc:
            logger.warning("Tavily 请求失败（重试耗尽）: %s", exc)
            return []

        results: list[dict[str, Any]] = []
        for item in data.get("results", []):
            content = item.get("content", "") or ""
            results.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": content[:_SNIPPET_MAX],
                "score": item.get("score", 0) or 0,
                "source": "tavily",
            })
        return results
