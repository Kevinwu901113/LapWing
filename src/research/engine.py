"""ResearchEngine — 把 scope_router + 搜索后端 + fetcher + refiner 串起来。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.research.types import ResearchResult

logger = logging.getLogger("lapwing.research.engine")

_TOP_K_FETCH = 3
_MAX_CONTENT_PER_SOURCE = 5000
_RESULTS_PER_BACKEND = 5
_RESEARCH_OVERALL_TIMEOUT = 30.0  # 整个 research(question) 的硬上限


class ResearchEngine:
    """research(question) 的编排器。"""

    def __init__(
        self,
        scope_router: Any,
        tavily_backend: Any,
        bocha_backend: Any,
        fetcher: Any,
        refiner: Any,
    ) -> None:
        self.scope_router = scope_router
        self.tavily = tavily_backend
        self.bocha = bocha_backend
        self.fetcher = fetcher
        self.refiner = refiner

    async def research(self, question: str, scope: str = "auto") -> ResearchResult:
        try:
            return await asyncio.wait_for(
                self._research_inner(question, scope),
                timeout=_RESEARCH_OVERALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "research overall timeout %ds: question=%r",
                int(_RESEARCH_OVERALL_TIMEOUT), question[:100],
            )
            return ResearchResult(
                answer=f"查询超时，没能在 {int(_RESEARCH_OVERALL_TIMEOUT)} 秒内完成。",
                confidence="low",
                unclear="查询超时",
            )

    async def _research_inner(self, question: str, scope: str) -> ResearchResult:
        if scope == "auto":
            scope = await self.scope_router.decide(question)

        logger.info("research: question=%r scope=%s", question[:100], scope)

        # 1. 并行搜索
        backends_used: list[str] = []
        search_tasks = []
        if scope in ("global", "both"):
            search_tasks.append(self.tavily.search(question, max_results=_RESULTS_PER_BACKEND))
            backends_used.append("tavily")
        if scope in ("cn", "both"):
            search_tasks.append(self.bocha.search(question, max_results=_RESULTS_PER_BACKEND))
            backends_used.append("bocha")

        if not search_tasks:
            logger.warning("research: scope=%r 未匹配任何后端", scope)
            return ResearchResult(
                answer="没有可用的搜索后端。",
                confidence="low",
                unclear=f"未知 scope: {scope}",
                search_backend_used=backends_used,
            )

        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # 2. 合并去重排序
        candidates = self._merge_and_rank(search_results)
        if not candidates:
            return ResearchResult(
                answer="没有找到相关信息。",
                confidence="low",
                unclear="搜索引擎没有返回结果",
                search_backend_used=backends_used,
            )

        # 3. 并行 fetch top-K
        top = candidates[:_TOP_K_FETCH]
        fetch_results = await asyncio.gather(
            *[self.fetcher.fetch(c["url"]) for c in top],
            return_exceptions=True,
        )

        # 4. 构建精炼输入
        sources = []
        for cand, fetched in zip(top, fetch_results):
            if isinstance(fetched, Exception) or not fetched:
                sources.append({
                    "url": cand["url"],
                    "title": cand["title"],
                    "content": cand["snippet"],
                    "is_fetched": False,
                })
            else:
                sources.append({
                    "url": cand["url"],
                    "title": cand["title"],
                    "content": fetched[:_MAX_CONTENT_PER_SOURCE],
                    "is_fetched": True,
                })

        # 5. 精炼
        result = await self.refiner.refine(question, sources)
        result.search_backend_used = backends_used
        return result

    @staticmethod
    def _merge_and_rank(search_results) -> list[dict[str, Any]]:
        seen_urls: set[str] = set()
        merged: list[dict[str, Any]] = []
        for result in search_results:
            if isinstance(result, Exception):
                logger.warning("搜索后端异常: %s", result)
                continue
            for item in result or []:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    merged.append(item)
        merged.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)
        return merged
