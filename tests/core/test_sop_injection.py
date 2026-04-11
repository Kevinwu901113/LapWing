"""tests/core/test_sop_injection.py — Layer 6.5 SOP injection tests."""

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import src.core.prompt_builder as pb

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
    if name == "lapwing_capabilities":
        return "CAPABILITIES"
    return ""


def base_stack(**overrides):
    stack = ExitStack()
    load_fn = overrides.get("load_fn", _mock_load_prompt)
    stack.enter_context(patch("src.core.brain.load_prompt", side_effect=load_fn))
    stack.enter_context(patch("src.core.prompt_builder.load_prompt", side_effect=load_fn))
    stack.enter_context(patch("src.core.brain.LLMRouter"))
    stack.enter_context(patch("src.core.brain.ConversationMemory"))
    stack.enter_context(patch("src.core.brain.SOUL_PATH", overrides.get("soul", _NONEXISTENT / "soul.md")))
    stack.enter_context(patch("src.core.prompt_builder.RULES_PATH", overrides.get("rules", _NONEXISTENT / "rules.md")))
    stack.enter_context(patch("src.core.prompt_builder.KEVIN_NOTES_PATH", overrides.get("kevin", _NONEXISTENT / "kevin.md")))
    stack.enter_context(patch("src.core.prompt_builder.CONVERSATION_SUMMARIES_DIR", overrides.get("summaries", _NONEXISTENT / "summaries")))
    return stack


class TestSopInjection:
    async def test_sop_files_injected_from_real_dir(self):
        """真实 prompts/sop/ 目录中的 SOP 文件被注入到 system prompt。"""
        # 使用真实的 prompts/sop/ 目录（已存在 5 个文件）
        real_sop_dir = Path("prompts/sop")
        assert real_sop_dir.exists(), "prompts/sop/ 目录应存在"
        sop_files = sorted(real_sop_dir.glob("*.md"))
        assert len(sop_files) > 0, "prompts/sop/ 目录应有 .md 文件"

        with base_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")

        assert "# 标准操作流程" in result

        # 验证至少一个 SOP 文件的内容被注入
        any_injected = False
        for sop_file in sop_files:
            content = sop_file.read_text(encoding="utf-8").strip()
            if content and content[:30] in result:
                any_injected = True
                break
        assert any_injected, "至少一个 SOP 文件的内容应被注入"

    async def test_sop_appears_before_capabilities(self):
        """SOP 层（Layer 6.5）出现在 capabilities（Layer 7）之前。"""
        real_sop_dir = Path("prompts/sop")
        assert real_sop_dir.exists(), "prompts/sop/ 目录应存在"

        with base_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")

        assert "# 标准操作流程" in result
        assert "CAPABILITIES" in result
        assert result.index("# 标准操作流程") < result.index("CAPABILITIES")

    async def test_empty_sop_dir_skips_gracefully(self, tmp_path, monkeypatch):
        """SOP 目录为空时静默跳过，不产生错误或空 section。"""
        empty_sop_dir = tmp_path / "empty_sop"
        empty_sop_dir.mkdir()
        monkeypatch.setattr(pb, "_SOP_DIR", empty_sop_dir)

        with base_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")

        assert "# 标准操作流程" not in result

    async def test_nonexistent_sop_dir_skips_gracefully(self, tmp_path, monkeypatch):
        """SOP 目录不存在时静默跳过。"""
        monkeypatch.setattr(pb, "_SOP_DIR", tmp_path / "no_such_dir")

        with base_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")

        assert "# 标准操作流程" not in result

    async def test_sop_files_joined_with_separator(self, tmp_path, monkeypatch):
        """多个 SOP 文件用分隔符 --- 拼接。"""
        sop_dir = tmp_path / "sop"
        sop_dir.mkdir()
        (sop_dir / "01_first.md").write_text("SOP 第一条规程", encoding="utf-8")
        (sop_dir / "02_second.md").write_text("SOP 第二条规程", encoding="utf-8")
        monkeypatch.setattr(pb, "_SOP_DIR", sop_dir)

        with base_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")

        assert "SOP 第一条规程" in result
        assert "SOP 第二条规程" in result
        assert "---" in result

    async def test_sop_empty_files_skipped(self, tmp_path, monkeypatch):
        """SOP 目录中的空文件被跳过，不产生空 section。"""
        sop_dir = tmp_path / "sop"
        sop_dir.mkdir()
        (sop_dir / "empty.md").write_text("   \n  ", encoding="utf-8")
        monkeypatch.setattr(pb, "_SOP_DIR", sop_dir)

        with base_stack():
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            result = await brain._build_system_prompt("chat1")

        assert "# 标准操作流程" not in result
