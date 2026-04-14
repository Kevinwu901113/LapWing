"""Web 搜索封装 — 支持 Tavily（主）→ DDG（备）→ Bing（末）三级回退，带内存缓存。"""

import asyncio
import logging
import os
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any

import httpx

from config.settings import (
    SEARCH_CACHE_TTL_SECONDS,
    SEARCH_PROVIDER,
    SEARCH_PROXY_URL,
    TAVILY_API_KEY,
    TAVILY_SEARCH_DEPTH,
)

logger = logging.getLogger("lapwing.tools.web_search")

# 搜索超时配置（秒）
_SEARCH_TIMEOUT = int(os.getenv("SEARCH_TIMEOUT", "15"))
_SEARCH_TOTAL_TIMEOUT = int(os.getenv("SEARCH_TOTAL_TIMEOUT", "25"))

_BING_TIMEOUT = 10
_BING_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# ── 内存缓存 ──────────────────────────────────────────────────────────────────
_search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_MAX_ENTRIES = 200


def _cache_key(query: str, max_results: int) -> str:
    return f"{query.strip().lower()}|{max_results}"


def _cache_get(query: str, max_results: int) -> list[dict[str, Any]] | None:
    if SEARCH_CACHE_TTL_SECONDS <= 0:
        return None
    key = _cache_key(query, max_results)
    entry = _search_cache.get(key)
    if entry is None:
        return None
    ts, results = entry
    if time.monotonic() - ts > SEARCH_CACHE_TTL_SECONDS:
        _search_cache.pop(key, None)
        return None
    logger.debug("[web_search] 缓存命中: %s", key)
    return results


def _cache_put(query: str, max_results: int, results: list[dict[str, Any]]) -> None:
    if SEARCH_CACHE_TTL_SECONDS <= 0 or not results:
        return
    if len(_search_cache) >= _CACHE_MAX_ENTRIES:
        oldest_key = min(_search_cache, key=lambda k: _search_cache[k][0])
        _search_cache.pop(oldest_key, None)
    _search_cache[_cache_key(query, max_results)] = (time.monotonic(), results)


# ── Bing HTML 解析器 ─────────────────────────────────────────────────────────

