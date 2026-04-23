"""SmartFetcher — 先用 httpx 抓静态页面，必要时降级到 browser_manager。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

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

# 超时预算（秒）
_FETCH_OVERALL_TIMEOUT = 15.0    # httpx + browser 合计上限
_BROWSER_FETCH_TIMEOUT = 8.0     # 浏览器降级单次上限
_BROWSER_CLOSE_TIMEOUT = 3.0     # close_tab 清理上限

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# 视频/音频/二进制媒体域名 — 文本抓取毫无价值，跳过避免 SPA 抓取卡死。
_BLACKLIST_DOMAINS = (
    "youtube.com", "youtu.be",
    "vimeo.com",
    "tiktok.com",
    "twitch.tv",
    "douyin.com",
    "open.spotify.com",
    "music.apple.com",
)
# B 站视频页（视频播放页）单独处理，避免误伤文章/专栏页。
_BLACKLIST_PATH_PREFIXES = (
    ("bilibili.com", "/video/"),
    ("www.bilibili.com", "/video/"),
    ("b23.tv", "/"),
)
# 二进制媒体后缀
_BLACKLIST_SUFFIXES = (
    ".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi",
    ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".dmg", ".iso", ".exe",
)


def _is_blacklisted(url: str) -> bool:
    """判断 URL 是否属于不抓取的视频/音频/二进制资源。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if not host:
        return False

    for domain in _BLACKLIST_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True

    for prefix_host, prefix_path in _BLACKLIST_PATH_PREFIXES:
        if (host == prefix_host or host.endswith("." + prefix_host)) and path.startswith(prefix_path):
            return True

    for suffix in _BLACKLIST_SUFFIXES:
        if path.endswith(suffix):
            return True

    return False


class SmartFetcher:
    """带 fallback 的网页抓取器。

    流程：
      1. httpx GET 拿 HTML → 提取正文
      2. 文本太短或像 SPA 外壳 → 用 browser_manager 重新打开（执行 JS 后取文本）
      3. 都失败 → 返回 None

    超时保护：
      - fetch() 整体最多 15s（httpx + browser）
      - _browser_fetch() 单次最多 8s
    """

    def __init__(self, browser_manager: Any | None = None, proxy_router: Any | None = None) -> None:
        self.browser_manager = browser_manager
        self.proxy_router = proxy_router

    async def fetch(self, url: str) -> str | None:
        if _is_blacklisted(url):
            logger.info("fetcher: skip blacklisted url %s", url)
            return None
        try:
            return await asyncio.wait_for(
                self._fetch_inner(url),
                timeout=_FETCH_OVERALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("fetch overall timeout %ds: %s", int(_FETCH_OVERALL_TIMEOUT), url)
            return None

    async def _fetch_inner(self, url: str) -> str | None:
        text, used_strategy = await self._httpx_fetch(url)

        # 代理相关失败时尝试切换策略重试
        if text is None and used_strategy is not None and self.proxy_router is not None:
            alt = self.proxy_router.report_failure_and_get_alternative(url, used_strategy)
            if alt is not None:
                logger.info("fetcher: 切换代理策略重试 %s (new=%s)", url, alt.strategy)
                text, _ = await self._httpx_fetch_with_decision(url, alt)
                if text:
                    self.proxy_router.confirm_alternative(url, alt.strategy)

        # SPA / 浏览器降级（现有逻辑不变）
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

    async def _httpx_fetch(self, url: str) -> tuple[str | None, str | None]:
        """解析代理策略，委托给 _httpx_fetch_with_decision。

        返回 (text, strategy)；strategy 为 None 表示未使用 proxy_router。
        """
        if self.proxy_router is None:
            text, _ = await self._httpx_fetch_with_decision(url, None)
            return text, None

        decision = self.proxy_router.resolve(url)
        text, is_proxy_failure = await self._httpx_fetch_with_decision(url, decision)
        if text is not None:
            self.proxy_router.report_success(url, decision.strategy)
            return text, None  # 成功后无需上层重试
        # is_proxy_failure=True 时，上层可用 strategy 触发 report_failure_and_get_alternative
        return None, decision.strategy if is_proxy_failure else None

    async def _httpx_fetch_with_decision(
        self, url: str, decision: Any | None
    ) -> tuple[str | None, bool]:
        """用给定的代理决策发起 httpx 请求。

        返回 (text, is_proxy_related_failure)：
          - text: 提取后的正文，失败时为 None
          - is_proxy_related_failure: True 表示失败可能由代理引起，应尝试切换策略
        """
        proxy_url = decision.proxy_url if decision is not None else None
        strategy = decision.strategy if decision is not None else "direct"

        client_kwargs: dict = dict(
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        if proxy_url:
            client_kwargs["proxies"] = {"all://": proxy_url}

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("httpx 抓取失败 %s [%s] strategy=%s: %s", url, status, strategy, exc)
            # 403 / 429 可能是代理被拦截，触发切换
            return None, status in (403, 429)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.warning("httpx 连接失败 %s strategy=%s: %s", url, strategy, exc)
            return None, True
        except Exception as exc:
            logger.warning("httpx 抓取失败 %s strategy=%s: %s", url, strategy, exc)
            return None, False

        return self._extract_text(response.text), False

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
        """浏览器降级。硬超时 _BROWSER_FETCH_TIMEOUT 秒。"""
        tab_id_box: dict[str, str] = {}

        async def _work() -> str | None:
            tab_info = await self.browser_manager.new_tab(url)
            tab_id_box["id"] = tab_info.tab_id
            return await self.browser_manager.get_page_text(tab_id=tab_info.tab_id)

        try:
            text = await asyncio.wait_for(_work(), timeout=_BROWSER_FETCH_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("browser fetch timeout %ds: %s", int(_BROWSER_FETCH_TIMEOUT), url)
            text = None
        finally:
            tab_id = tab_id_box.get("id")
            if tab_id is not None:
                try:
                    await asyncio.wait_for(
                        self.browser_manager.close_tab(tab_id),
                        timeout=_BROWSER_CLOSE_TIMEOUT,
                    )
                except Exception as exc:
                    logger.debug("close_tab 异常 %s: %s", tab_id, exc)

        if text:
            return _WHITESPACE_RE.sub(" ", text).strip()
        return None
