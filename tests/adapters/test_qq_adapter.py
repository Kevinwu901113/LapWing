"""QQAdapter 单元测试。"""

import pytest

from src.adapters.qq_adapter import QQAdapter


def _make_adapter(**overrides) -> QQAdapter:
    config = {
        "ws_url": "ws://127.0.0.1:3001",
        "access_token": "test",
        "self_id": "100",
        "kevin_id": "200",
        **overrides,
    }
    return QQAdapter(config=config)


class TestExtractText:
    def test_string_message(self):
        adapter = _make_adapter()
        event = {"message": "你好"}
        assert adapter._extract_text(event) == "你好"

    def test_array_message(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "text", "data": {"text": "hello "}},
                {"type": "text", "data": {"text": "world"}},
            ]
        }
        assert adapter._extract_text(event) == "hello world"

    def test_array_with_at_segment(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "at", "data": {"qq": "100"}},
                {"type": "text", "data": {"text": "你好"}},
            ]
        }
        assert adapter._extract_text(event) == "你好"

    def test_empty_message(self):
        adapter = _make_adapter()
        assert adapter._extract_text({"message": ""}) == ""
        assert adapter._extract_text({"message": []}) == ""


class TestExtractImage:
    def test_no_image(self):
        adapter = _make_adapter()
        event = {"message": [{"type": "text", "data": {"text": "hi"}}]}
        assert adapter._extract_image(event) is None

    def test_has_image(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "image", "data": {"url": "https://example.com/img.png"}},
            ]
        }
        assert adapter._extract_image(event) == "https://example.com/img.png"

    def test_string_message_no_image(self):
        adapter = _make_adapter()
        assert adapter._extract_image({"message": "text"}) is None


class TestMarkdownToPlain:
    def test_bold(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("**bold**") == "bold"

    def test_italic(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("*italic*") == "italic"

    def test_code_block(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("```python\nprint(1)\n```") == "print(1)\n"

    def test_inline_code(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("`code`") == "code"

    def test_link(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("[text](url)") == "text (url)"

    def test_plain_text_unchanged(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("hello world") == "hello world"


class TestSplitText:
    def test_short_text(self):
        adapter = _make_adapter()
        assert adapter._split_text("short", 100) == ["short"]

    def test_split_at_newline(self):
        adapter = _make_adapter()
        text = "line1\nline2\nline3"
        chunks = adapter._split_text(text, 10)
        assert all(len(c) <= 10 for c in chunks)
        recombined = "\n".join(chunks)
        assert "line1" in recombined
        assert "line3" in recombined

    def test_split_long_no_newline(self):
        adapter = _make_adapter()
        text = "a" * 20
        chunks = adapter._split_text(text, 8)
        assert all(len(c) <= 8 for c in chunks)
        assert "".join(chunks) == text


class TestBuildMessageSegments:
    def test_text_only(self):
        adapter = _make_adapter()
        segments = adapter._build_message_segments("hello", None)
        assert segments == [{"type": "text", "data": {"text": "hello"}}]

    def test_with_image(self):
        adapter = _make_adapter()
        segments = adapter._build_message_segments("caption", "base64data")
        assert len(segments) == 2
        assert segments[0]["type"] == "text"
        assert segments[1]["type"] == "image"
        assert "base64://" in segments[1]["data"]["file"]
