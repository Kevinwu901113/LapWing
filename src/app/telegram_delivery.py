"""Telegram 文本投递：Markdown 渲染、分块、HTML 回退发送。"""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Any, Awaitable, Callable

from markdown_it import MarkdownIt

from config.settings import (
    TELEGRAM_HTML_CHUNK_LIMIT,
    TELEGRAM_MARKDOWN_TABLE_MODE,
    TELEGRAM_TEXT_MODE,
)

_PARSE_ERROR_RE = re.compile(r"can't parse entities|parse entities|find end of the entity", re.I)
_HTML_TAG_PATTERN = re.compile(r"(<\/?)([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*?>")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_SUPPORTED_SELF_CLOSING_TAGS = {"br"}

_MARKDOWN = MarkdownIt(
    "default",
    {
        "html": False,
        "linkify": False,
        "typographer": False,
    },
).enable("strikethrough")


@dataclass(frozen=True)
class TelegramFormattedChunk:
    html: str
    text: str


def _escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _escape_html_attr(text: str) -> str:
    return html.escape(text, quote=True)


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return False
    return not stripped.startswith("```")


def _split_table_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _align_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    col_widths = [max(len(row[col]) for row in normalized) for col in range(width)]

    def _line(values: list[str]) -> str:
        cells = [values[col].ljust(col_widths[col]) for col in range(width)]
        return " | ".join(cells).rstrip()

    header = normalized[0]
    sep = ["-" * max(3, col_widths[col]) for col in range(width)]
    lines = [_line(header), _line(sep)]
    for row in normalized[1:]:
        lines.append(_line(row))
    return "\n".join(lines).rstrip()


def convert_markdown_tables(markdown: str, table_mode: str) -> str:
    if table_mode == "off":
        return markdown

    lines = markdown.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and _is_table_row(lines[index])
            and _TABLE_SEPARATOR_RE.match(lines[index + 1] or "")
        ):
            table_lines = [lines[index]]
            index += 2
            while index < len(lines) and _is_table_row(lines[index]):
                table_lines.append(lines[index])
                index += 1
            rows = [_split_table_row(line) for line in table_lines]
            if table_mode == "bullets":
                header = rows[0] if rows else []
                bullets: list[str] = []
                for row in rows[1:]:
                    cells = []
                    for col, value in enumerate(row):
                        key = header[col] if col < len(header) else f"列{col + 1}"
                        cells.append(f"{key}: {value}")
                    bullets.append(f"- {'; '.join(cells)}")
                output.extend(bullets or ["- （空表）"])
            else:
                aligned = _align_table(rows)
                output.append("```text")
                output.append(aligned)
                output.append("```")
            continue

        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _render_inline(children: list[Any]) -> str:
    rendered: list[str] = []
    for token in children:
        token_type = token.type
        if token_type == "text":
            rendered.append(_escape_html(token.content))
            continue
        if token_type in {"softbreak", "hardbreak"}:
            rendered.append("\n")
            continue
        if token_type == "code_inline":
            rendered.append(f"<code>{_escape_html(token.content)}</code>")
            continue
        if token_type == "strong_open":
            rendered.append("<b>")
            continue
        if token_type == "strong_close":
            rendered.append("</b>")
            continue
        if token_type == "em_open":
            rendered.append("<i>")
            continue
        if token_type == "em_close":
            rendered.append("</i>")
            continue
        if token_type in {"s_open", "strikethrough_open"}:
            rendered.append("<s>")
            continue
        if token_type in {"s_close", "strikethrough_close"}:
            rendered.append("</s>")
            continue
        if token_type == "link_open":
            href = str(token.attrGet("href") or "").strip()
            if href:
                rendered.append(f'<a href="{_escape_html_attr(href)}">')
            continue
        if token_type == "link_close":
            rendered.append("</a>")
            continue
        if token_type == "image":
            src = str(token.attrGet("src") or "").strip()
            alt = _escape_html(token.content or src)
            if src:
                rendered.append(f'<a href="{_escape_html_attr(src)}">{alt}</a>')
            else:
                rendered.append(alt)
            continue
        if token_type == "html_inline":
            rendered.append(_escape_html(token.content))
            continue
        if token.content:
            rendered.append(_escape_html(token.content))
    return "".join(rendered)


