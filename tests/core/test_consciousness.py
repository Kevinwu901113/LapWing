"""ConsciousnessEngine 单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.consciousness import ConsciousnessEngine


def _make_engine(brain=None, send_fn=None):
    brain = brain or MagicMock()
    send_fn = send_fn or AsyncMock()
    return ConsciousnessEngine(
        brain=brain,
        send_fn=send_fn,
    )


class TestParseAndStripNext:
    def test_parses_minutes(self):
        engine = _make_engine()
        text, interval = engine._parse_and_strip_next("无事 [NEXT: 10m]")
        assert interval == 600
        assert text == "无事"

    def test_parses_hours(self):
        engine = _make_engine()
        text, interval = engine._parse_and_strip_next("做完了 [NEXT: 2h]")
        assert interval == 7200
        assert text == "做完了"

    def test_parses_seconds(self):
        engine = _make_engine()
        text, interval = engine._parse_and_strip_next("快速检查 [NEXT: 30s]")
        assert interval == 30
        assert text == "快速检查"

    def test_default_on_missing(self):
        engine = _make_engine()
        text, interval = engine._parse_and_strip_next("无事")
        assert interval is None
        assert text == "无事"

    def test_default_on_empty(self):
        engine = _make_engine()
        text, interval = engine._parse_and_strip_next("")
        assert interval is None
        assert text == ""

    def test_case_insensitive(self):
        engine = _make_engine()
        text, interval = engine._parse_and_strip_next("[NEXT: 5M]")
        assert interval == 300
        assert text == ""


class TestConversationState:
    def test_on_conversation_start_clears_event(self):
        engine = _make_engine()
        assert engine._conversation_event.is_set()
        engine.on_conversation_start()
        assert not engine._conversation_event.is_set()
        assert engine._in_conversation is True

    def test_on_conversation_end_sets_event(self):
        engine = _make_engine()
        engine.on_conversation_start()
        engine.on_conversation_end()
        assert engine._conversation_event.is_set()
        assert engine._in_conversation is False

    def test_on_conversation_start_cancels_thinking(self):
        engine = _make_engine()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        engine._thinking_task = mock_task
        engine.on_conversation_start()
        mock_task.cancel.assert_called_once()


class TestConsciousnessPrompt:
    @pytest.mark.asyncio
    async def test_prompt_contains_timestamp(self):
        engine = _make_engine()
        prompt = await engine._build_consciousness_prompt()
        assert "[内部意识 tick" in prompt
        assert "你可以做任何你觉得应该做的事" in prompt

    @pytest.mark.asyncio
    async def test_prompt_contains_rules(self):
        engine = _make_engine()
        prompt = await engine._build_consciousness_prompt()
        assert "[NEXT:" in prompt
        assert "自由时间" in prompt


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        engine = _make_engine()
        await engine.start()
        assert engine._running is True
        assert engine._task is not None
        await engine.stop()
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        engine = _make_engine()
        await engine.start()
        await engine.stop()
        assert engine._task.cancelled() or engine._task.done()
