"""tests/core/test_brain_system_prompt.py — 分层 system prompt 测试。"""

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_NONEXISTENT = Path("/nonexistent")


@pytest.fixture(autouse=True)
def reset_module_cache():
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]


def _mock_load_prompt(name, **kwargs):
    if name == "lapwing_soul":
        return "SOUL"
    return ""


def base_brain_stack(**overrides):
    """返回 ExitStack，包含所有标准 mock。overrides 可替换特定路径 mock。"""
    stack = ExitStack()
    stack.enter_context(patch("src.core.brain.load_prompt", side_effect=_mock_load_prompt))
    stack.enter_context(patch("src.core.brain.LLMRouter"))
    stack.enter_context(patch("src.core.brain.ConversationMemory"))
    stack.enter_context(patch("src.core.brain.SOUL_PATH", overrides.get("soul", _NONEXISTENT / "soul.md")))
    stack.enter_context(patch("src.core.brain.RULES_PATH", overrides.get("rules", _NONEXISTENT / "rules.md")))
    stack.enter_context(patch("src.core.brain.KEVIN_NOTES_PATH", overrides.get("kevin", _NONEXISTENT / "kevin.md")))
    stack.enter_context(patch("src.core.brain.CONVERSATION_SUMMARIES_DIR", overrides.get("summaries", _NONEXISTENT / "summaries")))
    return stack


class TestLayerOrdering:
    async def test_soul_is_always_first_section(self):
        """核心人格始终是第一 section。"""
        with base_brain_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert result.startswith("SOUL")

    async def test_soul_before_kevin_notes(self, tmp_path):
        """soul 在 kevin notes 之前。"""
        kevin_file = tmp_path / "kevin.md"
        kevin_file.write_text("Kevin 的信息", encoding="utf-8")
        with base_brain_stack(kevin=kevin_file):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert result.index("SOUL") < result.index("Kevin 的信息")

    async def test_kevin_notes_before_capabilities(self, tmp_path):
        """kevin notes 在 capabilities 之前。"""
        kevin_file = tmp_path / "kevin.md"
        kevin_file.write_text("Kevin 信息", encoding="utf-8")

        def mock_load(name, **kwargs):
            if name == "lapwing_soul":
                return "SOUL"
            if name == "lapwing_capabilities":
                return "CAPABILITIES"
            return ""

        with ExitStack() as stack:
            stack.enter_context(patch("src.core.brain.load_prompt", side_effect=mock_load))
            stack.enter_context(patch("src.core.brain.LLMRouter"))
            stack.enter_context(patch("src.core.brain.ConversationMemory"))
            stack.enter_context(patch("src.core.brain.SOUL_PATH", _NONEXISTENT / "soul.md"))
            stack.enter_context(patch("src.core.brain.RULES_PATH", _NONEXISTENT / "rules.md"))
            stack.enter_context(patch("src.core.brain.KEVIN_NOTES_PATH", kevin_file))
            stack.enter_context(patch("src.core.brain.CONVERSATION_SUMMARIES_DIR", _NONEXISTENT / "summaries"))
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert result.index("Kevin 信息") < result.index("CAPABILITIES")


class TestLayer1Rules:
    async def test_rules_injected_when_file_has_content(self, tmp_path):
        """规则文件有实质内容时注入。"""
        rules_file = tmp_path / "rules.md"
        rules_file.write_text("# 行为规则\n\n- 不要乱说话", encoding="utf-8")
        with base_brain_stack(rules=rules_file):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert "## 你从经验中学到的规则" in result
            assert "不要乱说话" in result

    async def test_rules_skipped_when_contains_placeholder(self, tmp_path):
        """规则文件包含"暂无规则"时跳过。"""
        rules_file = tmp_path / "rules.md"
        rules_file.write_text("（暂无规则。）", encoding="utf-8")
        with base_brain_stack(rules=rules_file):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert "## 你从经验中学到的规则" not in result

    async def test_rules_skipped_when_file_missing(self):
        """规则文件不存在时跳过。"""
        with base_brain_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert "## 你从经验中学到的规则" not in result


class TestLayer2KevinNotes:
    async def test_kevin_notes_injected_when_file_exists(self, tmp_path):
        """KEVIN.md 存在时注入。"""
        kevin_file = tmp_path / "kevin.md"
        kevin_file.write_text("# 关于 Kevin\n\n他喜欢摄影。", encoding="utf-8")
        with base_brain_stack(kevin=kevin_file):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert "## 你对他的了解" in result
            assert "他喜欢摄影" in result

    async def test_kevin_notes_skipped_when_missing(self):
        """KEVIN.md 不存在时跳过。"""
        with base_brain_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert "## 你对他的了解" not in result


class TestLayer3FileSummaries:
    async def test_file_summaries_injected_when_present(self, tmp_path):
        """摘要目录有文件时注入。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        (summaries_dir / "2026-03-29_120000.md").write_text("# 摘要\n\n今天聊了游戏。", encoding="utf-8")
        with base_brain_stack(summaries=summaries_dir):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert "## 最近的对话" in result
            assert "今天聊了游戏" in result

    async def test_file_summaries_skipped_when_dir_empty(self, tmp_path):
        """摘要目录为空时跳过。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        with base_brain_stack(summaries=summaries_dir):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")
            assert "## 最近的对话" not in result


class TestLayer25SqliteFacts:
    async def test_sqlite_facts_still_shown(self):
        """SQLite facts 作为补充仍然显示。"""
        with base_brain_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[
                {"fact_key": "习惯_起床时间", "fact_value": "早上七点"},
            ])
            result = await brain._build_system_prompt("chat1")
            assert "## 补充信息（自动提取）" in result
            assert "习惯_起床时间" in result