def _extract_nested_tokens(
    tokens: list[Any],
    start_index: int,
    open_type: str,
    close_type: str,
) -> tuple[list[Any], int]:
    depth = 0
    first_content_index = start_index + 1
    index = start_index
    while index < len(tokens):
        token_type = tokens[index].type
        if token_type == open_type:
            depth += 1
        elif token_type == close_type:
            depth -= 1
            if depth == 0:
                return tokens[first_content_index:index], index + 1
        index += 1
    return tokens[first_content_index:], len(tokens)


def _prefix_list_item(text: str, prefix: str) -> str:
    lines = text.splitlines() or [""]
    if not lines:
        return prefix.rstrip()
    output = [f"{prefix}{lines[0]}"]
    continuation_indent = " " * len(prefix)
    for line in lines[1:]:
        output.append(f"{continuation_indent}{line}")
    return "\n".join(output)


def _render_list(tokens: list[Any], index: int, ordered: bool) -> tuple[str, int]:
    open_type = "ordered_list_open" if ordered else "bullet_list_open"
    close_type = "ordered_list_close" if ordered else "bullet_list_close"
    inner_tokens, next_index = _extract_nested_tokens(tokens, index, open_type, close_type)

    current = 1
    if ordered:
        start_attr = tokens[index].attrGet("start")
        if start_attr is not None:
            try:
                current = max(1, int(start_attr))
            except ValueError:
                current = 1

    rendered_items: list[str] = []
    cursor = 0
    while cursor < len(inner_tokens):
        token = inner_tokens[cursor]
        if token.type != "list_item_open":
            cursor += 1
            continue
        item_tokens, cursor = _extract_nested_tokens(
            inner_tokens,
            cursor,
            "list_item_open",
            "list_item_close",
        )
        text = _render_blocks(item_tokens).strip()
        if ordered:
            rendered_items.append(_prefix_list_item(text, f"{current}. "))
            current += 1
        else:
            rendered_items.append(_prefix_list_item(text, "- "))
    return "\n".join(item for item in rendered_items if item.strip()), next_index


def _render_blocks(tokens: list[Any]) -> str:
    blocks: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        token_type = token.type

        if token_type in {"paragraph_open", "heading_open"}:
            inline_index = index + 1
            inline_token = tokens[inline_index] if inline_index < len(tokens) else None
            content = ""
            if inline_token is not None and inline_token.type == "inline":
                content = _render_inline(inline_token.children or [])
            if token_type == "heading_open":
                blocks.append(f"<b>{content}</b>")
            else:
                blocks.append(content)
            index += 3
            continue

        if token_type in {"fence", "code_block"}:
            text = token.content.rstrip("\n")
            blocks.append(f"<pre><code>{_escape_html(text)}</code></pre>")
            index += 1
            continue

        if token_type == "blockquote_open":
            inner, next_index = _extract_nested_tokens(tokens, index, "blockquote_open", "blockquote_close")
            blocks.append(f"<blockquote>{_render_blocks(inner).strip()}</blockquote>")
            index = next_index
            continue

        if token_type == "bullet_list_open":
            list_text, next_index = _render_list(tokens, index, ordered=False)
            blocks.append(list_text)
            index = next_index
            continue

        if token_type == "ordered_list_open":
            list_text, next_index = _render_list(tokens, index, ordered=True)
            blocks.append(list_text)
            index = next_index
            continue

        if token_type == "inline":
            blocks.append(_render_inline(token.children or []))
            index += 1
            continue

        if token_type == "hr":
            blocks.append("----------")
            index += 1
            continue

        index += 1

    return "\n\n".join(item for item in blocks if item.strip())


def render_telegram_html_text(
    text: str,
    *,
    text_mode: str = TELEGRAM_TEXT_MODE,
    table_mode: str = TELEGRAM_MARKDOWN_TABLE_MODE,
) -> str:
    if text_mode == "html":
        return text
    normalized = convert_markdown_tables(text, table_mode=table_mode)
    tokens = _MARKDOWN.parse(normalized)
    return _render_blocks(tokens)


