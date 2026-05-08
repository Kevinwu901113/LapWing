"""QQAdapter 单元测试。"""

import asyncio
import logging

import pytest
from unittest.mock import AsyncMock
from websockets.protocol import State as WsState

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


def _private_event(
    *,
    user_id: str = "200",
    message_id: str = "msg-1",
    message: object = "hello",
) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": user_id,
        "message_id": message_id,
        "message": message,
    }


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


class TestExtractImageUrls:
    def test_no_image(self):
        adapter = _make_adapter()
        event = {"message": [{"type": "text", "data": {"text": "hi"}}]}
        assert adapter._extract_image_urls(event) == []

    def test_has_image(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "image", "data": {"url": "https://example.com/img.png"}},
            ]
        }
        assert adapter._extract_image_urls(event) == ["https://example.com/img.png"]

    def test_multiple_images(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "image", "data": {"url": "https://example.com/a.png"}},
                {"type": "text", "data": {"text": "caption"}},
                {"type": "image", "data": {"url": "https://example.com/b.jpg"}},
            ]
        }
        assert adapter._extract_image_urls(event) == [
            "https://example.com/a.png",
            "https://example.com/b.jpg",
        ]

    def test_string_message_no_image(self):
        adapter = _make_adapter()
        assert adapter._extract_image_urls({"message": "text"}) == []


