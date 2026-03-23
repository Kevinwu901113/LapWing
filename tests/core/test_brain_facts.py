"""brain.py 用户画像注入相关测试。"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_module_cache():
    """每个测试前后清除 brain 和 fact_extractor 的模块缓存，确保测试隔离。"""
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]


class TestBuildSystemPrompt:
    async def test_returns_base_prompt_when_no_facts(self):
        """没有用户 facts 时，返回未修改的基础 prompt。"""
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert result == "基础人格 prompt"

    async def test_appends_user_facts_section_when_facts_exist(self):
        """有用户 facts 时，在基础 prompt 后追加用户画像段落。"""
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "偏好_食物_不吃辣", "fact_value": "不喜欢吃辣的食物"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert "基础人格 prompt" in result
            assert "偏好_食物_不吃辣" in result
            assert "不喜欢吃辣的食物" in result

    async def test_base_prompt_appears_before_facts(self):
        """基础 prompt 在 facts 段落之前。"""
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "偏好_食物_不吃辣", "fact_value": "不喜欢辣"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert result.index("基础人格 prompt") < result.index("偏好_食物_不吃辣")

    async def test_memory_summaries_are_placed_in_separate_section(self):
        """memory_summary_* 不应混在普通 facts 段落里。"""
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "偏好_食物_不吃辣", "fact_value": "不喜欢吃辣", "updated_at": "2026-03-23"},
                {"fact_key": "memory_summary_2026-03-23", "fact_value": "今天聊了面试和睡眠。", "updated_at": "2026-03-23"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert "## 你对这个用户的了解" in result
            assert "## 最近聊过的事" in result
            assert "- 2026-03-23: 今天聊了面试和睡眠。" in result
            user_section = result.split("## 最近聊过的事")[0]
            assert "memory_summary_2026-03-23" not in user_section

    async def test_only_latest_three_memory_summaries_are_kept(self):
        """最近聊过的事只保留最新三条。"""
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "memory_summary_2026-03-20", "fact_value": "20", "updated_at": "2026-03-20"},
                {"fact_key": "memory_summary_2026-03-21", "fact_value": "21", "updated_at": "2026-03-21"},
                {"fact_key": "memory_summary_2026-03-22", "fact_value": "22", "updated_at": "2026-03-22"},
                {"fact_key": "memory_summary_2026-03-23", "fact_value": "23", "updated_at": "2026-03-23"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert "- 2026-03-23: 23" in result
            assert "- 2026-03-22: 22" in result
            assert "- 2026-03-21: 21" in result
            assert "- 2026-03-20: 20" not in result

    async def test_returns_base_plus_recent_section_when_only_memory_summaries_exist(self):
        """只有 memory summaries 时，也应注入最近聊过的事段落。"""
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "memory_summary_2026-03-23", "fact_value": "今天聊了工作安排。", "updated_at": "2026-03-23"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert result.startswith("基础人格 prompt")
            assert "## 最近聊过的事" in result
            assert "## 你对这个用户的了解" not in result


class TestThinkNotifiesExtractor:
    async def test_think_calls_fact_extractor_notify(self):
        """think() 每次调用时通知 fact_extractor。"""
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.router.complete = AsyncMock(return_value="回复")
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            await brain.think("chat1", "你好")

            brain.fact_extractor.notify.assert_called_once_with("chat1")

    async def test_think_calls_interest_tracker_notify_when_present(self):
        """配置了 interest_tracker 时，think() 也会通知它。"""
        with patch("src.core.brain.load_prompt", return_value="prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()
            brain.router.complete = AsyncMock(return_value="回复")
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.interest_tracker = MagicMock()
            brain.interest_tracker.notify = MagicMock()

            await brain.think("chat1", "你好")

            brain.interest_tracker.notify.assert_called_once_with("chat1")
