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


async def test_browser_fetch_hard_timeout(monkeypatch):
    """浏览器降级超过 _BROWSER_FETCH_TIMEOUT 时直接返回 None，而不是阻塞。"""
    import asyncio as _asyncio
    from src.research import fetcher as fetcher_module

    monkeypatch.setattr(fetcher_module, "_BROWSER_FETCH_TIMEOUT", 0.2)

    spa_shell = "<html><body>Sign in Log in Menu Home Cookie</body></html>"
    ctx = _mock_httpx_response(spa_shell)

    bm = MagicMock()
    tab_info = MagicMock()
    tab_info.tab_id = "stuck-tab"

    async def hang_new_tab(url):
        await _asyncio.sleep(5.0)  # 模拟真实浏览器卡住
        return tab_info

    bm.new_tab = AsyncMock(side_effect=hang_new_tab)
    bm.get_page_text = AsyncMock(return_value="never reached")
    bm.close_tab = AsyncMock()

    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=bm)
        text = await fetcher.fetch("https://stuck.example.com")
    # SPA shell 触发 browser fallback，但 browser 卡住，超时返回 None，
    # 此时返回原 SPA 文本（也很短）
    assert text == "Sign in Log in Menu Home Cookie"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc",
    "https://youtu.be/xyz",
    "https://m.youtube.com/watch?v=q",
    "https://vimeo.com/12345",
    "https://www.tiktok.com/@x/video/1",
    "https://www.twitch.tv/streamer",
    "https://www.bilibili.com/video/BV1Xy4y1k7Bs",
    "https://b23.tv/abc",
    "https://example.com/file.mp4",
    "https://example.com/photo.JPG",
    "https://example.com/doc.pdf",
    "https://cdn.example.com/song.mp3",
])
async def test_blacklisted_urls_short_circuit(url):
    """黑名单域名/后缀直接返回 None，不发起 httpx 请求。"""
    bm = _make_browser_manager()
    fetcher = SmartFetcher(browser_manager=bm)
    with patch("httpx.AsyncClient") as mock_client:
        text = await fetcher.fetch(url)
    assert text is None
    mock_client.assert_not_called()
    bm.new_tab.assert_not_called()


# ---------------------------------------------------------------------------
# ProxyRouter 集成测试
# ---------------------------------------------------------------------------

async def test_fetch_uses_proxy_decision():
    """proxy_router 存在时，resolve() 被调用，且用代理 URL 构造 AsyncClient。"""
    html = "<html><body>" + ("proxy article content. " * 500) + "</body></html>"
    ctx = _mock_httpx_response(html)

    mock_router = MagicMock()
    mock_router.resolve.return_value = MagicMock(strategy="proxy", proxy_url="http://proxy:7890")

    with patch("httpx.AsyncClient", return_value=ctx) as mock_client_cls:
        fetcher = SmartFetcher(browser_manager=None, proxy_router=mock_router)
        text = await fetcher.fetch("https://example.com/article")

    assert text is not None
    assert "proxy article content" in text
    mock_router.resolve.assert_called_once_with("https://example.com/article")
    # 构造时应带代理参数
    call_kwargs = mock_client_cls.call_args[1] if mock_client_cls.call_args[1] else {}
    assert "proxies" in call_kwargs
    assert call_kwargs["proxies"] == {"all://": "http://proxy:7890"}
    mock_router.report_success.assert_called_once_with("https://example.com/article", "proxy")


async def test_fetch_retries_on_403_with_alternative():
    """代理返回 403 时触发 report_failure_and_get_alternative，改用 direct 重试并成功。"""
    # 第一次调用（proxy）→ 403；第二次调用（direct）→ 200
    fail_response = MagicMock()
    fail_response.status_code = 403
    http_error = httpx.HTTPStatusError("403", request=MagicMock(), response=fail_response)

    fail_client = AsyncMock()
    fail_client.get.side_effect = http_error
    fail_ctx = MagicMock()
    fail_ctx.__aenter__ = AsyncMock(return_value=fail_client)
    fail_ctx.__aexit__ = AsyncMock(return_value=None)

    good_html = "<html><body>" + ("direct success content. " * 500) + "</body></html>"
    success_ctx = _mock_httpx_response(good_html)

    mock_router = MagicMock()
    mock_router.resolve.return_value = MagicMock(strategy="proxy", proxy_url="http://proxy:7890")
    mock_router.report_failure_and_get_alternative.return_value = MagicMock(
        strategy="direct", proxy_url=None
    )

    with patch("httpx.AsyncClient", side_effect=[fail_ctx, success_ctx]):
        fetcher = SmartFetcher(browser_manager=None, proxy_router=mock_router)
        text = await fetcher.fetch("https://blocked.example.com")

    assert text is not None
    assert "direct success content" in text
    mock_router.report_failure_and_get_alternative.assert_called_once_with(
        "https://blocked.example.com", "proxy"
    )
    mock_router.confirm_alternative.assert_called_once_with("https://blocked.example.com", "direct")


