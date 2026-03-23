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
    m.get_top_interests = AsyncMock(return_value=[])
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
        assert ctx.top_interests_summary == "（暂无明显兴趣）"

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

    async def test_builds_top_interests_summary(self, mock_memory):
        mock_memory.get_top_interests = AsyncMock(return_value=[
            {"topic": "Python 编程", "weight": 8.5, "last_seen": "2026-03-23"},
            {"topic": "机器学习", "weight": 4.2, "last_seen": "2026-03-22"},
        ])
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert ctx.top_interests_summary == "- Python 编程（8.5）\n- 机器学习（4.2）"


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

    async def test_decide_does_not_corrupt_braces_in_user_facts(self, mock_brain):
        """user_facts_summary 中的 { } 应原样传入 NIM，不被 _escape_braces 双写。"""
        from datetime import timezone
        mock_brain.memory.get_user_facts = AsyncMock(return_value=[
            {"fact_key": "test", "fact_value": "{not_a_placeholder}"}
        ])
        engine = HeartbeatEngine(brain=mock_brain, bot=MagicMock())
        engine.registry.register(FakeFastAction())
        ctx = SenseContext(
            beat_type="fast",
            now=datetime.now(timezone.utc),
            last_interaction=None,
            silence_hours=5.0,
            user_facts_summary="- test: {not_a_placeholder}",
            recent_memory_summary="",
            chat_id="c1",
            top_interests_summary="（暂无明显兴趣）",
        )
        await engine._decide(ctx)
        call_args = mock_brain.router.complete.call_args
        prompt_content = call_args.args[0][0]["content"]  # system message content
        assert "{not_a_placeholder}" in prompt_content
        assert "{{not_a_placeholder}}" not in prompt_content

    async def test_decide_includes_top_interests_in_prompt(self, mock_brain):
        engine = HeartbeatEngine(brain=mock_brain, bot=MagicMock())
        engine.registry.register(FakeFastAction())
        ctx = SenseContext(
            beat_type="fast",
            now=datetime.now(timezone.utc),
            last_interaction=None,
            silence_hours=5.0,
            user_facts_summary="（暂无已知信息）",
            recent_memory_summary="",
            chat_id="c1",
            top_interests_summary="- Python 编程（8.5）",
        )
        await engine._decide(ctx)
        prompt_content = mock_brain.router.complete.call_args.args[0][0]["content"]
        assert "用户当前兴趣" in prompt_content
        assert "- Python 编程（8.5）" in prompt_content
