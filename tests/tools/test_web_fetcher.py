"""web_fetcher 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.tools.web_fetcher import _MAX_TEXT, fetch


def _mock_async_client(response=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        client.get = AsyncMock(return_value=response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm, client


def _make_response(html: str, content_type: str = "text/html; charset=utf-8") -> MagicMock:
    response = MagicMock()
    response.headers = {"content-type": content_type}
    response.text = html
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
class TestFetch:
    async def test_fetch_success(self):
        response = _make_response(
            "<html><head><title>Example</title></head>"
            "<body><h1>Hello</h1><p>world</p></body></html>"
        )
        cm, client = _mock_async_client(response=response)

        with patch("src.tools.web_fetcher.httpx.AsyncClient", return_value=cm):
            result = await fetch("https://example.com")

        assert result.success is True
        assert result.title == "Example"
        assert result.text == "Hello world"
        client.get.assert_awaited_once_with("https://example.com")

    async def test_fetch_strips_script_and_style(self):
        response = _make_response(
            "<html><head><title>T</title><style>.x{color:red}</style></head>"
            "<body><script>alert(1)</script><p>keep me</p></body></html>"
        )
        cm, _ = _mock_async_client(response=response)

        with patch("src.tools.web_fetcher.httpx.AsyncClient", return_value=cm):
            result = await fetch("https://example.com")

        assert result.success is True
        assert "alert" not in result.text
        assert "color:red" not in result.text
        assert result.text == "keep me"

    async def test_fetch_timeout(self):
        cm, _ = _mock_async_client(side_effect=httpx.TimeoutException("timeout"))

        with patch("src.tools.web_fetcher.httpx.AsyncClient", return_value=cm):
            result = await fetch("https://example.com")

        assert result.success is False
        assert "超时" in result.error

    async def test_fetch_non_html(self):
        response = _make_response("%PDF", content_type="application/pdf")
        cm, _ = _mock_async_client(response=response)

        with patch("src.tools.web_fetcher.httpx.AsyncClient", return_value=cm):
            result = await fetch("https://example.com/file.pdf")

        assert result.success is False
        assert "内容类型" in result.error

    async def test_fetch_truncates_long_text(self):
        response = _make_response(
            f"<html><head><title>Long</title></head><body><p>{'A' * (_MAX_TEXT + 200)}</p></body></html>"
        )
        cm, _ = _mock_async_client(response=response)

        with patch("src.tools.web_fetcher.httpx.AsyncClient", return_value=cm):
            result = await fetch("https://example.com")

        assert result.success is True
        assert len(result.text) == _MAX_TEXT

    async def test_fetch_connection_error(self):
        request = httpx.Request("GET", "https://example.com")
        cm, _ = _mock_async_client(side_effect=httpx.ConnectError("boom", request=request))

        with patch("src.tools.web_fetcher.httpx.AsyncClient", return_value=cm):
            result = await fetch("https://example.com")

        assert result.success is False
        assert "连接失败" in result.error
