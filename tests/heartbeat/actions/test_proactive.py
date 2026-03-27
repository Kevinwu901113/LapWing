"""ProactiveMessageAction 测试。"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.heartbeat import SenseContext
from src.heartbeat.actions.proactive import ProactiveMessageAction, ReminderDispatchAction


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
    b.memory = MagicMock()
    b.memory.get_unshared_discoveries = AsyncMock(return_value=[])
    b.memory.get_due_reminders = AsyncMock(return_value=[])
    b.memory.complete_or_reschedule_reminder = AsyncMock(return_value=True)
    b.memory.append = AsyncMock()
    b.memory.mark_discovery_shared = AsyncMock()
    b.router = MagicMock()
    b.router.complete = AsyncMock(return_value="你好，好久不见，最近怎么样？")
    return b


@pytest.fixture
def mock_bot():
    b = MagicMock()
    b.send_message = AsyncMock()
    return b


class TestProactiveMessageAction:
    def test_beat_types_includes_fast(self):
        assert "fast" in ProactiveMessageAction().beat_types

    def test_name_is_proactive_message(self):
        assert ProactiveMessageAction().name == "proactive_message"

    async def test_sends_message_to_user(self, ctx, mock_brain, mock_bot):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_bot.send_message.assert_called_once()
        assert mock_bot.send_message.call_args.kwargs["chat_id"] == "c1"

    async def test_stores_reply_in_memory(self, ctx, mock_brain, mock_bot):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_brain.memory.append.assert_called_once_with(
            "c1", "assistant", "你好，好久不见，最近怎么样？"
        )

    async def test_uses_heartbeat_purpose(self, ctx, mock_brain, mock_bot):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        assert mock_brain.router.complete.call_args.kwargs.get("purpose") == "heartbeat"

    async def test_sanitizes_thinking_tags_before_send_and_store(self, ctx, mock_brain, mock_bot):
        mock_brain.router.complete = AsyncMock(return_value="<think>内部</think>你好")

        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)

        mock_bot.send_message.assert_awaited_once_with(chat_id="c1", text="你好")
        mock_brain.memory.append.assert_awaited_once_with("c1", "assistant", "你好")

    async def test_silent_on_llm_failure(self, ctx, mock_brain, mock_bot):
        mock_brain.router.complete = AsyncMock(side_effect=Exception("API error"))
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_bot.send_message.assert_not_called()

    async def test_marks_discovery_shared_when_used(self, ctx, mock_brain, mock_bot):
        mock_brain.memory.get_unshared_discoveries = AsyncMock(return_value=[
            {"id": 42, "title": "有趣文章", "summary": "内容摘要", "url": "http://x.com"}
        ])
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_brain.memory.mark_discovery_shared.assert_called_once_with(42)

    async def test_publishes_desktop_event_when_event_bus_present(self, ctx, mock_brain, mock_bot):
        mock_brain.event_bus = MagicMock()
        mock_brain.event_bus.publish = AsyncMock()

        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)

        mock_brain.event_bus.publish.assert_awaited_once_with(
            "proactive_message",
            {
                "chat_id": "c1",
                "text": "你好，好久不见，最近怎么样？",
            },
        )


@pytest.fixture
def minute_ctx():
    return SenseContext(
        beat_type="minute",
        now=datetime.now(timezone.utc),
        last_interaction=None,
        silence_hours=2.0,
        user_facts_summary="- 作息：晚饭后会休息",
        recent_memory_summary="",
        chat_id="c1",
    )


@pytest.mark.asyncio
class TestReminderDispatchAction:
    async def test_contract(self):
        action = ReminderDispatchAction()
        assert action.beat_types == ["minute"]
        assert action.selection_mode == "always"

    async def test_skips_when_no_due_reminder(self, minute_ctx, mock_brain, mock_bot):
        mock_brain.memory.get_due_reminders = AsyncMock(return_value=[])
        await ReminderDispatchAction().execute(minute_ctx, mock_brain, mock_bot)
        mock_bot.send_message.assert_not_called()
        mock_brain.memory.complete_or_reschedule_reminder.assert_not_called()

    async def test_dispatches_due_reminder_and_updates_state(self, minute_ctx, mock_brain, mock_bot):
        mock_brain.memory.get_due_reminders = AsyncMock(return_value=[
            {
                "id": 12,
                "chat_id": "c1",
                "content": "起身活动一下",
                "recurrence_type": "once",
                "next_trigger_at": minute_ctx.now.isoformat(),
                "weekday": None,
                "time_of_day": None,
                "active": True,
                "created_at": minute_ctx.now.isoformat(),
                "last_triggered_at": None,
                "cancelled_at": None,
            }
        ])
        mock_brain.router.complete = AsyncMock(return_value="提醒你：起身活动一下")
        mock_brain.event_bus = MagicMock()
        mock_brain.event_bus.publish = AsyncMock()

        await ReminderDispatchAction().execute(minute_ctx, mock_brain, mock_bot)

        mock_bot.send_message.assert_awaited_once_with(chat_id="c1", text="提醒你：起身活动一下")
        mock_brain.memory.complete_or_reschedule_reminder.assert_awaited_once_with(12, now=minute_ctx.now)
        mock_brain.event_bus.publish.assert_awaited_once_with(
            "reminder_message",
            {"chat_id": "c1", "text": "提醒你：起身活动一下"},
        )

    async def test_falls_back_when_llm_returns_empty(self, minute_ctx, mock_brain, mock_bot):
        mock_brain.memory.get_due_reminders = AsyncMock(return_value=[
            {
                "id": 99,
                "chat_id": "c1",
                "content": "喝水",
                "recurrence_type": "daily",
                "next_trigger_at": minute_ctx.now.isoformat(),
                "weekday": None,
                "time_of_day": "10:00",
                "active": True,
                "created_at": minute_ctx.now.isoformat(),
                "last_triggered_at": None,
                "cancelled_at": None,
            }
        ])
        mock_brain.router.complete = AsyncMock(return_value="")

        await ReminderDispatchAction().execute(minute_ctx, mock_brain, mock_bot)

        mock_bot.send_message.assert_awaited_once_with(chat_id="c1", text="提醒你：喝水")

    async def test_reminder_sanitizes_model_message(self, minute_ctx, mock_brain, mock_bot):
        mock_brain.memory.get_due_reminders = AsyncMock(return_value=[
            {
                "id": 101,
                "chat_id": "c1",
                "content": "出门散步",
                "recurrence_type": "once",
                "next_trigger_at": minute_ctx.now.isoformat(),
                "weekday": None,
                "time_of_day": None,
                "active": True,
                "created_at": minute_ctx.now.isoformat(),
                "last_triggered_at": None,
                "cancelled_at": None,
            }
        ])
        mock_brain.router.complete = AsyncMock(return_value="<think>内部</think>提醒你去散步")

        await ReminderDispatchAction().execute(minute_ctx, mock_brain, mock_bot)

        mock_bot.send_message.assert_awaited_once_with(chat_id="c1", text="提醒你去散步")
