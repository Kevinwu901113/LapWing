"""MemoryConsolidationAction 测试。"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.heartbeat import SenseContext
from src.heartbeat.actions.consolidation import MemoryConsolidationAction


@pytest.fixture
def ctx():
    return SenseContext(
        beat_type="slow", now=datetime.now(timezone.utc),
        last_interaction=None, silence_hours=8.0,
        user_facts_summary="", recent_memory_summary="",
        chat_id="c1",
    )


@pytest.fixture
def mock_brain():
    b = MagicMock()
    b.memory = MagicMock()
    b.memory.get = AsyncMock(return_value=[
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好"},
    ])
    b.memory.set_user_fact = AsyncMock()
    b.router = MagicMock()
    b.router.complete = AsyncMock(return_value="用户今天问候了Lapwing。")
    b.fact_extractor = MagicMock()
    b.fact_extractor.force_extraction = AsyncMock()
    return b


class TestMemoryConsolidationAction:
    def test_beat_types_is_slow_only(self):
        a = MemoryConsolidationAction()
        assert a.beat_types == ["slow"]

    def test_name(self):
        assert MemoryConsolidationAction().name == "memory_consolidation"

    async def test_stores_summary_as_user_fact(self, ctx, mock_brain):
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.memory.set_user_fact.assert_called_once()
        key = mock_brain.memory.set_user_fact.call_args.args[1]
        assert key.startswith("memory_summary_")

    async def test_calls_force_extraction(self, ctx, mock_brain):
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.fact_extractor.force_extraction.assert_called_once_with("c1")

    async def test_uses_heartbeat_purpose(self, ctx, mock_brain):
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        assert mock_brain.router.complete.call_args.kwargs.get("purpose") == "heartbeat"

    async def test_skips_when_no_history(self, ctx, mock_brain):
        mock_brain.memory.get = AsyncMock(return_value=[])
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.router.complete.assert_not_called()
        mock_brain.memory.set_user_fact.assert_not_called()

    async def test_silent_on_llm_failure(self, ctx, mock_brain):
        mock_brain.router.complete = AsyncMock(side_effect=Exception("API error"))
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.memory.set_user_fact.assert_not_called()
