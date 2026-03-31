"""tests/heartbeat/actions/test_auto_memory.py — AutoMemoryAction 测试。"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.heartbeat import SenseContext


def _make_ctx(silence_hours: float = 0.5) -> SenseContext:
    now = datetime.now(timezone.utc)
    last = now - timedelta(hours=silence_hours)
    return SenseContext(
        beat_type="fast",
        now=now,
        last_interaction=last,
        silence_hours=silence_hours,
        user_facts_summary="",
        recent_memory_summary="",
        chat_id="test_chat",
    )


def _make_brain(messages=None):
    brain = MagicMock()
    brain.memory.get = AsyncMock(return_value=messages or [{"role": "user", "content": f"msg {i}"} for i in range(6)])
    brain.auto_memory_extractor = MagicMock()
    brain.auto_memory_extractor.extract_from_messages = AsyncMock(return_value=[])
    return brain


class TestAutoMemoryAction:
    async def test_skips_when_user_active(self):
        from src.heartbeat.actions.auto_memory import AutoMemoryAction, _last_extraction
        _last_extraction.clear()
        action = AutoMemoryAction()
        brain = _make_brain()
        ctx = _make_ctx(silence_hours=0.1)  # 6 分钟，不足 15
        await action.execute(ctx, brain, AsyncMock())
        brain.auto_memory_extractor.extract_from_messages.assert_not_called()

    async def test_runs_when_idle_enough(self):
        from src.heartbeat.actions.auto_memory import AutoMemoryAction, _last_extraction
        _last_extraction.clear()
        action = AutoMemoryAction()
        brain = _make_brain()
        ctx = _make_ctx(silence_hours=0.5)  # 30 分钟
        await action.execute(ctx, brain, AsyncMock())
        brain.auto_memory_extractor.extract_from_messages.assert_called_once()

    async def test_skips_when_recently_extracted(self):
        from src.heartbeat.actions.auto_memory import AutoMemoryAction, _last_extraction
        _last_extraction.clear()
        _last_extraction["test_chat"] = datetime.now(timezone.utc) - timedelta(minutes=10)
        action = AutoMemoryAction()
        brain = _make_brain()
        ctx = _make_ctx(silence_hours=1.0)
        await action.execute(ctx, brain, AsyncMock())
        brain.auto_memory_extractor.extract_from_messages.assert_not_called()

    async def test_skips_short_conversation(self):
        from src.heartbeat.actions.auto_memory import AutoMemoryAction, _last_extraction
        _last_extraction.clear()
        action = AutoMemoryAction()
        brain = _make_brain(messages=[{"role": "user", "content": "hi"}])
        ctx = _make_ctx(silence_hours=1.0)
        await action.execute(ctx, brain, AsyncMock())
        brain.auto_memory_extractor.extract_from_messages.assert_not_called()

    async def test_skips_when_extractor_not_initialized(self):
        from src.heartbeat.actions.auto_memory import AutoMemoryAction, _last_extraction
        _last_extraction.clear()
        action = AutoMemoryAction()
        brain = _make_brain()
        brain.auto_memory_extractor = None  # 未初始化
        ctx = _make_ctx(silence_hours=1.0)
        # 不应抛出异常
        await action.execute(ctx, brain, AsyncMock())

    async def test_handles_memory_get_exception(self):
        from src.heartbeat.actions.auto_memory import AutoMemoryAction, _last_extraction
        _last_extraction.clear()
        action = AutoMemoryAction()
        brain = MagicMock()
        brain.memory.get = AsyncMock(side_effect=Exception("DB error"))
        brain.auto_memory_extractor = MagicMock()
        ctx = _make_ctx(silence_hours=1.0)
        # 不应抛出异常
        await action.execute(ctx, brain, AsyncMock())
        brain.auto_memory_extractor.extract_from_messages.assert_not_called()
