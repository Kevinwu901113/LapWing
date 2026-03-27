"""brain.py 用户画像注入相关测试。"""

import sys
from pathlib import Path
from types import SimpleNamespace
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

    async def test_appends_related_history_section_from_vector_hits(self):
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.vector_store = MagicMock()
            brain.vector_store.search = AsyncMock(return_value=[
                {
                    "text": "之前聊过 RAG 和论文选题。",
                    "metadata": {"date": "2026-03-20"},
                    "distance": 0.12,
                }
            ])

            result = await brain._build_system_prompt("chat1", "我想继续聊论文")

            assert "## 相关历史记忆" in result
            assert "- 2026-03-20: 之前聊过 RAG 和论文选题。" in result
            brain.vector_store.search.assert_awaited_once_with("chat1", "我想继续聊论文", n_results=2)

    async def test_skips_related_history_when_date_already_in_recent_summaries(self):
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "memory_summary_2026-03-23", "fact_value": "今天聊了工作安排。", "updated_at": "2026-03-23"},
            ])
            brain.vector_store = MagicMock()
            brain.vector_store.search = AsyncMock(return_value=[
                {
                    "text": "今天聊了工作安排。",
                    "metadata": {"date": "2026-03-23"},
                    "distance": 0.1,
                }
            ])

            result = await brain._build_system_prompt("chat1", "继续聊工作")

            assert "## 相关历史记忆" not in result

    async def test_related_history_search_failure_is_ignored(self):
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.vector_store = MagicMock()
            brain.vector_store.search = AsyncMock(side_effect=RuntimeError("boom"))

            result = await brain._build_system_prompt("chat1", "继续聊")

            assert result.startswith("基础人格 prompt")
            assert "## 本地执行规则" in result

    async def test_injects_skill_catalog_when_skills_available(self):
        with patch("src.core.brain.load_prompt", return_value="基础人格 prompt"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.skill_manager = MagicMock()
            brain.skill_manager.has_model_visible_skills.return_value = True
            brain.skill_manager.render_catalog_for_prompt.return_value = (
                "<available_skills><skill><name>demo</name></skill></available_skills>"
            )

            result = await brain._build_system_prompt("chat1")

            assert "## 可用技能目录" in result
            assert "<available_skills>" in result
            assert "<name>demo</name>" in result


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
            brain.router.complete_with_tools = AsyncMock(
                return_value=SimpleNamespace(
                    text="回复",
                    tool_calls=[],
                    continuation_message=None,
                )
            )
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
            brain.router.complete_with_tools = AsyncMock(
                return_value=SimpleNamespace(
                    text="回复",
                    tool_calls=[],
                    continuation_message=None,
                )
            )
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()
            brain.interest_tracker = MagicMock()
            brain.interest_tracker.notify = MagicMock()

            await brain.think("chat1", "你好")

            brain.interest_tracker.notify.assert_called_once_with("chat1")
