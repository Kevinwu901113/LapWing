"""brain.py system prompt 组装相关测试（Phase 2 版）。

Phase 2 中 _build_system_prompt 委托给 class-based PromptBuilder（4 层），
不再走旧的 module-level build_system_prompt 函数。
"""

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_NONEXISTENT = Path("/nonexistent")


@pytest.fixture(autouse=True)
def reset_module_cache():
    """每个测试前后清除 brain 的模块缓存，确保测试隔离。"""
    for mod in list(sys.modules.keys()):
        if "brain" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod:
            del sys.modules[mod]


def _mock_load_prompt(name, **kwargs):
    if name == "lapwing_soul":
        return "基础人格 prompt"
    return ""


def make_brain_stack():
    """创建隔离的 LapwingBrain 实例的 patch stack。"""
    stack = ExitStack()
    stack.enter_context(patch("src.core.brain.load_prompt", side_effect=_mock_load_prompt))
    stack.enter_context(patch("src.core.prompt_builder.load_prompt", side_effect=_mock_load_prompt))
    stack.enter_context(patch("src.core.brain.LLMRouter"))
    stack.enter_context(patch("src.core.brain.ConversationMemory"))
    stack.enter_context(patch("src.core.brain.SOUL_PATH", _NONEXISTENT / "soul.md"))
    return stack


class TestBuildSystemPrompt:
    async def test_returns_prompt_with_soul_content(self, tmp_path):
        """PromptBuilder 注入后，soul.md 内容出现在结果中。"""
        soul = tmp_path / "soul.md"
        soul.write_text("基础人格 prompt", encoding="utf-8")
        constitution = tmp_path / "constitution.md"
        constitution.write_text("# 宪法", encoding="utf-8")

        with make_brain_stack():
            with patch("config.settings.PHASE0_MODE", ""):
                from src.core.brain import LapwingBrain
                from src.core.prompt_builder import PromptBuilder
                brain = LapwingBrain(db_path=Path("test.db"))
                brain.prompt_builder = PromptBuilder(
                    soul_path=soul,
                    constitution_path=constitution,
                )
                with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
                    result = await brain._build_system_prompt("chat1")
                assert "基础人格 prompt" in result

    async def test_fallback_without_prompt_builder(self):
        """没有 PromptBuilder 时 fallback 到 system_prompt property。"""
        with make_brain_stack():
            with patch("config.settings.PHASE0_MODE", ""):
                from src.core.brain import LapwingBrain
                brain = LapwingBrain(db_path=Path("test.db"))
                result = await brain._build_system_prompt("chat1")
                # 走 fallback，返回 system_prompt
                assert result is not None

    async def test_adapter_and_channel_passed_through(self, tmp_path):
        """adapter 参数正确传递到 PromptBuilder。"""
        soul = tmp_path / "soul.md"
        soul.write_text("Soul", encoding="utf-8")
        constitution = tmp_path / "constitution.md"
        constitution.write_text("Constitution", encoding="utf-8")

        with make_brain_stack():
            with patch("config.settings.PHASE0_MODE", ""):
                from src.core.brain import LapwingBrain
                from src.core.prompt_builder import PromptBuilder
                brain = LapwingBrain(db_path=Path("test.db"))
                brain.prompt_builder = PromptBuilder(
                    soul_path=soul,
                    constitution_path=constitution,
                )
                with patch("src.core.prompt_builder._get_period_name", return_value="下午"):
                    result = await brain._build_system_prompt(
                        "chat1", adapter="qq"
                    )
                assert "QQ 私聊" in result
