"""brain.py 用户画像注入相关测试。"""

import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_NONEXISTENT = Path("/nonexistent")


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


def _mock_load_prompt(name, **kwargs):
    """按 prompt 名称返回不同 mock 内容，隔离 load_prompt 副作用。"""
    if name == "lapwing_soul":
        return "基础人格 prompt"
    return ""


def make_brain():
    """创建隔离的 LapwingBrain 实例，所有文件路径指向不存在的位置。"""
    stack = ExitStack()
    stack.enter_context(patch("src.core.brain.load_prompt", side_effect=_mock_load_prompt))
    stack.enter_context(patch("src.core.brain.LLMRouter"))
    stack.enter_context(patch("src.core.brain.ConversationMemory"))
    stack.enter_context(patch("src.core.brain.SOUL_PATH", _NONEXISTENT / "soul.md"))
    stack.enter_context(patch("src.core.prompt_builder.RULES_PATH", _NONEXISTENT / "rules.md"))
    stack.enter_context(patch("src.core.prompt_builder.KEVIN_NOTES_PATH", _NONEXISTENT / "kevin.md"))
    stack.enter_context(patch("src.core.prompt_builder.CONVERSATION_SUMMARIES_DIR", _NONEXISTENT / "summaries"))
    return stack


class TestBuildSystemPrompt:
    async def test_returns_base_prompt_when_no_facts(self):
        """没有用户 facts 时，基础 prompt 出现在结果开头。"""
        with make_brain() as stack:
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert result.startswith("基础人格 prompt")

    async def test_appends_user_facts_section_when_facts_exist(self):
        """有用户 facts 时，在基础 prompt 后追加用户画像段落。"""
        with make_brain():
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
        with make_brain():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "偏好_食物_不吃辣", "fact_value": "不喜欢辣"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert result.index("基础人格 prompt") < result.index("偏好_食物_不吃辣")

    async def test_memory_summary_facts_not_injected_in_system_prompt(self):
        """memory_summary_* facts 不再注入 system prompt（摘要由 Compactor 的文件管理）。"""
        with make_brain():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "偏好_食物_不吃辣", "fact_value": "不喜欢吃辣", "updated_at": "2026-03-23"},
                {"fact_key": "memory_summary_2026-03-23", "fact_value": "今天聊了面试和睡眠。", "updated_at": "2026-03-23"},
            ])
            result = await brain._build_system_prompt("chat1")
            # 普通 fact 仍然注入
            assert "## 补充信息（自动提取）" in result
            assert "偏好_食物_不吃辣" in result
            # memory_summary_* 不再出现
            assert "## 最近聊过的事" not in result
            assert "memory_summary_2026-03-23" not in result

    async def test_memory_summary_facts_ignored_not_shown_in_facts_section(self):
        """facts 段落不含 memory_summary_* 条目。"""
        with make_brain():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "memory_summary_2026-03-23", "fact_value": "只有 summary", "updated_at": "2026-03-23"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert result.startswith("基础人格 prompt")
            assert "## 补充信息（自动提取）" not in result
            assert "只有 summary" not in result

    async def test_appends_related_history_section_from_vector_hits(self):
        with make_brain():
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

    async def test_vector_hits_appear_regardless_of_memory_summary_facts(self):
        """memory_summary_* facts 不再做 summary_dates 去重，向量命中正常显示。"""
        with make_brain():
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

            # 向量命中不再被 memory_summary 去重，应当出现
            assert "## 相关历史记忆" in result

    async def test_related_history_search_failure_is_ignored(self):
        with make_brain():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.vector_store = MagicMock()
            brain.vector_store.search = AsyncMock(side_effect=RuntimeError("boom"))

            result = await brain._build_system_prompt("chat1", "继续聊")

            assert result.startswith("基础人格 prompt")
            assert "## 本地执行状态" in result

    async def test_injects_skill_catalog_when_skills_available(self):
        with make_brain():
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
        with make_brain():
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
        with make_brain():
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