def _find_entity_end(text: str, start: int) -> int:
    if start < 0 or start >= len(text) or text[start] != "&":
        return -1
    cursor = start + 1
    if cursor >= len(text):
        return -1
    if text[cursor] == "#":
        cursor += 1
        if cursor >= len(text):
            return -1
        if text[cursor] in {"x", "X"}:
            cursor += 1
            begin = cursor
            while cursor < len(text) and re.match(r"[0-9A-Fa-f]", text[cursor]):
                cursor += 1
            if cursor == begin:
                return -1
        else:
            begin = cursor
            while cursor < len(text) and text[cursor].isdigit():
                cursor += 1
            if cursor == begin:
                return -1
    else:
        begin = cursor
        while cursor < len(text) and re.match(r"[A-Za-z0-9]", text[cursor]):
            cursor += 1
        if cursor == begin:
            return -1
    return cursor if cursor < len(text) and text[cursor] == ";" else -1


def _safe_split_index(text: str, max_length: int) -> int:
    if len(text) <= max_length:
        return len(text)
    max_length = max(1, int(max_length))
    last_amp = text.rfind("&", 0, max_length)
    if last_amp < 0:
        return max_length
    last_semicolon = text.rfind(";", 0, max_length)
    if last_amp < last_semicolon:
        return max_length
    entity_end = _find_entity_end(text, last_amp)
    if entity_end < 0 or entity_end < max_length:
        return max_length
    return last_amp


def split_telegram_html_chunks(html_text: str, limit: int) -> list[str]:
    if not html_text:
        return []
    normalized_limit = max(1, int(limit))
    if len(html_text) <= normalized_limit:
        return [html_text]

    chunks: list[str] = []
    open_tags: list[dict[str, str]] = []
    current = ""
    chunk_has_payload = False

    def _open_prefix() -> str:
        return "".join(item["open"] for item in open_tags)

    def _close_suffix() -> str:
        return "".join(item["close"] for item in reversed(open_tags))

    def _close_suffix_length() -> int:
        return sum(len(item["close"]) for item in open_tags)

    def _reset_current() -> None:
        nonlocal current, chunk_has_payload
        current = _open_prefix()
        chunk_has_payload = False

    def _flush_current() -> None:
        nonlocal current
        if not chunk_has_payload:
            return
        chunks.append(f"{current}{_close_suffix()}")
        _reset_current()

    def _append_text(segment: str) -> None:
        nonlocal current, chunk_has_payload
        remaining = segment
        while remaining:
            available = normalized_limit - len(current) - _close_suffix_length()
            if available <= 0:
                if not chunk_has_payload:
                    raise ValueError(
                        f"Telegram HTML chunk limit exceeded by tag overhead (limit={normalized_limit})"
                    )
                _flush_current()
                continue

            if len(remaining) <= available:
                current += remaining
                chunk_has_payload = True
                break

            split_at = _safe_split_index(remaining, available)
            if split_at <= 0:
                if not chunk_has_payload:
                    raise ValueError(
                        f"Telegram HTML chunk limit exceeded by leading entity (limit={normalized_limit})"
                    )
                _flush_current()
                continue

            current += remaining[:split_at]
            chunk_has_payload = True
            remaining = remaining[split_at:]
            _flush_current()

    _reset_current()
    last_index = 0
    for match in _HTML_TAG_PATTERN.finditer(html_text):
        tag_start = match.start()
        tag_end = match.end()
        _append_text(html_text[last_index:tag_start])

        raw_tag = match.group(0)
        is_closing = match.group(1) == "</"
        tag_name = (match.group(2) or "").lower()
        is_self_closing = (
            not is_closing
            and (tag_name in _SUPPORTED_SELF_CLOSING_TAGS or raw_tag.rstrip().endswith("/>"))
        )
        if not is_closing:
            next_close_length = 0 if is_self_closing else len(f"</{tag_name}>")
            if (
                chunk_has_payload
                and len(current) + len(raw_tag) + _close_suffix_length() + next_close_length > normalized_limit
            ):
                _flush_current()

        current += raw_tag
        if is_self_closing:
            chunk_has_payload = True
        if is_closing:
            for index in range(len(open_tags) - 1, -1, -1):
                if open_tags[index]["name"] == tag_name:
                    open_tags.pop(index)
                    break
        elif not is_self_closing:
            open_tags.append({"name": tag_name, "open": raw_tag, "close": f"</{tag_name}>"})
        last_index = tag_end

    _append_text(html_text[last_index:])
    _flush_current()
    return chunks or [html_text]


def _split_plain_text_chunks(text: str, limit: int) -> list[str]:
    if not text:
        return []
    normalized_limit = max(1, int(limit))
    return [text[offset: offset + normalized_limit] for offset in range(0, len(text), normalized_limit)]


