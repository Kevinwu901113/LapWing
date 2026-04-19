"""brain._schedule_conversation_end triggers episodic extraction (Step 7 M3).

Lightweight tests: patch `asyncio.sleep` so the delayed task fires
immediately, then verify the extractor's ``extract_from_chat`` gets
called with the right chat_id.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def brain(tmp_path):
    # Patch only what the brain constructor pulls in — keep the
    # _schedule_conversation_end method intact.
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.build_default_tool_registry"), \
         patch("src.core.brain.ConversationMemory"), \
         patch("src.core.brain.TaskRuntime"):
        from src.core.brain import LapwingBrain
        b = LapwingBrain(db_path=tmp_path / "x.db")
    return b


async def _run_scheduled(brain):
    """Drive the scheduled task to completion without the real 300s delay."""
    # Sleep is the only await in the inner coroutine; patch it on the
    # asyncio module used by brain.
    with patch("src.core.brain.asyncio.sleep", new=AsyncMock()):
        # Re-issue schedule so the patched sleep is seen.
        return await asyncio.gather(brain._conversation_end_task)


class TestExtractorTrigger:
    async def test_extractor_called_with_chat_id(self, brain):
        brain.inner_tick_scheduler = None
        brain.attention_manager = None
        extractor = MagicMock()
        extractor.extract_from_chat = AsyncMock(return_value=True)
        brain._episodic_extractor = extractor

        with patch("src.core.brain.asyncio.sleep", new=AsyncMock()):
            brain._schedule_conversation_end("chat_abc")
            await brain._conversation_end_task

        extractor.extract_from_chat.assert_awaited_once_with("chat_abc")

    async def test_extractor_not_called_when_chat_id_missing(self, brain):
        brain.inner_tick_scheduler = None
        brain.attention_manager = None
        extractor = MagicMock()
        extractor.extract_from_chat = AsyncMock(return_value=True)
        brain._episodic_extractor = extractor

        with patch("src.core.brain.asyncio.sleep", new=AsyncMock()):
            brain._schedule_conversation_end(None)
            await brain._conversation_end_task

        extractor.extract_from_chat.assert_not_called()

    async def test_extractor_failure_does_not_crash_end_task(self, brain):
        brain.inner_tick_scheduler = None
        brain.attention_manager = None
        extractor = MagicMock()
        extractor.extract_from_chat = AsyncMock(side_effect=RuntimeError("x"))
        brain._episodic_extractor = extractor

        with patch("src.core.brain.asyncio.sleep", new=AsyncMock()):
            brain._schedule_conversation_end("chat_xyz")
            # Must not raise.
            await brain._conversation_end_task

    async def test_no_schedule_when_nothing_wired(self, brain):
        brain.inner_tick_scheduler = None
        brain.attention_manager = None
        brain._episodic_extractor = None

        brain._schedule_conversation_end("chat_xyz")
        # Early-return: no task started.
        assert brain._conversation_end_task is None
