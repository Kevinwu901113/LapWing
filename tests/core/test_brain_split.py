"""brain.py think_conversational 的 [SPLIT] 多消息分发测试。"""

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
         patch("src.core.brain.ConversationMemory"):
        from src.core.brain import LapwingBrain
        brain = LapwingBrain(db_path=Path("test.db"))
    brain.memory.append = AsyncMock()
    brain.memory.append_to_session = AsyncMock()
    brain.memory.remove_last = AsyncMock()
    brain.fact_extractor = MagicMock()
    brain.fact_extractor.notify = MagicMock()
    brain.quality_checker = None
    return brain


def _make_ctx(reply_text: str):
    """Mock _ThinkCtx + _complete_chat that fires on_interim_text with reply_text."""
    from src.core.brain import _ThinkCtx
    ctx = _ThinkCtx(
        messages=[],
        effective_user_message="hi",
        approved_directory=None,
        early_reply=None,
        session_id=None,
    )

    async def fake_complete_chat(chat_id, messages, user_msg, **kwargs):
        on_interim = kwargs.get("on_interim_text")
        if on_interim:
            await on_interim(reply_text)
        return reply_text

    return ctx, fake_complete_chat


@pytest.mark.asyncio
class TestBrainSplit:
    async def test_no_split_marker_sends_single_message(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx("hello world")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch("src.core.brain.MESSAGE_SPLIT_ENABLED", True), \
             patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete), \
             patch("src.core.brain.asyncio.sleep", new_callable=AsyncMock):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert send_calls == ["hello world"]

    async def test_split_marker_sends_two_messages(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx("こんにちは [SPLIT] 元気ですか")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch("src.core.brain.MESSAGE_SPLIT_ENABLED", True), \
             patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete), \
             patch("src.core.brain.asyncio.sleep", new_callable=AsyncMock):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert send_calls == ["こんにちは", "元気ですか"]

    async def test_typing_fn_called_between_segments(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx("a [SPLIT] b [SPLIT] c")
        typing_calls = 0

        async def send_fn(text: str) -> None:
            pass

        async def typing_fn() -> None:
            nonlocal typing_calls
            typing_calls += 1

        with patch("src.core.brain.MESSAGE_SPLIT_ENABLED", True), \
             patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete), \
             patch("src.core.brain.asyncio.sleep", new_callable=AsyncMock):
            await brain.think_conversational(
                "chat1", "hi", send_fn=send_fn, typing_fn=typing_fn
            )

        # 3 segments → 2 inter-segment gaps → typing called twice
        assert typing_calls == 2

    async def test_sleep_called_between_segments(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx("hello [SPLIT] world")
        mock_sleep = AsyncMock()

        async def send_fn(text: str) -> None:
            pass

        with patch("src.core.brain.MESSAGE_SPLIT_ENABLED", True), \
             patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete), \
             patch("src.core.brain.asyncio.sleep", mock_sleep):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert mock_sleep.call_count == 1
        delay = mock_sleep.call_args[0][0]
        assert delay > 0

    async def test_split_disabled_sends_raw_text(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx("hello [SPLIT] world")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch("src.core.brain.MESSAGE_SPLIT_ENABLED", False), \
             patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete), \
             patch("src.core.brain.asyncio.sleep", new_callable=AsyncMock):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        # When disabled, raw text (with marker) is sent as one message
        assert send_calls == ["hello [SPLIT] world"]

    async def test_memory_has_no_split_markers(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx("first part [SPLIT] second part")
        memory_calls: list[str] = []
        brain.memory.append = AsyncMock(side_effect=lambda *a: memory_calls.append(a[2]))

        async def send_fn(text: str) -> None:
            pass

        with patch("src.core.brain.MESSAGE_SPLIT_ENABLED", True), \
             patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete), \
             patch("src.core.brain.asyncio.sleep", new_callable=AsyncMock):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert memory_calls, "memory.append should have been called"
        for stored in memory_calls:
            assert "[SPLIT]" not in stored
            assert "[split]" not in stored.lower()

    async def test_already_sent_check_prevents_double_send_with_split(self):
        """When on_interim_text already sent the split segments, final send is skipped."""
        brain = _make_brain()
        ctx, fake_complete = _make_ctx("hello [SPLIT] world")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch("src.core.brain.MESSAGE_SPLIT_ENABLED", True), \
             patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete), \
             patch("src.core.brain.asyncio.sleep", new_callable=AsyncMock):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        # Should not have sent the messages twice
        assert send_calls.count("hello") == 1
        assert send_calls.count("world") == 1