def split_plain_text_fallback(text: str, chunk_count: int, limit: int) -> list[str]:
    if not text:
        return []
    fixed_chunks = _split_plain_text_chunks(text, limit)
    if chunk_count <= 1 or len(fixed_chunks) >= chunk_count:
        return fixed_chunks

    normalized_limit = max(1, int(limit))
    chunks: list[str] = []
    offset = 0
    for index in range(chunk_count):
        remaining_chars = len(text) - offset
        remaining_chunks = chunk_count - index
        next_size = (
            remaining_chars
            if remaining_chunks == 1
            else min(normalized_limit, (remaining_chars + remaining_chunks - 1) // remaining_chunks)
        )
        chunks.append(text[offset: offset + next_size])
        offset += next_size
    return chunks


def markdown_to_telegram_chunks(
    text: str,
    *,
    text_mode: str = TELEGRAM_TEXT_MODE,
    table_mode: str = TELEGRAM_MARKDOWN_TABLE_MODE,
    chunk_limit: int = TELEGRAM_HTML_CHUNK_LIMIT,
) -> list[TelegramFormattedChunk]:
    if not text:
        return []

    html_text = render_telegram_html_text(text, text_mode=text_mode, table_mode=table_mode)
    html_chunks = split_telegram_html_chunks(html_text, chunk_limit)
    plain_chunks = split_plain_text_fallback(text, len(html_chunks), chunk_limit)

    result: list[TelegramFormattedChunk] = []
    for index, html_chunk in enumerate(html_chunks):
        plain_text = plain_chunks[index] if index < len(plain_chunks) else text
        result.append(TelegramFormattedChunk(html=html_chunk, text=plain_text))
    return result


def is_telegram_html_parse_error(exc: Exception) -> bool:
    return _PARSE_ERROR_RE.search(str(exc or "")) is not None


async def send_telegram_text_chunks(
    *,
    text: str,
    send_html_chunk: Callable[[str, int], Awaitable[Any]],
    send_plain_chunk: Callable[[str, int], Awaitable[Any]],
    text_mode: str = TELEGRAM_TEXT_MODE,
    table_mode: str = TELEGRAM_MARKDOWN_TABLE_MODE,
    chunk_limit: int = TELEGRAM_HTML_CHUNK_LIMIT,
) -> None:
    chunks = markdown_to_telegram_chunks(
        text,
        text_mode=text_mode,
        table_mode=table_mode,
        chunk_limit=chunk_limit,
    )
    for index, chunk in enumerate(chunks):
        try:
            await send_html_chunk(chunk.html, index)
        except Exception as exc:
            if not is_telegram_html_parse_error(exc):
                raise
            await send_plain_chunk(chunk.text, index)


async def send_telegram_text_to_chat(
    *,
    bot,
    chat_id: int | str,
    text: str,
    text_mode: str = TELEGRAM_TEXT_MODE,
    table_mode: str = TELEGRAM_MARKDOWN_TABLE_MODE,
    chunk_limit: int = TELEGRAM_HTML_CHUNK_LIMIT,
) -> None:
    async def _send_html(chunk: str, _index: int) -> Any:
        return await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")

    async def _send_plain(chunk: str, _index: int) -> Any:
        return await bot.send_message(chat_id=chat_id, text=chunk)

    await send_telegram_text_chunks(
        text=text,
        send_html_chunk=_send_html,
        send_plain_chunk=_send_plain,
        text_mode=text_mode,
        table_mode=table_mode,
        chunk_limit=chunk_limit,
    )


async def send_telegram_reply_text(
    *,
    message,
    text: str,
    text_mode: str = TELEGRAM_TEXT_MODE,
    table_mode: str = TELEGRAM_MARKDOWN_TABLE_MODE,
    chunk_limit: int = TELEGRAM_HTML_CHUNK_LIMIT,
) -> None:
    async def _send_html(chunk: str, index: int) -> Any:
        if index == 0:
            return await message.reply_text(chunk, parse_mode="HTML")
        return await message.chat.send_message(chunk, parse_mode="HTML")

    async def _send_plain(chunk: str, index: int) -> Any:
        if index == 0:
            return await message.reply_text(chunk)
        return await message.chat.send_message(chunk)

    await send_telegram_text_chunks(
        text=text,
        send_html_chunk=_send_html,
        send_plain_chunk=_send_plain,
        text_mode=text_mode,
        table_mode=table_mode,
        chunk_limit=chunk_limit,
    )
