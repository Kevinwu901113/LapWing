"""博查 (Bocha AI) 搜索后端 — 国内内容首选。

API 文档：https://open.bochaai.com/
请求：POST https://api.bochaai.com/v1/web-search
鉴权：Bearer token
响应：data.webPages.value[] 含 name / url / snippet / summary / siteName / datePublished
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.research.backends.base import SearchBackend

logger = logging.getLogger("lapwing.research.backends.bocha")

_API_URL = "https://api.bochaai.com/v1/web-search"
_TIMEOUT = 10.0
_SNIPPET_MAX = 500


class BochaBackend(SearchBackend):
    """博查 Web Search REST API 后端。"""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        if not self.api_key:
            logger.debug("Bocha api_key 为空，跳过")
            return []

        payload = {
            "query": query,
            "count": max_results,
            "freshness": "noLimit",
            "summary": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(_API_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("Bocha 请求失败: %s", exc)
            return []

        # 博查返回结构：{"code": 200, "msg": "ok", "data": {"webPages": {"value": [...]}}}
        web_pages = (
            data.get("data", {})
            .get("webPages", {})
            .get("value", [])
            if isinstance(data, dict) else []
        )

        results: list[dict[str, Any]] = []
        for item in web_pages:
            # summary 比 snippet 更详细，优先用 summary
            content = item.get("summary") or item.get("snippet") or ""
            results.append({
                "url": item.get("url", ""),
                "title": item.get("name", ""),
                "snippet": content[:_SNIPPET_MAX],
                "score": 1.0,  # 博查不返回 score，用 1.0 让排序保持插入顺序
                "source": "bocha",
            })
        return results
