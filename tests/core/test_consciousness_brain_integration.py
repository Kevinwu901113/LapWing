"""Brain ↔ ConsciousnessEngine 集成测试。"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_module_cache():
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]


def _make_brain():
    with patch("src.core.brain.load_prompt", return_value="prompt"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.ConversationMemory") as MockMemory:
        mock_mem_instance = MockMemory.return_value
        mock_mem_instance.append = AsyncMock()
        mock_mem_instance.append_to_session = AsyncMock()
        mock_mem_instance.remove_last = AsyncMock()
        from src.core.brain import LapwingBrain
        brain = LapwingBrain(db_path=Path("test.db"))
    brain.fact_extractor = MagicMock()
    brain.fact_extractor.notify = MagicMock()
    return brain


class TestBrainConsciousnessAttr:
    def test_consciousness_engine_attr_defaults_none(self):
        brain = _make_brain()
        assert brain.consciousness_engine is None

    def test_conversation_end_task_attr_defaults_none(self):
        brain = _make_brain()
        assert brain._conversation_end_task is None

    def test_consciousness_engine_can_be_set(self):
        brain = _make_brain()
        mock_engine = MagicMock()
        brain.consciousness_engine = mock_engine
        assert brain.consciousness_engine is mock_engine


class TestConversationStateNotification:
    @pytest.mark.asyncio
    async def test_think_conversational_notifies_start(self):
        brain = _make_brain()
        mock_engine = MagicMock()
        brain.consciousness_engine = mock_engine

        from src.core.brain import _ThinkCtx
        ctx = _ThinkCtx(
            messages=[], effective_user_message="hi",
            approved_directory=None, early_reply="hello",
            session_id=None,
        )
        brain._prepare_think = AsyncMock(return_value=ctx)

        await brain.think_conversational("test", "hi", AsyncMock())
        mock_engine.on_conversation_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_think_conversational_schedules_end(self):
        brain = _make_brain()
        mock_engine = MagicMock()
        brain.consciousness_engine = mock_engine

        from src.core.brain import _ThinkCtx
        ctx = _ThinkCtx(
            messages=[], effective_user_message="hi",
            approved_directory=None, early_reply="hello",
            session_id=None,
        )
        brain._prepare_think = AsyncMock(return_value=ctx)

        await brain.think_conversational("test", "hi", AsyncMock())
        assert brain._conversation_end_task is not None

    @pytest.mark.asyncio
    async def test_no_crash_without_consciousness_engine(self):
        brain = _make_brain()
        assert brain.consciousness_engine is None

        from src.core.brain import _ThinkCtx
        ctx = _ThinkCtx(
            messages=[], effective_user_message="hi",
            approved_directory=None, early_reply="hello",
            session_id=None,
        )
        brain._prepare_think = AsyncMock(return_value=ctx)

        result = await brain.think_conversational("test", "hi", AsyncMock())
        assert result == "hello"
