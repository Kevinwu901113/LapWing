"""DuckDuckGo 搜索封装 — 优先 DDG，失败时回退 Bing。"""

import asyncio
import logging
import urllib.parse
from html.parser import HTMLParser
from typing import Any

import httpx

from config.settings import SEARCH_PROXY_URL

logger = logging.getLogger("lapwing.tools.web_search")

_BING_TIMEOUT = 10
_BING_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


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


async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """搜索并返回结果列表。优先 DuckDuckGo，失败时回退 Bing。

    Returns:
        结果列表，每条包含 title、url、snippet 字段。搜索失败时返回空列表。
    """
    results = await _ddg_search(query, max_results)
    if results:
        return results

    logger.info(f"[web_search] DDG 无结果，回退 Bing: {query!r}")
    results = await _bing_search(query, max_results)
    if results:
        logger.info(f"[web_search] Bing 回退成功: {len(results)} 条")
    else:
        logger.warning(f"[web_search] DDG 和 Bing 均无结果: {query!r}")
    return results


async def _ddg_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """用 DuckDuckGo 搜索（在线程池中执行）。"""
    try:
        results = await asyncio.to_thread(_sync_ddg_search, query, max_results)
        if results:
            logger.info(f"[web_search] DDG query={query!r} → {len(results)} 条")
        return results
    except Exception as e:
        logger.warning(f"[web_search] DDG 异常 ({type(e).__name__}): {e}")
        return []


def _sync_ddg_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """同步执行 DuckDuckGo 搜索（在线程池中调用）。"""
    from ddgs import DDGS

    results: list[dict[str, Any]] = []
    with DDGS(proxy=SEARCH_PROXY_URL or None) as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return results


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
        logger.warning(f"[web_search] Bing 请求失败 ({type(e).__name__}): {e}")
        return []

    parser = _BingResultParser()
    try:
        parser.feed(response.text)
    except Exception as e:
        logger.warning(f"[web_search] Bing HTML 解析失败: {e}")
        return []

    return parser.results[:max_results]
