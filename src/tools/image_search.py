"""image_search 工具 — 使用 DuckDuckGo 搜索图片，返回可直接用于 send_image 的 URL 列表。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.settings import SEARCH_PROXY_URL
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.image_search")


def _sync_ddg_image_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """同步执行 DuckDuckGo 图片搜索（在线程池中调用）。"""
    from ddgs import DDGS

    results: list[dict[str, Any]] = []
    with DDGS(proxy=SEARCH_PROXY_URL or None) as ddgs:
        for r in ddgs.images(keywords=query, max_results=max_results):
            url = r.get("image", "")
            if url:
                results.append({
                    "url": url,
                    "thumbnail": r.get("thumbnail", ""),
                    "title": r.get("title", ""),
                    "source": r.get("url", ""),
                })
    return results


async def _execute_image_search(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    args = request.arguments
    query = str(args.get("query", "")).strip()
    max_results = int(args.get("max_results", 5))

    if not query:
        return ToolExecutionResult(
            success=False,
            payload={"error": "搜索关键词不能为空"},
            reason="missing query",
        )

    try:
        images = await asyncio.to_thread(_sync_ddg_image_search, query, max_results)
    except Exception as exc:
        logger.warning("图片搜索失败: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"图片搜索出错: {exc}"},
            reason=str(exc),
        )

    if not images:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"没有找到关于 '{query}' 的图片"},
            reason="no results",
        )

    logger.info("[image_search] query=%r → %d 条", query, len(images))
    return ToolExecutionResult(
        success=True,
        payload={"images": images, "count": len(images)},
    )


IMAGE_SEARCH_EXECUTORS = {
    "image_search": _execute_image_search,
}
