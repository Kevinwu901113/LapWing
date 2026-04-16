"""tests/core/test_brain_system_prompt.py — Phase 2 brain system prompt 测试。"""

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_NONEXISTENT = Path("/nonexistent")


@pytest.fixture(autouse=True)
def reset_module_cache():
    for mod in list(sys.modules.keys()):
        if "brain" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod:
            del sys.modules[mod]


def _mock_load_prompt(name, **kwargs):
    if name == "lapwing_soul":
        return "SOUL"
    return ""


def base_brain_stack(**overrides):
    stack = ExitStack()
    load_fn = overrides.get("load_fn", _mock_load_prompt)
    stack.enter_context(patch("src.core.brain.load_prompt", side_effect=load_fn))
    stack.enter_context(patch("src.core.prompt_builder.load_prompt", side_effect=load_fn))
    stack.enter_context(patch("src.core.brain.LLMRouter"))
    stack.enter_context(patch("src.core.brain.ConversationMemory"))
    stack.enter_context(patch("src.core.brain.SOUL_PATH", overrides.get("soul", _NONEXISTENT / "soul.md")))
    return stack


class TestPhase0Fallback:
    """Phase 0 模式下直接返回极简 prompt。"""

    async def test_phase0_uses_system_prompt_directly(self):
        with base_brain_stack():
            with patch("config.settings.PHASE0_MODE", "A"):
                from src.core.brain import LapwingBrain
                brain = LapwingBrain(db_path=Path("test.db"))
                # Phase 0 使用 system_prompt property
                result = await brain._build_system_prompt("chat1")
                # Phase 0 调用 build_phase0_prompt，我们 mock 了相关文件不存在
                # 所以会走 fallback
                assert result is not None


class TestPromptBuilderIntegration:
    """PromptBuilder 注入后的行为。"""

    async def test_uses_prompt_builder_when_available(self, tmp_path):
        soul = tmp_path / "soul.md"
        soul.write_text("# Lapwing Soul", encoding="utf-8")
        constitution = tmp_path / "constitution.md"
        constitution.write_text("# Constitution", encoding="utf-8")

        with base_brain_stack():
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
                        "chat1", adapter="desktop"
                    )
                assert "# Lapwing Soul" in result
                assert "# Constitution" in result
                assert "## 当前状态" in result

    async def test_fallback_when_no_prompt_builder(self):
        with base_brain_stack():
            with patch("config.settings.PHASE0_MODE", ""):
                from src.core.brain import LapwingBrain
                brain = LapwingBrain(db_path=Path("test.db"))
                assert brain.prompt_builder is None
                result = await brain._build_system_prompt("chat1")
                # Falls back to self.system_prompt
                assert result is not None
