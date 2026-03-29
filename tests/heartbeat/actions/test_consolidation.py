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
    b.vector_store = MagicMock()
    b.vector_store.upsert = AsyncMock()
    return b


class TestMemoryConsolidationAction:
    def test_beat_types_is_slow_only(self):
        a = MemoryConsolidationAction()
        assert a.beat_types == ["slow"]

    def test_name(self):
        assert MemoryConsolidationAction().name == "memory_consolidation"

    async def test_calls_force_extraction(self, ctx, mock_brain):
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.fact_extractor.force_extraction.assert_called_once_with("c1")

    async def test_does_not_generate_llm_summary(self, ctx, mock_brain):
        """摘要生成已移交给 Compactor，这里不再调用 LLM。"""
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.router.complete.assert_not_called()

    async def test_does_not_write_memory_summary_fact(self, ctx, mock_brain):
        """memory_summary_* fact 由 Compactor 管理，不再写入 SQLite。"""
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.memory.set_user_fact.assert_not_called()

    async def test_skips_when_no_history(self, ctx, mock_brain):
        mock_brain.memory.get = AsyncMock(return_value=[])
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.router.complete.assert_not_called()
        mock_brain.fact_extractor.force_extraction.assert_not_called()

    async def test_silent_on_force_extraction_failure(self, ctx, mock_brain):
        mock_brain.fact_extractor.force_extraction = AsyncMock(side_effect=Exception("DB error"))
        # Should not raise
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
