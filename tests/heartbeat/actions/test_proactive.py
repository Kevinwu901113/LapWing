"""ProactiveMessageAction 测试。"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.heartbeat import SenseContext
from src.heartbeat.actions.proactive import ProactiveMessageAction


@pytest.fixture
def ctx():
    return SenseContext(
        beat_type="fast", now=datetime.now(timezone.utc),
        last_interaction=None, silence_hours=20.0,
        user_facts_summary="- 偏好: 不吃辣",
        recent_memory_summary="", chat_id="c1",
    )


@pytest.fixture
def mock_brain():
    b = MagicMock()
    b.event_bus = None
    b.memory = MagicMock()
    b.memory.get_unshared_discoveries = AsyncMock(return_value=[])
    b.memory.append = AsyncMock()
    b.memory.mark_discovery_shared = AsyncMock()
    b.router = MagicMock()
    b.router.query_lightweight = AsyncMock(return_value="PASS")
    b.compose_proactive = AsyncMock(return_value="你好，好久不见，最近怎么样？")
    return b


@pytest.fixture
def mock_send_fn():
    return AsyncMock()


class TestProactiveMessageAction:
    def test_beat_types_includes_fast(self):
        assert "fast" in ProactiveMessageAction().beat_types

    def test_name_is_proactive_message(self):
        assert ProactiveMessageAction().name == "proactive_message"

    async def test_sends_message_to_user(self, ctx, mock_brain, mock_send_fn):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)
        mock_send_fn.assert_awaited_once_with("你好，好久不见，最近怎么样？")

    async def test_stores_reply_in_memory(self, ctx, mock_brain, mock_send_fn):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)
        mock_brain.memory.append.assert_called_once_with(
            "c1", "assistant", "你好，好久不见，最近怎么样？"
        )

    async def test_uses_compose_proactive(self, ctx, mock_brain, mock_send_fn):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)
        mock_brain.compose_proactive.assert_awaited_once()
        call_kwargs = mock_brain.compose_proactive.call_args.kwargs
        assert call_kwargs["chat_id"] == "c1"
        assert call_kwargs["max_tokens"] == 200
        assert call_kwargs["purpose"] == "主动消息"

    async def test_forwards_clean_output(self, ctx, mock_brain, mock_send_fn):
        mock_brain.compose_proactive = AsyncMock(return_value="你好")

        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)

        mock_send_fn.assert_awaited_once_with("你好")
        mock_brain.memory.append.assert_awaited_once_with("c1", "assistant", "你好")

    async def test_silent_on_llm_failure(self, ctx, mock_brain, mock_send_fn):
        mock_brain.compose_proactive = AsyncMock(side_effect=Exception("API error"))
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_silent_when_compose_returns_none(self, ctx, mock_brain, mock_send_fn):
        mock_brain.compose_proactive = AsyncMock(return_value=None)
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_marks_discovery_shared_when_used(self, ctx, mock_brain, mock_send_fn):
        mock_brain.memory.get_unshared_discoveries = AsyncMock(return_value=[
            {"id": 42, "title": "有趣文章", "summary": "内容摘要", "url": "http://x.com"}
        ])
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)
        mock_brain.memory.mark_discovery_shared.assert_called_once_with(42)

    async def test_publishes_desktop_event_when_event_bus_present(self, ctx, mock_brain, mock_send_fn):
        mock_brain.event_bus = MagicMock()
        mock_brain.event_bus.publish = AsyncMock()

        await ProactiveMessageAction().execute(ctx, mock_brain, mock_send_fn)

        mock_brain.event_bus.publish.assert_awaited_once_with(
            "proactive_message",
            {
                "chat_id": "c1",
                "text": "你好，好久不见，最近怎么样？",
            },
        )
