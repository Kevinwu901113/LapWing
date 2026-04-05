"""ReminderScheduler 单元测试。"""

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


@pytest.mark.asyncio
async def test_start_loads_active_reminders(mock_memory, mock_send_fn):
    """启动时从 DB 加载 active reminders。"""
    now = datetime.now(timezone.utc)
    mock_memory.list_reminders.return_value = [
        {
            "id": 1, "chat_id": "chat_1", "content": "test",
            "next_trigger_at": (now + timedelta(hours=1)).isoformat(),
            "recurrence_type": "once", "active": True,
        }
    ]
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    assert 1 in scheduler._tasks
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_notify_new_schedules_task(mock_memory, mock_send_fn):
    """notify_new 立即创建调度 Task。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=42, chat_id="chat_1", content="test reminder",
        next_trigger_at=now + timedelta(seconds=60),
        recurrence_type="once",
    )

    assert 42 in scheduler._tasks
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_notify_cancel_removes_task(mock_memory, mock_send_fn):
    """notify_cancel 取消并移除 Task。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=42, chat_id="chat_1", content="test",
        next_trigger_at=now + timedelta(hours=1),
        recurrence_type="once",
    )
    assert 42 in scheduler._tasks

    scheduler.notify_cancel(42)
    await asyncio.sleep(0.05)
    assert 42 not in scheduler._tasks
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_fire_sends_message_and_updates_db(mock_memory, mock_send_fn):
    """到期后发送消息并更新 DB。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=99, chat_id="chat_1", content="该开会了",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
    )

    await asyncio.sleep(0.5)

    mock_send_fn.assert_called_once()
    msg = mock_send_fn.call_args[0][0]
    assert "该开会了" in msg
    assert msg.startswith("⏰")

    mock_memory.append.assert_called_once_with("chat_1", "assistant", msg)
    mock_memory.complete_or_reschedule_reminder.assert_called_once()


@pytest.mark.asyncio
async def test_overdue_fires_immediately(mock_memory, mock_send_fn):
    """已过期的提醒立即触发。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    scheduler.notify_new(
        reminder_id=1, chat_id="chat_1", content="overdue",
        next_trigger_at=past, recurrence_type="once",
    )

    await asyncio.sleep(0.3)
    mock_send_fn.assert_called_once()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_all(mock_memory, mock_send_fn):
    """shutdown 取消所有 pending Task。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()

    now = datetime.now(timezone.utc)
    for i in range(5):
        scheduler.notify_new(
            reminder_id=i, chat_id="chat_1", content=f"r{i}",
            next_trigger_at=now + timedelta(hours=1),
            recurrence_type="once",
        )
    assert len(scheduler._tasks) == 5

    await scheduler.shutdown()
    assert len(scheduler._tasks) == 0
    mock_send_fn.assert_not_called()


@pytest.mark.asyncio
async def test_event_bus_published_on_fire(mock_memory, mock_send_fn):
    """提醒触发时发布 event_bus 事件。"""
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()

    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn, event_bus=event_bus)
    await scheduler.start()

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=7, chat_id="chat_1", content="开会",
        next_trigger_at=now + timedelta(seconds=0.05),
        recurrence_type="once",
    )

    await asyncio.sleep(0.5)

    event_bus.publish.assert_called_once()
    call_args = event_bus.publish.call_args
    assert call_args[0][0] == "reminder_message"
    assert call_args[0][1]["chat_id"] == "chat_1"
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_notify_new_before_start_is_ignored(mock_memory, mock_send_fn):
    """未启动时调用 notify_new 不产生 Task。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)

    now = datetime.now(timezone.utc)
    scheduler.notify_new(
        reminder_id=1, chat_id="chat_1", content="test",
        next_trigger_at=now + timedelta(hours=1),
        recurrence_type="once",
    )

    assert len(scheduler._tasks) == 0


@pytest.mark.asyncio
async def test_double_start_is_idempotent(mock_memory, mock_send_fn):
    """重复调用 start 只加载一次。"""
    scheduler = ReminderScheduler(memory=mock_memory, send_fn=mock_send_fn)
    await scheduler.start()
    await scheduler.start()

    mock_memory.get_all_chat_ids.assert_called_once()
    await scheduler.shutdown()
