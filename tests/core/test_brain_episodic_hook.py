"""brain._schedule_conversation_end only closes the attention session.

Lightweight tests: patch `asyncio.sleep` so the delayed task fires
immediately. Focus dormant now owns episodic extraction; session end no
longer defines a memory boundary.
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


class TestSessionEnd:
    async def test_session_end_does_not_call_episodic_extractor(self, brain):
        brain.inner_tick_scheduler = None
        brain.attention_manager = AsyncMock()
        extractor = AsyncMock()
        extractor.extract_from_chat = AsyncMock(return_value=True)
        brain._episodic_extractor = extractor

        with patch("src.core.brain.asyncio.sleep", new=AsyncMock()):
            brain._schedule_conversation_end("chat_abc")
            await brain._conversation_end_task

        extractor.extract_from_chat.assert_not_called()
        brain.attention_manager.end_session.assert_awaited_once()

    async def test_inner_tick_scheduler_notified(self, brain):
        brain.inner_tick_scheduler = MagicMock()
        brain.attention_manager = None

        with patch("src.core.brain.asyncio.sleep", new=AsyncMock()):
            brain._schedule_conversation_end("chat_xyz")
            await brain._conversation_end_task

        brain.inner_tick_scheduler.note_conversation_end.assert_called_once()

    async def test_no_schedule_when_nothing_wired(self, brain):
        brain.inner_tick_scheduler = None
        brain.attention_manager = None
        brain._episodic_extractor = None

        brain._schedule_conversation_end("chat_xyz")
        # Early-return: no task started.
        assert brain._conversation_end_task is None
