"""ConversationMemory 记忆管理、待办与提醒测试。"""

import pytest
from datetime import datetime, timedelta, timezone

from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    store = ConversationMemory(tmp_path / "test.db")
    await store.init_db()
    yield store
    await store.close()


# TestUserFactsDeletion removed (Phase 1: user_facts facade removed)


@pytest.mark.asyncio
class TestTodos:
    async def test_add_todo_returns_id_and_lists_item(self, memory):
        todo_id = await memory.add_todo("c1", "买牛奶", "2026-03-25")
        todos = await memory.list_todos("c1")

        assert todo_id > 0
        assert todos == [
            {
                "id": todo_id,
                "content": "买牛奶",
                "due_date": "2026-03-25",
                "done": False,
                "created_at": todos[0]["created_at"],
            }
        ]

    async def test_list_todos_sorted_by_done_due_date_then_created_at(self, memory):
        first_id = await memory.add_todo("c1", "无截止日任务", None)
        second_id = await memory.add_todo("c1", "先到期", "2026-03-24")
        third_id = await memory.add_todo("c1", "后到期", "2026-03-25")
        await memory.mark_todo_done("c1", second_id)

        todos = await memory.list_todos("c1")

        assert [item["id"] for item in todos] == [third_id, first_id, second_id]

    async def test_mark_todo_done_respects_chat_scope(self, memory):
        todo_id = await memory.add_todo("c1", "只属于 c1", None)

        success = await memory.mark_todo_done("c2", todo_id)
        todos = await memory.list_todos("c1")

        assert success is False
        assert todos[0]["done"] is False

    async def test_delete_todo_respects_chat_scope(self, memory):
        todo_id = await memory.add_todo("c1", "只属于 c1", None)

        success = await memory.delete_todo("c2", todo_id)
        todos = await memory.list_todos("c1")

        assert success is False
        assert len(todos) == 1

    async def test_delete_todo_success(self, memory):
        todo_id = await memory.add_todo("c1", "买牛奶", None)

        success = await memory.delete_todo("c1", todo_id)
        todos = await memory.list_todos("c1")

        assert success is True
        assert todos == []


# TestClearMemory removed (Phase 1: user_facts/discoveries/interests facades removed)


@pytest.mark.asyncio
class TestReminders:
    async def test_once_reminder_triggered_once_then_inactive(self, memory):
        now = datetime.now(timezone.utc)
        reminder_id = await memory.add_reminder(
            chat_id="c1",
            content="起身走两分钟",
            recurrence_type="once",
            next_trigger_at=now + timedelta(minutes=1),
        )
        assert reminder_id > 0

        due = await memory.get_due_reminders(
            chat_id="c1",
            now=now + timedelta(minutes=1, seconds=10),
            grace_seconds=90,
            limit=10,
        )
        assert [item["id"] for item in due] == [reminder_id]

        finished = await memory.complete_or_reschedule_reminder(
            reminder_id,
            now=now + timedelta(minutes=1, seconds=10),
        )
        assert finished is True

        reminders = await memory.list_reminders("c1")
        assert reminders == []

    async def test_daily_reminder_reschedules_after_dispatch(self, memory):
        now = datetime.now(timezone.utc)
        reminder_id = await memory.add_reminder(
            chat_id="c1",
            content="喝水",
            recurrence_type="daily",
            next_trigger_at=now + timedelta(minutes=1),
            time_of_day="09:30",
        )
        assert reminder_id > 0

        dispatched_at = now + timedelta(minutes=1, seconds=5)
        ok = await memory.complete_or_reschedule_reminder(reminder_id, dispatched_at)
        assert ok is True

        reminders = await memory.list_reminders("c1")
        assert len(reminders) == 1
        next_trigger = datetime.fromisoformat(reminders[0]["next_trigger_at"])
        assert next_trigger > dispatched_at
        assert (next_trigger - dispatched_at) <= timedelta(days=1, minutes=1)

    async def test_weekly_reminder_reschedules_after_dispatch(self, memory):
        now = datetime.now(timezone.utc)
        reminder_id = await memory.add_reminder(
            chat_id="c1",
            content="周会准备",
            recurrence_type="weekly",
            next_trigger_at=now + timedelta(minutes=1),
            weekday=(now.weekday() + 1) % 7,
            time_of_day="10:00",
        )
        assert reminder_id > 0

        dispatched_at = now + timedelta(minutes=1, seconds=5)
        ok = await memory.complete_or_reschedule_reminder(reminder_id, dispatched_at)
        assert ok is True

        reminders = await memory.list_reminders("c1")
        assert len(reminders) == 1
        next_trigger = datetime.fromisoformat(reminders[0]["next_trigger_at"])
        assert next_trigger > dispatched_at
        assert (next_trigger - dispatched_at) <= timedelta(days=7, minutes=1)

    async def test_cancel_reminder_respects_chat_scope(self, memory):
        reminder_id = await memory.add_reminder(
            chat_id="c1",
            content="睡前收尾",
            recurrence_type="daily",
            next_trigger_at=datetime.now(timezone.utc) + timedelta(hours=1),
            time_of_day="22:00",
        )
        assert reminder_id > 0

        wrong_chat_result = await memory.cancel_reminder("c2", reminder_id)
        assert wrong_chat_result is False

        correct_chat_result = await memory.cancel_reminder("c1", reminder_id)
        assert correct_chat_result is True
        assert await memory.list_reminders("c1") == []

    async def test_due_reminder_isolated_by_chat(self, memory):
        now = datetime.now(timezone.utc)
        r1 = await memory.add_reminder(
            chat_id="c1",
            content="提醒 c1",
            recurrence_type="once",
            next_trigger_at=now + timedelta(minutes=1),
        )
        r2 = await memory.add_reminder(
            chat_id="c2",
            content="提醒 c2",
            recurrence_type="once",
            next_trigger_at=now + timedelta(minutes=1),
        )
        assert r1 > 0 and r2 > 0

        due_c1 = await memory.get_due_reminders(
            chat_id="c1",
            now=now + timedelta(minutes=1, seconds=1),
            grace_seconds=30,
            limit=10,
        )
        assert [item["id"] for item in due_c1] == [r1]

    async def test_missed_once_reminder_dropped_without_backfill(self, memory):
        now = datetime.now(timezone.utc)
        reminder_id = await memory.add_reminder(
            chat_id="c1",
            content="错过了就算了",
            recurrence_type="once",
            next_trigger_at=now + timedelta(minutes=1),
        )
        assert reminder_id > 0

        # 超过 grace 窗口，按策略丢弃，不补发
        due = await memory.get_due_reminders(
            chat_id="c1",
            now=now + timedelta(minutes=5),
            grace_seconds=30,
            limit=10,
        )
        assert due == []
        assert await memory.list_reminders("c1") == []