class TestMarkdownToPlain:
    def test_bold(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("**bold**") == "bold"

    def test_italic(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("*italic*") == "italic"

    def test_code_block(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("```python\nprint(1)\n```") == "print(1)"

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


@pytest.mark.asyncio
class TestPublicSendHelpers:
    async def test_send_private_message_delegates_to_private_api(self):
        adapter = _make_adapter()
        adapter._send_private_msg = AsyncMock(return_value={"status": "ok"})

        await adapter.send_private_message("200", "**hello**")

        adapter._send_private_msg.assert_awaited_once_with("200", "hello")

    async def test_send_group_message_uses_group_api(self):
        adapter = _make_adapter()
        adapter._send_group_msg = AsyncMock(return_value={"status": "ok"})

        await adapter.send_group_message("123", "`group hello`")

        adapter._send_group_msg.assert_awaited_once_with("123", "group hello")


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


@pytest.mark.asyncio
class TestPrivateMessageObservability:
    async def test_private_non_kevin_id_logs_drop(self, caplog):
        adapter = _make_adapter()
        adapter.on_message = AsyncMock()
        caplog.set_level(logging.INFO, logger="lapwing.adapters.qq_adapter")

        await adapter._handle_message_event(_private_event(user_id="201"))

        adapter.on_message.assert_not_called()
        assert "qq_message_drop" in caplog.text
        assert "reason=private_user_id_mismatch" in caplog.text
        assert "configured_kevin_id_tail=200" in caplog.text

    async def test_self_message_logs_drop(self, caplog):
        adapter = _make_adapter()
        caplog.set_level(logging.INFO, logger="lapwing.adapters.qq_adapter")

        await adapter._handle_message_event(_private_event(user_id="100"))

        assert "reason=self_message" in caplog.text

    async def test_duplicate_message_logs_drop(self, caplog):
        adapter = _make_adapter()
        adapter._mark_as_read = AsyncMock()
        adapter.on_message = AsyncMock()
        caplog.set_level(logging.INFO, logger="lapwing.adapters.qq_adapter")
        event = _private_event(message_id="dup-1")

        await adapter._handle_message_event(event)
        await asyncio.sleep(0)
        await adapter._handle_message_event(event)

        assert "reason=duplicate_message" in caplog.text

    async def test_empty_private_message_logs_drop(self, caplog):
        adapter = _make_adapter()
        caplog.set_level(logging.INFO, logger="lapwing.adapters.qq_adapter")

        await adapter._handle_message_event(_private_event(message=""))

        assert "reason=empty_private_message" in caplog.text

    async def test_missing_on_message_logs_drop(self, caplog):
        adapter = _make_adapter()
        adapter._mark_as_read = AsyncMock()
        caplog.set_level(logging.INFO, logger="lapwing.adapters.qq_adapter")

        await adapter._handle_message_event(_private_event(message_id="missing-cb"))
        await asyncio.sleep(0)

        assert "reason=missing_on_message" in caplog.text

    async def test_on_message_task_exception_is_logged(self, caplog):
        adapter = _make_adapter()
        adapter._mark_as_read = AsyncMock()

        async def failing_on_message(**kwargs):
            raise RuntimeError("boom")

        adapter.on_message = failing_on_message
        caplog.set_level(logging.ERROR, logger="lapwing.adapters.qq_adapter")

        await adapter._handle_message_event(_private_event(message_id="raises"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert "qq_adapter_task_exception" in caplog.text
        assert "task_name=qq-on-message" in caplog.text
        assert "exception_class=RuntimeError" in caplog.text
        assert "RuntimeError: boom" in caplog.text
        assert "qq_adapter_health layer=qq_adapter reason=callback_exception" in caplog.text
        assert adapter.callback_exception_count == 1

    async def test_call_api_ws_not_open_logs_health_snapshot(self, caplog):
        adapter = _make_adapter()
        caplog.set_level(logging.WARNING, logger="lapwing.adapters.qq_adapter")

        result = await adapter._call_api("send_private_msg", {}, timeout=0.01)

        assert result == {"status": "failed", "retcode": -1}
        assert "api_call_ws_not_open layer=qq_adapter action=send_private_msg" in caplog.text
        assert "qq_adapter_health layer=qq_adapter reason=api_call_ws_not_open" in caplog.text

    async def test_send_text_logs_non_ok_result_before_raising(self, caplog):
        adapter = _make_adapter()
        adapter._send_private_msg = AsyncMock(return_value={"status": "failed", "retcode": -7})
        caplog.set_level(logging.WARNING, logger="lapwing.adapters.qq_adapter")

        with pytest.raises(RuntimeError, match="retcode=-7"):
            await adapter.send_text("200", "hello")

        assert "qq_send_failure layer=qq_adapter action=send_private_msg retcode=-7" in caplog.text

    async def test_call_api_timeout_logs_and_increments_counter(self, caplog):
        class OpenWs:
            state = WsState.OPEN

            async def send(self, payload):
                self.payload = payload

        adapter = _make_adapter()
        adapter.ws = OpenWs()
        caplog.set_level(logging.WARNING, logger="lapwing.adapters.qq_adapter")

        result = await adapter._call_api("send_private_msg", {}, timeout=0.01)

        assert result == {"status": "failed", "retcode": -2}
        assert adapter.api_call_timeout_count == 1
        assert len(adapter._echo_futures) == 0
        assert "api_call_timeout layer=qq_adapter action=send_private_msg" in caplog.text
        assert "qq_adapter_health layer=qq_adapter reason=api_call_timeout" in caplog.text

    async def test_send_private_msg_non_numeric_logs_structured_warning(self, caplog):
        adapter = _make_adapter()
        caplog.set_level(logging.WARNING, logger="lapwing.adapters.qq_adapter")

        result = await adapter._send_private_msg("non_numeric_test_id", "hello")

        assert result == {"status": "failed", "retcode": -3}
        assert "QQ private send invalid user_id" in caplog.text
        assert "reason=non_numeric_user_id" in caplog.text
        # Only the tail of the non-numeric id is exposed
        assert "non_numeric_test_id" not in caplog.text

    async def test_send_private_msg_segments_non_numeric_logs_structured_warning(self, caplog):
        adapter = _make_adapter()
        caplog.set_level(logging.WARNING, logger="lapwing.adapters.qq_adapter")

        result = await adapter._send_private_msg_segments("chat", [{"type": "text", "data": {"text": "hi"}}])

        assert result == {"status": "failed", "retcode": -3}
        assert "QQ private send invalid user_id" in caplog.text
        assert "reason=non_numeric_user_id" in caplog.text
