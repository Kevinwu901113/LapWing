"""SenseLayer 和 HeartbeatEngine 决策层测试。"""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.heartbeat import SenseLayer, HeartbeatEngine, HeartbeatAction, SenseContext


class FakeFastAction(HeartbeatAction):
    name = "fake_fast"
    description = "test"
    beat_types = ["fast"]
    async def execute(self, ctx, brain, bot): pass


@pytest.fixture
def mock_memory():
    m = MagicMock()
    m.get_all_chat_ids = AsyncMock(return_value=["c1"])
    m.get_last_interaction = AsyncMock(return_value=None)
    m.get_user_facts = AsyncMock(return_value=[])
    m.get = AsyncMock(return_value=[])
    return m


@pytest.fixture
def mock_brain(mock_memory):
    b = MagicMock()
    b.memory = mock_memory
    b.router = MagicMock()
    b.router.complete = AsyncMock(return_value='{"actions": [], "reason": "静默"}')
    return b


class TestSenseLayer:
    async def test_builds_context_fast_beat(self, mock_memory):
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert ctx.chat_id == "c1"
        assert ctx.beat_type == "fast"
        assert ctx.recent_memory_summary == ""

    async def test_slow_beat_fills_recent_summary(self, mock_memory):
        mock_memory.get = AsyncMock(return_value=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
        ])
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "slow")
        assert "你好" in ctx.recent_memory_summary

    async def test_large_silence_when_no_interaction(self, mock_memory):
        mock_memory.get_last_interaction = AsyncMock(return_value=None)
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert ctx.silence_hours > 1000

    async def test_silence_calculated_from_last_interaction(self, mock_memory):
        from datetime import timedelta
        past = datetime.now(timezone.utc) - timedelta(hours=5)
        mock_memory.get_last_interaction = AsyncMock(return_value=past)
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert 4.9 < ctx.silence_hours < 5.1


class TestHeartbeatEngineDecision:
    def test_parse_valid_json(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision('{"actions": ["proactive_message"], "reason": "test"}')
        assert result == ["proactive_message"]

    def test_parse_empty_actions(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision('{"actions": [], "reason": "静默"}')
        assert result == []

    def test_parse_malformed_returns_empty(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision("这不是JSON")
        assert result == []

    def test_parse_handles_code_fence(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision('```json\n{"actions": ["x"], "reason": "r"}\n```')
        assert result == ["x"]

    async def test_run_beat_silent_when_no_actions(self, mock_brain):
        mock_brain.router.complete = AsyncMock(
            return_value='{"actions": [], "reason": "静默"}'
        )
        bot = MagicMock()
        engine = HeartbeatEngine(brain=mock_brain, bot=bot)
        engine.registry.register(FakeFastAction())
        await engine._run_beat("fast")
        await asyncio.gather(*engine._running_tasks, return_exceptions=True)
        bot.send_message.assert_not_called()
