"""SmartFetcher — 先用 httpx 抓静态页面，必要时降级到 browser_manager。"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("lapwing.research.fetcher")

_TIMEOUT = 10.0
_USER_AGENT = "Mozilla/5.0 (compatible; Lapwing/1.0)"
_SPA_INDICATOR_THRESHOLD = 3000
_SPA_NAV_KEYWORDS = (
    "sign in", "log in", "登录", "menu", "navigation", "home", "cookie",
    "subscribe", "loading...",
)
_SPA_NAV_HIT_THRESHOLD = 3

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class SmartFetcher:
    """带 fallback 的网页抓取器。

    流程：
      1. httpx GET 拿 HTML → 提取正文
      2. 文本太短或像 SPA 外壳 → 用 browser_manager 重新打开（执行 JS 后取文本）
      3. 都失败 → 返回 None
    """

    def __init__(self, browser_manager: Any | None = None) -> None:
        self.browser_manager = browser_manager

    async def fetch(self, url: str) -> str | None:
        text = await self._httpx_fetch(url)

        if text and len(text) >= _SPA_INDICATOR_THRESHOLD and not self._looks_like_spa(text):
            return text

        if self.browser_manager is not None:
            try:
                logger.info("fetcher: 浏览器降级抓取 %s", url)
                browser_text = await self._browser_fetch(url)
                if browser_text:
                    return browser_text
            except Exception as exc:
                logger.warning("浏览器抓取失败 %s: %s", url, exc)

        return text

    async def _httpx_fetch(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except Exception as exc:
            logger.warning("httpx 抓取失败 %s: %s", url, exc)
            return None
        return self._extract_text(response.text)

    @staticmethod
    def _extract_text(html: str) -> str:
        text = _SCRIPT_RE.sub(" ", html)
        text = _STYLE_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)
        return _WHITESPACE_RE.sub(" ", text).strip()

    @staticmethod
    def _looks_like_spa(text: str) -> bool:
        # 长文本基本可信
        if len(text) > 5000:
            return False
        lowered = text.lower()
        hits = sum(1 for kw in _SPA_NAV_KEYWORDS if kw in lowered)
        return hits >= _SPA_NAV_HIT_THRESHOLD

    async def _browser_fetch(self, url: str) -> str | None:
        tab_info = await self.browser_manager.new_tab(url)
        tab_id = tab_info.tab_id
        try:
            text = await self.browser_manager.get_page_text(tab_id=tab_id)
            if text:
                return _WHITESPACE_RE.sub(" ", text).strip()
            return None
        finally:
            try:
                await self.browser_manager.close_tab(tab_id)
            except Exception as exc:
                logger.debug("close_tab 异常 %s: %s", tab_id, exc)
