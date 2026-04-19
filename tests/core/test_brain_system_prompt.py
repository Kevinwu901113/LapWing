"""brain._render_messages — v2.0 Step 3 replaces _build_system_prompt.

These tests exercise the integration point between LapwingBrain and
StateViewBuilder / StateSerializer. They don't repeat the per-layer
rendering tests that live in ``tests/core/test_state_serializer.py``;
they only pin down that brain wires the right stores into the builder
and returns the fully assembled messages list.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

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


def _stack():
    stack = ExitStack()
    stack.enter_context(patch("src.core.brain.load_prompt", return_value="SOUL_FALLBACK"))
    stack.enter_context(patch("src.core.brain.LLMRouter"))
    stack.enter_context(patch("src.core.brain.ConversationMemory"))
    stack.enter_context(patch("src.core.brain.SOUL_PATH", _NONEXISTENT / "soul.md"))
    return stack


class TestRenderMessages:
    """v2.0 Step 3: brain._render_messages takes recent_messages and
    returns the full [system, *trajectory_with_voice] list."""

    async def test_assembles_full_list(self, tmp_path):
        soul = tmp_path / "soul.md"
        soul.write_text("I AM LAPWING", encoding="utf-8")
        constitution = tmp_path / "constitution.md"
        constitution.write_text("CONSTITUTION", encoding="utf-8")

        with _stack(), patch("config.settings.PHASE0_MODE", ""):
            from src.core.brain import LapwingBrain
            from src.core.state_view_builder import StateViewBuilder

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.state_view_builder = StateViewBuilder(
                soul_path=soul,
                constitution_path=constitution,
                voice_prompt_name="does_not_exist",
            )
            messages = await brain._render_messages(
                "chat1",
                [{"role": "user", "content": "hi"}],
                adapter="desktop",
            )
            assert messages[0]["role"] == "system"
            assert "I AM LAPWING" in messages[0]["content"]
            assert "CONSTITUTION" in messages[0]["content"]
            # 1 recent + total=2 < 4 → voice folded into system, no
            # injected user-role note; messages = [system, user]
            assert len(messages) == 2
            assert messages[1] == {"role": "user", "content": "hi"}

    async def test_voice_injected_for_long_convo(self, tmp_path):
        soul = tmp_path / "soul.md"
        soul.write_text("SOUL", encoding="utf-8")

        with _stack(), patch("config.settings.PHASE0_MODE", ""):
            from src.core.brain import LapwingBrain
            from src.core.state_view_builder import StateViewBuilder

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.state_view_builder = StateViewBuilder(
                soul_path=soul,
                constitution_path=tmp_path / "no_const",
                voice_prompt_name="does_not_exist",  # empty voice → no inject
            )
            # Even with 8 turns, empty voice means no note gets inserted.
            recent = [{"role": "user", "content": f"t{i}"} for i in range(8)]
            out = await brain._render_messages("c", recent, adapter="desktop")
            assert all("[System Note]" not in m.get("content", "") for m in out)
            # length = system + 8 recent
            assert len(out) == 9

    async def test_phase0_skips_state_serializer(self, tmp_path):
        with _stack(), patch("config.settings.PHASE0_MODE", "A"):
            from src.core.brain import LapwingBrain

            brain = LapwingBrain(db_path=Path("test.db"))
            out = await brain._render_messages(
                "c", [{"role": "user", "content": "x"}], adapter="desktop"
            )
            assert out[0]["role"] == "system"
            # Phase 0 returns raw soul fallback, no runtime-state block
            assert "## 当前状态" not in out[0]["content"]

    async def test_adapter_drives_channel_description(self, tmp_path):
        with _stack(), patch("config.settings.PHASE0_MODE", ""):
            from src.core.brain import LapwingBrain
            from src.core.state_view_builder import StateViewBuilder

            brain = LapwingBrain(db_path=Path("test.db"))
            brain.state_view_builder = StateViewBuilder(
                soul_path=tmp_path / "_",
                constitution_path=tmp_path / "_",
                voice_prompt_name="does_not_exist",
            )
            out = await brain._render_messages(
                "c1", [{"role": "user", "content": "yo"}],
                adapter="qq",
            )
            assert "QQ 私聊" in out[0]["content"]
