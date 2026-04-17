"""SmartFetcher 单元测试 — mock httpx + browser_manager。"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.research.fetcher import SmartFetcher


def _mock_httpx_response(html: str, raises: Exception | None = None):
    response = MagicMock()
    response.text = html
    response.raise_for_status = MagicMock()

    client = AsyncMock()
    if raises is not None:
        client.get.side_effect = raises
    else:
        client.get.return_value = response

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _make_browser_manager(text_to_return: str = "browser fallback text " * 200):
    bm = MagicMock()
    tab_info = MagicMock()
    tab_info.tab_id = "tab-123"
    bm.new_tab = AsyncMock(return_value=tab_info)
    bm.get_page_text = AsyncMock(return_value=text_to_return)
    bm.close_tab = AsyncMock()
    return bm


async def test_httpx_returns_long_html_directly():
    html = "<html><body>" + ("real article content. " * 500) + "</body></html>"
    ctx = _mock_httpx_response(html)
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=None)
        text = await fetcher.fetch("https://example.com")
    assert text is not None
    assert "real article content" in text
    assert "<body>" not in text
    assert "<html>" not in text


async def test_strips_scripts_and_styles():
    html = """
    <html>
      <head><style>body { color: red; }</style></head>
      <body>
        <script>console.log('boo');</script>
        <p>visible content visible content visible content</p>
      </body>
    </html>
    """ + ("padding " * 500)
    ctx = _mock_httpx_response(html)
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=None)
        text = await fetcher.fetch("https://example.com")
    assert "console.log" not in text
    assert "color: red" not in text
    assert "visible content" in text


async def test_short_spa_shell_triggers_browser_fallback():
    spa_shell = "<html><body>Sign in Log in Menu Home loading... Cookie</body></html>"
    ctx = _mock_httpx_response(spa_shell)
    bm = _make_browser_manager("浏览器抓到的真实正文 " * 100)
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=bm)
        text = await fetcher.fetch("https://spa.example.com")
    assert text is not None
    assert "浏览器抓到的真实正文" in text
    bm.new_tab.assert_awaited_once_with("https://spa.example.com")
    bm.close_tab.assert_awaited_once_with("tab-123")


async def test_httpx_failure_triggers_browser_fallback():
    ctx = _mock_httpx_response("", raises=httpx.ConnectError("boom"))
    bm = _make_browser_manager("browser saved the day " * 100)
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=bm)
        text = await fetcher.fetch("https://flaky.example.com")
    assert text is not None
    assert "browser saved the day" in text


async def test_both_failures_returns_none():
    ctx = _mock_httpx_response("", raises=httpx.ConnectError("boom"))
    bm = _make_browser_manager()
    bm.new_tab.side_effect = RuntimeError("browser also dead")
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=bm)
        text = await fetcher.fetch("https://very.broken.com")
    assert text is None


async def test_no_browser_manager_returns_short_text_anyway():
    """没注入 browser_manager 时，即便 httpx 拿到的是短文本也直接返回。"""
    html = "<html><body>short stuff</body></html>"
    ctx = _mock_httpx_response(html)
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=None)
        text = await fetcher.fetch("https://small.example.com")
    assert text == "short stuff"


async def test_browser_close_tab_failure_does_not_propagate():
    spa_shell = "<html><body>Sign in Log in Menu Home Cookie</body></html>"
    ctx = _mock_httpx_response(spa_shell)
    bm = _make_browser_manager("good content " * 100)
    bm.close_tab.side_effect = RuntimeError("close failed")
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=bm)
        text = await fetcher.fetch("https://example.com")
    assert text is not None
    assert "good content" in text