async def test_fetch_no_retry_on_404():
    """404 不是代理相关错误，不触发 report_failure_and_get_alternative。"""
    fail_response = MagicMock()
    fail_response.status_code = 404
    http_error = httpx.HTTPStatusError("404", request=MagicMock(), response=fail_response)

    fail_client = AsyncMock()
    fail_client.get.side_effect = http_error
    fail_ctx = MagicMock()
    fail_ctx.__aenter__ = AsyncMock(return_value=fail_client)
    fail_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_router = MagicMock()
    mock_router.resolve.return_value = MagicMock(strategy="proxy", proxy_url="http://proxy:7890")

    with patch("httpx.AsyncClient", return_value=fail_ctx):
        fetcher = SmartFetcher(browser_manager=None, proxy_router=mock_router)
        text = await fetcher.fetch("https://notfound.example.com")

    assert text is None
    mock_router.report_failure_and_get_alternative.assert_not_called()


async def test_fetch_confirms_alternative_on_success():
    """重试成功后 confirm_alternative 被调用（使用新策略）。"""
    fail_response = MagicMock()
    fail_response.status_code = 429
    http_error = httpx.HTTPStatusError("429", request=MagicMock(), response=fail_response)

    fail_client = AsyncMock()
    fail_client.get.side_effect = http_error
    fail_ctx = MagicMock()
    fail_ctx.__aenter__ = AsyncMock(return_value=fail_client)
    fail_ctx.__aexit__ = AsyncMock(return_value=None)

    good_html = "<html><body>" + ("confirmed content. " * 500) + "</body></html>"
    success_ctx = _mock_httpx_response(good_html)

    mock_router = MagicMock()
    mock_router.resolve.return_value = MagicMock(strategy="proxy", proxy_url="http://proxy:7890")
    mock_router.report_failure_and_get_alternative.return_value = MagicMock(
        strategy="direct", proxy_url=None
    )

    with patch("httpx.AsyncClient", side_effect=[fail_ctx, success_ctx]):
        fetcher = SmartFetcher(browser_manager=None, proxy_router=mock_router)
        text = await fetcher.fetch("https://ratelimited.example.com")

    assert text is not None
    assert "confirmed content" in text
    mock_router.confirm_alternative.assert_called_once_with(
        "https://ratelimited.example.com", "direct"
    )
    mock_router.report_success.assert_called_once_with(
        "https://ratelimited.example.com", "direct"
    )


@pytest.mark.parametrize("url", [
    "https://www.bilibili.com/read/cv12345",   # B 站专栏（不在黑名单）
    "https://space.bilibili.com/123",            # B 站个人空间
    "https://example.com/article.html",
    "https://news.example.com/",
    "https://en.wikipedia.org/wiki/Foo",
])
async def test_non_blacklisted_urls_pass_through(url):
    """非黑名单 URL 正常走 httpx 流程。"""
    html = "<html><body>" + ("article body. " * 500) + "</body></html>"
    ctx = _mock_httpx_response(html)
    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=None)
        text = await fetcher.fetch(url)
    assert text is not None
    assert "article body" in text


async def test_overall_fetch_timeout(monkeypatch):
    """整体 fetch 超过 _FETCH_OVERALL_TIMEOUT 时返回 None。"""
    import asyncio as _asyncio
    from src.research import fetcher as fetcher_module

    monkeypatch.setattr(fetcher_module, "_FETCH_OVERALL_TIMEOUT", 0.3)

    async def hang_get(url):
        await _asyncio.sleep(5.0)
        return MagicMock(text="never", raise_for_status=MagicMock())

    client = AsyncMock()
    client.get.side_effect = hang_get
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=ctx):
        fetcher = SmartFetcher(browser_manager=None)
        text = await fetcher.fetch("https://hung.example.com")
    assert text is None
