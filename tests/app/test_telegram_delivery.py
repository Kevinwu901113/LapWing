"""Telegram 渲染与发送链路测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.telegram_delivery import (
    convert_markdown_tables,
    markdown_to_telegram_chunks,
    render_telegram_html_text,
    send_telegram_text_chunks,
    split_telegram_html_chunks,
)


def test_render_markdown_styles_and_escape_html():
    html = render_telegram_html_text(
        "**粗体** `code` <b>raw</b> [link](https://example.com)",
        text_mode="markdown",
        table_mode="code",
    )
    assert "<b>粗体</b>" in html
    assert "<code>code</code>" in html
    assert "&lt;b&gt;raw&lt;/b&gt;" in html
    assert '<a href="https://example.com">link</a>' in html


def test_convert_markdown_tables_code_mode():
    markdown = (
        "| 时间 | 对阵 |\n"
        "| --- | --- |\n"
        "| 10:10 | 道奇 vs 响尾蛇 |\n"
    )
    converted = convert_markdown_tables(markdown, "code")
    assert converted.startswith("```text")
    assert "时间" in converted
    assert "道奇 vs 响尾蛇" in converted


def test_markdown_table_renders_to_pre_code():
    markdown = (
        "| 时间 | 对阵 |\n"
        "| --- | --- |\n"
        "| 10:10 | 道奇 vs 响尾蛇 |\n"
    )
    html = render_telegram_html_text(markdown, text_mode="markdown", table_mode="code")
    assert "<pre><code>" in html
    assert "道奇 vs 响尾蛇" in html


def test_split_telegram_html_chunks_preserves_wrapping_tags():
    html = f"<b>{'A' * 5000}</b>"
    chunks = split_telegram_html_chunks(html, 1000)
    assert len(chunks) > 1
    assert "".join(chunk.replace("<b>", "").replace("</b>", "") for chunk in chunks) == "A" * 5000
    for chunk in chunks:
        assert chunk.startswith("<b>")
        assert chunk.endswith("</b>")


def test_split_telegram_html_chunks_keeps_entities_intact():
    html = "A&amp;" + ("B" * 50)
    chunks = split_telegram_html_chunks(html, 8)
    assert "".join(chunks) == html
    assert all(not chunk.endswith("&") for chunk in chunks)


@pytest.mark.asyncio
async def test_send_telegram_text_chunks_falls_back_on_parse_error():
    sent_html: list[str] = []
    sent_plain: list[str] = []

    async def _send_html(chunk: str, _index: int):
        sent_html.append(chunk)
        raise Exception("Bad Request: can't parse entities")

    async def _send_plain(chunk: str, _index: int):
        sent_plain.append(chunk)

    await send_telegram_text_chunks(
        text="你好，**世界**",
        send_html_chunk=_send_html,
        send_plain_chunk=_send_plain,
        text_mode="markdown",
        table_mode="code",
        chunk_limit=4000,
    )

    assert sent_html
    assert sent_plain == ["你好，**世界**"]


@pytest.mark.asyncio
async def test_send_telegram_text_chunks_raises_non_parse_error():
    send_html = AsyncMock(side_effect=RuntimeError("network down"))
    send_plain = AsyncMock()

    with pytest.raises(RuntimeError, match="network down"):
        await send_telegram_text_chunks(
            text="hello",
            send_html_chunk=send_html,
            send_plain_chunk=send_plain,
            text_mode="markdown",
            table_mode="code",
            chunk_limit=4000,
        )

    send_plain.assert_not_awaited()


def test_markdown_to_telegram_chunks_aligns_plain_and_html_chunks():
    chunks = markdown_to_telegram_chunks(
        text=("**X**" * 3000),
        text_mode="markdown",
        table_mode="code",
        chunk_limit=400,
    )
    assert len(chunks) > 1
    assert all(chunk.html for chunk in chunks)
    assert all(isinstance(chunk.text, str) and chunk.text for chunk in chunks)
