"""main.py 命令处理测试。"""

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def main_module():
    for mod in list(sys.modules.keys()):
        if mod == "main":
            del sys.modules[mod]

    with patch("src.core.brain.LapwingBrain") as mock_brain_cls:
        module = importlib.import_module("main")
        module.brain = mock_brain_cls.return_value
        yield module

    for mod in list(sys.modules.keys()):
        if mod == "main":
            del sys.modules[mod]


def make_update(chat_id: int = 42):
    update = MagicMock()
    update.message = MagicMock()
    update.message.chat_id = chat_id
    update.message.reply_text = AsyncMock()
    return update


@pytest.mark.asyncio
class TestInterestsCommand:
    async def test_cmd_interests_returns_ranked_list(self, main_module):
        main_module.brain.memory.get_top_interests = AsyncMock(return_value=[
            {"topic": "Python 编程", "weight": 8.52},
            {"topic": "机器学习", "weight": 4.21},
        ])
        update = make_update()

        await main_module.cmd_interests(update, MagicMock())

        update.message.reply_text.assert_awaited_once_with(
            "你目前记录的兴趣话题：\n"
            "1. Python 编程（权重 8.5）\n"
            "2. 机器学习（权重 4.2）"
        )

    async def test_cmd_interests_returns_empty_state(self, main_module):
        main_module.brain.memory.get_top_interests = AsyncMock(return_value=[])
        update = make_update()

        await main_module.cmd_interests(update, MagicMock())

        update.message.reply_text.assert_awaited_once_with(
            "我还没有记录到明显的兴趣话题。"
        )
