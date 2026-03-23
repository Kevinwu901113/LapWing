"""网页抓取工具 - 获取 HTML 标题和正文纯文本。"""

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

logger = logging.getLogger("lapwing.tools.web_fetcher")

_MAX_TEXT = 4000
_TIMEOUT = 10
_USER_AGENT = "Lapwing/1.0 (personal assistant)"


@dataclass
class FetchResult:
    url: str
    title: str
    text: str
    success: bool
    error: str


class _HTMLTextExtractor(HTMLParser):
    """提取 title 和正文纯文本。"""

    _SKIP_TAGS = {"script", "style", "nav", "header", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        if lowered in self._SKIP_TAGS:
            self._skip_depth += 1
        elif lowered == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        self._text_parts.append(data)

    @property
    def title(self) -> str:
        return _normalize_text(" ".join(self._title_parts))

    @property
    def text(self) -> str:
        return _normalize_text(" ".join(self._text_parts))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


async def fetch(url: str) -> FetchResult:
    """抓取指定 URL，返回标题和正文纯文本。"""
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.warning(f"[web_fetcher] 请求超时: {url} ({exc})")
        return FetchResult(url=url, title="", text="", success=False, error="请求超时")
    except httpx.ConnectError as exc:
        logger.warning(f"[web_fetcher] 连接失败: {url} ({exc})")
        return FetchResult(url=url, title="", text="", success=False, error="连接失败")
    except Exception as exc:
        logger.warning(f"[web_fetcher] 抓取失败: {url} ({exc})")
        return FetchResult(url=url, title="", text="", success=False, error=str(exc))

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        error = f"不支持的内容类型: {content_type or 'unknown'}"
        logger.info(f"[web_fetcher] 非 HTML 响应: {url} ({content_type})")
        return FetchResult(url=url, title="", text="", success=False, error=error)

    try:
        parser = _HTMLTextExtractor()
        parser.feed(response.text)
        text = parser.text[:_MAX_TEXT]
        result = FetchResult(
            url=url,
            title=parser.title,
            text=text,
            success=True,
            error="",
        )
        logger.info(f"[web_fetcher] 抓取成功: {url}，标题={result.title!r}，正文长度={len(text)}")
        return result
    except Exception as exc:
        logger.warning(f"[web_fetcher] 解析失败: {url} ({exc})")
        return FetchResult(url=url, title="", text="", success=False, error=str(exc))
