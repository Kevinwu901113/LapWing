"""SenseLayer / ProactiveRuntime / HeartbeatEngine 测试。"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.heartbeat import (
    ActionRegistry,
    HeartbeatAction,
    HeartbeatEngine,
    ProactiveRuntime,
    SenseContext,
    SenseLayer,
)


class FakeFastAction(HeartbeatAction):
    name = "fake_fast"
    description = "test fast action"
    beat_types = ["fast"]

    async def execute(self, ctx, brain, send_fn):
        return None


class FakeMinuteAlwaysAction(HeartbeatAction):
    name = "fake_minute"
    description = "test minute always action"
    beat_types = ["minute"]
    selection_mode = "always"

    async def execute(self, ctx, brain, send_fn):
        return None


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
        mock_memory.get = AsyncMock(
            return_value=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好"},
            ]
        )
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
        mock_memory.get_top_interests = AsyncMock(
            return_value=[
                {"topic": "Python 编程", "weight": 8.5, "last_seen": "2026-03-23"},
                {"topic": "机器学习", "weight": 4.2, "last_seen": "2026-03-22"},
            ]
        )
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert ctx.top_interests_summary == "- Python 编程（8.5）\n- 机器学习（4.2）"


class TestProactiveRuntime:
    @pytest.fixture
    def runtime(self, mock_brain):
        registry = ActionRegistry()
        sense = SenseLayer(mock_brain.memory)
        return ProactiveRuntime(
            brain=mock_brain,
            send_fn=AsyncMock(),
            registry=registry,
            sense=sense,
        )

    def test_parse_valid_json(self, runtime):
        result = runtime._parse_decision('{"actions": ["proactive_message"], "reason": "test"}')
        assert result == ["proactive_message"]

    def test_parse_empty_actions(self, runtime):
        result = runtime._parse_decision('{"actions": [], "reason": "静默"}')
        assert result == []

    def test_parse_malformed_returns_empty(self, runtime):
        result = runtime._parse_decision("这不是JSON")
        assert result == []

    def test_parse_handles_code_fence(self, runtime):
        result = runtime._parse_decision('```json\n{"actions": ["x"], "reason": "r"}\n```')
        assert result == ["x"]

    async def test_decide_does_not_corrupt_braces_in_user_facts(self, runtime, mock_brain):
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
        await runtime._decide(ctx, [FakeFastAction()])
        prompt_content = mock_brain.router.complete.call_args.args[0][0]["content"]
        assert "{not_a_placeholder}" in prompt_content
        assert "{{not_a_placeholder}}" not in prompt_content

    async def test_decide_includes_top_interests_in_prompt(self, runtime, mock_brain):
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
        await runtime._decide(ctx, [FakeFastAction()])
        prompt_content = mock_brain.router.complete.call_args.args[0][0]["content"]
        assert "用户当前兴趣" in prompt_content
        assert "- Python 编程（8.5）" in prompt_content

    async def test_minute_process_does_not_call_llm_and_executes_always(self, runtime, mock_brain):
        action = FakeMinuteAlwaysAction()
        action.execute = AsyncMock()
        runtime._registry.register(action)

        await runtime.process(chat_id="c1", beat_type="minute")

        action.execute.assert_awaited_once()
        mock_brain.router.complete.assert_not_called()


class TestHeartbeatEngine:
    async def test_run_tick_silent_when_no_actions(self, mock_brain):
        mock_brain.router.complete = AsyncMock(return_value='{"actions": [], "reason": "静默"}')
        send_fn = AsyncMock()
        engine = HeartbeatEngine(brain=mock_brain, send_fn=send_fn)
        engine.registry.register(FakeFastAction())
        await engine._run_tick("fast")
        await asyncio.gather(*engine._running_tasks, return_exceptions=True)
        send_fn.assert_not_called()
