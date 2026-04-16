"""测试 Cron Agent 执行模式。"""

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.reminder_scheduler import ReminderScheduler


@pytest.fixture
def mock_memory():
    mem = AsyncMock()
    mem.get_all_chat_ids = AsyncMock(return_value=["chat_1"])
    mem.list_reminders = AsyncMock(return_value=[])
    mem.append = AsyncMock()
    mem.complete_or_reschedule_reminder = AsyncMock(return_value=True)
    mem.get_reminder_by_id = AsyncMock(return_value=None)
    return mem


@pytest.fixture
def mock_send_fn():
    return AsyncMock()


@pytest.fixture
def mock_brain():
    brain = AsyncMock()
    brain.think_conversational = AsyncMock(return_value="道奇今晚10点打巨人，Sasaki先发")
    return brain


@pytest.mark.asyncio
async def test_notify_mode_uses_existing_behavior(mock_memory, mock_send_fn):
    """notify 模式走原有文本推送。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=1, chat_id="chat_1", content="该开会了",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
        execution_mode="notify",
    )

    await asyncio.sleep(0.5)

    mock_send_fn.assert_called_once()
    msg = mock_send_fn.call_args[0][0]
    assert msg.startswith("⏰")
    assert "该开会了" in msg
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_mode_calls_brain_think(mock_memory, mock_send_fn, mock_brain):
    """agent 模式调用 brain.think_conversational。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    scheduler._brain = mock_brain
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=2, chat_id="chat_1", content="查道奇比赛",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
        execution_mode="agent",
    )

    await asyncio.sleep(0.5)

    mock_brain.think_conversational.assert_called_once()
    call_kwargs = mock_brain.think_conversational.call_args
    # 验证 user_message 包含 "[定时任务]" 前缀
    user_msg = call_kwargs.kwargs.get("user_message") or call_kwargs[1].get("user_message")
    assert "[定时任务]" in user_msg
    assert "查道奇比赛" in user_msg
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_mode_silent_suppresses_send(mock_memory, mock_send_fn, mock_brain):
    """agent 输出以 [SILENT] 开头时不发送。"""
    mock_brain.think_conversational.return_value = "[SILENT] 没有新比赛"

    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    scheduler._brain = mock_brain
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=3, chat_id="chat_1", content="查比赛",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
        execution_mode="agent",
    )

    await asyncio.sleep(0.5)

    mock_send_fn.assert_not_called()
    # 但 DB 应该更新
    mock_memory.complete_or_reschedule_reminder.assert_called_once()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_mode_sends_result(mock_memory, mock_send_fn, mock_brain):
    """agent 有价值输出时发送给用户。"""
    mock_brain.think_conversational.return_value = "道奇今晚10点打巨人，Sasaki先发"

    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    scheduler._brain = mock_brain
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=4, chat_id="chat_1", content="查道奇比赛",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
        execution_mode="agent",
    )

    await asyncio.sleep(0.5)

    mock_send_fn.assert_called_once_with("道奇今晚10点打巨人，Sasaki先发")
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_mode_failure_fallback(mock_memory, mock_send_fn, mock_brain):
    """agent 执行失败时 fallback 到简单通知。"""
    mock_brain.think_conversational.side_effect = RuntimeError("LLM 超时")

    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    scheduler._brain = mock_brain
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=5, chat_id="chat_1", content="查天气",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
        execution_mode="agent",
    )

    await asyncio.sleep(0.5)

    mock_send_fn.assert_called_once()
    msg = mock_send_fn.call_args[0][0]
    assert "查天气" in msg
    assert "自动执行失败" in msg
    # 不应崩溃，DB 应该更新
    mock_memory.complete_or_reschedule_reminder.assert_called_once()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_default_execution_mode_is_notify(mock_memory, mock_send_fn):
    """未指定 execution_mode 时默认为 notify。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    now = datetime.now(timezone.utc)
    # 不传 execution_mode
    scheduler.notify_new(
        reminder_id=6, chat_id="chat_1", content="默认模式测试",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
    )

    await asyncio.sleep(0.5)

    # 应该走 notify 路径
    mock_send_fn.assert_called_once()
    msg = mock_send_fn.call_args[0][0]
    assert msg.startswith("⏰")
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_mode_without_brain_fallback(mock_memory, mock_send_fn):
    """没有 brain 引用时 agent 模式 fallback 到 notify。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    # 不设置 _brain
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=7, chat_id="chat_1", content="无brain测试",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
        execution_mode="agent",
    )

    await asyncio.sleep(0.5)

    # 应该 fallback 到 notify
    mock_send_fn.assert_called_once()
    msg = mock_send_fn.call_args[0][0]
    assert msg.startswith("⏰")
    await scheduler.shutdown()
