"""TodoAgent 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import AgentTask
from src.agents.todo_agent import TodoAgent


def make_task(user_message: str = "帮我记个待办，明天交周报") -> AgentTask:
    return AgentTask(
        chat_id="42",
        user_message=user_message,
        history=[],
        user_facts=[],
    )


def make_memory():
    memory = MagicMock()
    memory.add_todo = AsyncMock(return_value=3)
    memory.list_todos = AsyncMock(return_value=[])
    memory.mark_todo_done = AsyncMock(return_value=True)
    memory.delete_todo = AsyncMock(return_value=True)
    memory.add_reminder = AsyncMock(return_value=11)
    memory.list_reminders = AsyncMock(return_value=[])
    memory.cancel_reminder = AsyncMock(return_value=True)
    return memory


@pytest.mark.asyncio
class TestTodoAgent:
    async def test_add_todo(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(
            return_value='{"action":"add","todo_id":null,"content":"交周报","due_date":"2026-03-25","reason":null}'
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task(), router)

        memory.add_todo.assert_awaited_once_with("42", "交周报", "2026-03-25")
        assert result.content == "已添加待办 #3：交周报（截止 2026-03-25）"
        assert result.needs_persona_formatting is True

    async def test_list_todos_formats_stable_ids(self):
        memory = make_memory()
        memory.list_todos = AsyncMock(return_value=[
            {"id": 3, "content": "交周报", "due_date": "2026-03-25", "done": False, "created_at": "x"},
            {"id": 4, "content": "复盘", "due_date": None, "done": True, "created_at": "y"},
        ])
        router = MagicMock()
        router.complete = AsyncMock(
            return_value='{"action":"list","todo_id":null,"content":null,"due_date":null,"reason":null}'
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task("列出我的待办"), router)

        assert result.content == (
            "当前待办：\n"
            "- [ ] #3 交周报（截止 2026-03-25）\n"
            "- [x] #4 复盘"
        )

    async def test_done_todo_uses_chat_scope(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(
            return_value='{"action":"done","todo_id":7,"content":null,"due_date":null,"reason":null}'
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task("把 7 号待办完成"), router)

        memory.mark_todo_done.assert_awaited_once_with("42", 7)
        assert result.content == "已完成待办 #7。"

    async def test_delete_todo_returns_not_found(self):
        memory = make_memory()
        memory.delete_todo = AsyncMock(return_value=False)
        router = MagicMock()
        router.complete = AsyncMock(
            return_value='{"action":"delete","todo_id":5,"content":null,"due_date":null,"reason":null}'
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task("删掉待办 5"), router)

        assert result.content == "没有这条待办。"

    async def test_add_todo_rejects_invalid_due_date(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(
            return_value='{"action":"add","todo_id":null,"content":"交周报","due_date":"明天","reason":null}'
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task(), router)

        assert "没能识别截止日期" in result.content
        memory.add_todo.assert_not_awaited()

    async def test_returns_error_message_on_invalid_json(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(return_value="not json")

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task("列出待办"), router)

        assert "没看懂" in result.content

    async def test_add_once_reminder(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(
            return_value=(
                '{"domain":"reminder","action":"reminder_add","content":"站起来活动",'
                '"recurrence_type":"once","trigger_at":"2099-03-25 18:30",'
                '"weekday":null,"time_of_day":null,"todo_id":null,"reminder_id":null,'
                '"due_date":null,"reason":null}'
            )
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {timezone} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task("1分钟后提醒我活动"), router)

        memory.add_reminder.assert_awaited_once()
        call = memory.add_reminder.call_args.kwargs
        assert call["chat_id"] == "42"
        assert call["content"] == "站起来活动"
        assert call["recurrence_type"] == "once"
        assert result.content.startswith("已添加提醒 #11：站起来活动")

    async def test_list_reminders(self):
        memory = make_memory()
        memory.list_reminders = AsyncMock(return_value=[
            {
                "id": 11,
                "chat_id": "42",
                "content": "喝水",
                "recurrence_type": "daily",
                "next_trigger_at": "2099-03-25T10:00:00+00:00",
                "weekday": None,
                "time_of_day": "18:00",
                "active": True,
                "created_at": "x",
                "last_triggered_at": None,
                "cancelled_at": None,
            }
        ])
        router = MagicMock()
        router.complete = AsyncMock(
            return_value=(
                '{"domain":"reminder","action":"reminder_list","content":null,'
                '"recurrence_type":null,"trigger_at":null,"weekday":null,'
                '"time_of_day":null,"todo_id":null,"reminder_id":null,'
                '"due_date":null,"reason":null}'
            )
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {timezone} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task("列出提醒"), router)

        assert result.content.startswith("当前提醒：")
        assert "#11 喝水" in result.content

    async def test_cancel_reminder(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(
            return_value=(
                '{"domain":"reminder","action":"reminder_cancel","content":null,'
                '"recurrence_type":null,"trigger_at":null,"weekday":null,"time_of_day":null,'
                '"todo_id":null,"reminder_id":11,"due_date":null,"reason":null}'
            )
        )

        with patch("src.agents.todo_agent.load_prompt", return_value="{today} {timezone} {user_message}"):
            result = await TodoAgent(memory=memory).execute(make_task("取消提醒11"), router)

        memory.cancel_reminder.assert_awaited_once_with("42", 11)
        assert result.content == "已取消提醒 #11。"