class _BingResultParser(HTMLParser):
    """从 Bing 搜索结果页提取标题、链接和摘要。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, Any]] = []
        self._in_algo = False        # 在 .b_algo li 内
        self._in_title_a = False     # 在标题 <a> 内
        self._in_caption = False     # 在摘要 <p> 内
        self._depth_algo = 0
        self._current: dict[str, Any] = {}
        self._title_buf: list[str] = []
        self._caption_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_dict = dict(attrs)
        css = attr_dict.get("class", "") or ""

        if tag == "li" and "b_algo" in css:
            self._in_algo = True
            self._depth_algo = 0
            self._current = {}
            self._title_buf = []
            self._caption_buf = []

        if self._in_algo:
            if tag == "li":
                self._depth_algo += 1
            if tag == "a" and not self._current.get("url"):
                href = attr_dict.get("href", "")
                if href.startswith("http"):
                    self._current["url"] = href
                    self._in_title_a = True
            if tag == "p" and ("b_lineclamp" in css or "b_caption" in css or not css):
                if not self._current.get("snippet"):
                    self._in_caption = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_title_a = False
        if tag == "p":
            if self._in_caption and self._caption_buf:
                self._current["snippet"] = " ".join(self._caption_buf).strip()
                self._in_caption = False
        if self._in_algo and tag == "li":
            self._depth_algo -= 1
            if self._depth_algo <= 0:
                self._in_algo = False
                title = " ".join(self._title_buf).strip()
                if self._current.get("url") and title:
                    self._current.setdefault("title", title)
                    self._current.setdefault("snippet", "")
                    self.results.append(dict(self._current))

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._title_buf.append(data)
        elif self._in_caption:
            self._caption_buf.append(data)


# ── 公开接口 ─────────────────────────────────────────────────────────────────

async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """搜索并返回结果列表。根据 SEARCH_PROVIDER 选择引擎，带三级回退和缓存。

    Returns:
        结果列表，每条包含 title、url、snippet 字段。
        Tavily 结果可能额外包含 published_date 和 relevance_score。
    """
    # 1. 检查缓存
    cached = _cache_get(query, max_results)
    if cached is not None:
        return cached

    # 2. 总超时保护
    try:
        results = await asyncio.wait_for(
            _search_with_fallback(query, max_results),
            timeout=_SEARCH_TOTAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("[web_search] 搜索总超时 (%ds): %r", _SEARCH_TOTAL_TIMEOUT, query)
        return []

    if not results:
        logger.warning("[web_search] 所有搜索引擎均无结果: %r", query)

    # 3. 写入缓存
    _cache_put(query, max_results, results)

    return results


async def _search_with_fallback(query: str, max_results: int) -> list[dict[str, Any]]:
    """搜索回退链：Tavily → DDG → Bing。"""
    use_tavily = SEARCH_PROVIDER == "tavily" or (
        SEARCH_PROVIDER == "auto" and TAVILY_API_KEY
    )
    use_ddg = SEARCH_PROVIDER in ("ddg", "auto")

    results: list[dict[str, Any]] = []

    # Tavily
    if use_tavily:
        results = await _tavily_search(query, max_results)

    # DDG 回退
    if not results and use_ddg:
        if use_tavily:
            logger.info("[web_search] Tavily 无结果，回退 DDG: %r", query)
        results = await _ddg_search(query, max_results)

    # Bing 回退
    if not results:
        logger.info("[web_search] DDG 无结果，回退 Bing: %r", query)
        results = await _bing_search(query, max_results)

    return results


# ── Tavily ───────────────────────────────────────────────────────────────────

async def _tavily_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """用 Tavily 搜索（在线程池中执行同步 SDK），带超时保护。"""
    if not TAVILY_API_KEY:
        return []
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_sync_tavily_search, query, max_results),
            timeout=_SEARCH_TIMEOUT,
        )
        if results:
            logger.info("[web_search] Tavily query=%r → %d 条", query, len(results))
        return results
    except asyncio.TimeoutError:
        logger.warning("[web_search] Tavily 搜索超时 (%ds): %r", _SEARCH_TIMEOUT, query)
        return []
    except Exception as e:
        logger.warning("[web_search] Tavily 异常 (%s): %s", type(e).__name__, e)
        return []


def _sync_tavily_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """同步执行 Tavily 搜索（在线程池中调用）。"""
    from tavily import TavilyClient

    client = TavilyClient(api_key=TAVILY_API_KEY)
    response = client.search(
        query=query,
        max_results=max_results,
        search_depth=TAVILY_SEARCH_DEPTH,
        country="cn",
    )

    results: list[dict[str, Any]] = []
    for r in response.get("results", []):
        item: dict[str, Any] = {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        if r.get("published_date"):
            item["published_date"] = r["published_date"]
        if r.get("score") is not None:
            item["relevance_score"] = round(r["score"], 3)
        results.append(item)
    return results


# ── DuckDuckGo ───────────────────────────────────────────────────────────────

async def _ddg_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """用 DuckDuckGo 搜索（在线程池中执行），带超时保护。"""
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_sync_ddg_search, query, max_results),
            timeout=_SEARCH_TIMEOUT,
        )
        if results:
            logger.info("[web_search] DDG query=%r → %d 条", query, len(results))
        return results
    except asyncio.TimeoutError:
        logger.warning("[web_search] DDG 搜索超时 (%ds): %r", _SEARCH_TIMEOUT, query)
        return []
    except Exception as e:
        logger.warning("[web_search] DDG 异常 (%s): %s", type(e).__name__, e)
        return []


def _sync_ddg_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """同步执行 DuckDuckGo 搜索（在线程池中调用）。"""
    from ddgs import DDGS

    results: list[dict[str, Any]] = []
    with DDGS(proxy=SEARCH_PROXY_URL or None) as ddgs:
        for r in ddgs.text(query, max_results=max_results, region="cn-zh"):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return results


# ── Bing ─────────────────────────────────────────────────────────────────────

async def _bing_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """用 Bing 搜索（抓取 cn.bing.com，无需 API key）。"""
    encoded = urllib.parse.quote(query)
    url = f"https://cn.bing.com/search?q={encoded}&setlang=zh-hans&cc=CN"
    try:
        async with httpx.AsyncClient(
            timeout=_BING_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _BING_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            proxy=SEARCH_PROXY_URL or None,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as e:
        logger.warning("[web_search] Bing 请求失败 (%s): %s", type(e).__name__, e)
        return []

    parser = _BingResultParser()
    try:
        parser.feed(response.text)
    except Exception as e:
        logger.warning("[web_search] Bing HTML 解析失败: %s", e)
        return []

    return parser.results[:max_results]
