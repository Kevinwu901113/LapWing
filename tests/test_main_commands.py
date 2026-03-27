"""Telegram 命令行为测试（通过 TelegramApp）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.telegram_app import TelegramApp


@pytest.fixture
def telegram_app():
    brain = MagicMock()
    brain.memory = MagicMock()
    container = SimpleNamespace(
        brain=brain,
        start=AsyncMock(),
        shutdown=AsyncMock(),
    )
    return TelegramApp(container=container), brain


def make_update(chat_id: int = 42):
    update = MagicMock()
    update.message = MagicMock()
    update.message.chat_id = chat_id
    update.message.reply_text = AsyncMock()
    return update


@pytest.mark.asyncio
class TestInterestsCommand:
    async def test_cmd_interests_returns_ranked_list(self, telegram_app):
        app, brain = telegram_app
        brain.memory.get_top_interests = AsyncMock(return_value=[
            {"topic": "Python 编程", "weight": 8.52},
            {"topic": "机器学习", "weight": 4.21},
        ])
        update = make_update()

        await app.cmd_interests(update, MagicMock())

        update.message.reply_text.assert_awaited_once_with(
            "你目前记录的兴趣话题：\n"
            "1. Python 编程（权重 8.5）\n"
            "2. 机器学习（权重 4.2）"
        )

    async def test_cmd_interests_returns_empty_state(self, telegram_app):
        app, brain = telegram_app
        brain.memory.get_top_interests = AsyncMock(return_value=[])
        update = make_update()

        await app.cmd_interests(update, MagicMock())
        update.message.reply_text.assert_awaited_once_with("我还没有记录到明显的兴趣话题。")


@pytest.mark.asyncio
class TestMemoryCommand:
    async def test_cmd_memory_returns_visible_facts_only(self, telegram_app):
        app, brain = telegram_app
        brain.memory.get_user_facts = AsyncMock(return_value=[
            {"fact_key": "偏好_语言", "fact_value": "中文", "updated_at": "2026-03-24"},
            {"fact_key": "memory_summary_2026-03-23", "fact_value": "聊了工作。", "updated_at": "2026-03-23"},
            {"fact_key": "项目_方向", "fact_value": "RAG", "updated_at": "2026-03-22"},
        ])
        update = make_update()
        context = MagicMock(args=[])

        await app.cmd_memory(update, context)
        update.message.reply_text.assert_awaited_once_with(
            "你记住了以下关于我的信息：\n"
            "1. [偏好_语言] 中文\n"
            "2. [项目_方向] RAG"
        )

    async def test_cmd_memory_returns_empty_state(self, telegram_app):
        app, brain = telegram_app
        brain.memory.get_user_facts = AsyncMock(return_value=[])
        update = make_update()
        context = MagicMock(args=[])

        await app.cmd_memory(update, context)
        update.message.reply_text.assert_awaited_once_with("我现在还没有记住关于你的信息。")

    async def test_cmd_memory_delete_by_visible_index(self, telegram_app):
        app, brain = telegram_app
        brain.memory.get_user_facts = AsyncMock(return_value=[
            {"fact_key": "个人_姓名", "fact_value": "小明", "updated_at": "2026-03-24"},
            {"fact_key": "memory_summary_2026-03-23", "fact_value": "聊了论文。", "updated_at": "2026-03-23"},
            {"fact_key": "偏好_语言", "fact_value": "中文", "updated_at": "2026-03-22"},
        ])
        brain.memory.delete_user_fact = AsyncMock(return_value=True)
        update = make_update()
        context = MagicMock(args=["delete", "2"])

        await app.cmd_memory(update, context)

        brain.memory.delete_user_fact.assert_awaited_once_with("42", "偏好_语言")
        update.message.reply_text.assert_awaited_once_with("这条记忆已经删掉了。")

    async def test_cmd_memory_returns_usage_when_args_invalid(self, telegram_app):
        app, brain = telegram_app
        brain.memory.get_user_facts = AsyncMock(return_value=[])

        update = make_update()
        await app.cmd_memory(update, MagicMock(args=["delete"]))
        update.message.reply_text.assert_awaited_once_with("用法：/memory delete <编号>")

        update = make_update()
        await app.cmd_memory(update, MagicMock(args=["delete", "abc"]))
        update.message.reply_text.assert_awaited_once_with("用法：/memory delete <编号>")

    async def test_cmd_memory_returns_not_found_when_index_out_of_range(self, telegram_app):
        app, brain = telegram_app
        brain.memory.get_user_facts = AsyncMock(return_value=[
            {"fact_key": "偏好_语言", "fact_value": "中文", "updated_at": "2026-03-24"},
        ])
        update = make_update()
        context = MagicMock(args=["delete", "2"])

        await app.cmd_memory(update, context)
        update.message.reply_text.assert_awaited_once_with("没有这条记忆")


@pytest.mark.asyncio
class TestModelCommand:
    async def test_cmd_model_default_resets_session_override(self, telegram_app):
        app, brain = telegram_app
        brain.reset_model = MagicMock(return_value={"cleared": 3})
        update = make_update()
        context = MagicMock(args=["default"])

        await app.cmd_model(update, context)

        brain.reset_model.assert_called_once_with("42")
        reply = update.message.reply_text.await_args.args[0]
        assert "已恢复默认模型" in reply
