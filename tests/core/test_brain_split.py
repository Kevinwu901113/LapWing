"""brain.think_conversational 的直接输出契约测试。

直接输出模式：模型裸文本 = 用户可见消息。工具调用是内部操作。
send_message 工具仅用于主动消息场景（意识 tick / 定时提醒等无对话上下文时）。
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_module_cache():
    import config.settings as _settings
    _orig_budget = _settings.TASK_NO_ACTION_BUDGET
    _settings.TASK_NO_ACTION_BUDGET = 0
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod or "task_runtime" in mod:
            del sys.modules[mod]
    yield
    _settings.TASK_NO_ACTION_BUDGET = _orig_budget
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod or "task_runtime" in mod:
            del sys.modules[mod]


def _make_brain():
    with patch("src.core.brain.load_prompt", return_value="prompt"), \
         patch("src.core.brain.LLMRouter"):
        from src.core.brain import LapwingBrain
        brain = LapwingBrain(db_path=Path("test.db"))
    brain.fact_extractor = MagicMock()
    brain.fact_extractor.notify = MagicMock()
    brain.trajectory_store = MagicMock()
    brain.trajectory_store.append = AsyncMock(return_value=1)
    return brain


def _make_ctx_for_bare_text(reply_text: str):
    """模拟 _ThinkCtx + _complete_chat：模型只返回裸文本。"""
    from src.core.brain import _ThinkCtx
    ctx = _ThinkCtx(
        messages=[],
        effective_user_message="hi",
        approved_directory=None,
        early_reply=None,
    )

    async def fake_complete_chat(chat_id, messages, user_msg, **kwargs):
        on_interim = kwargs.get("on_interim_text")
        if on_interim:
            await on_interim(reply_text)
        return reply_text

    return ctx, fake_complete_chat


@pytest.mark.asyncio
class TestDirectOutputContract:
    """裸文本 → 直接发送给用户。"""

    async def test_bare_text_reaches_send_fn(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("hello world")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        # 直接输出：裸文本发送给用户
        assert "hello world" in send_calls

    async def test_bare_text_split_by_paragraphs(self):
        """多段落文本按空行分割成多条消息。"""
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("第一条\n\n第二条\n\n第三条")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert "第一条" in send_calls
        assert "第二条" in send_calls
        assert "第三条" in send_calls

    async def test_memory_records_spoken_text(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("spoken text")

        async def send_fn(text: str) -> None:
            pass

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            result = await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert "spoken text" in result

    async def test_think_tags_stripped_before_sending(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("<think>internal</think>visible text")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        for call in send_calls:
            assert "internal" not in call
        assert any("visible text" in c for c in send_calls)

    async def test_empty_text_not_sent(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            result = await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert send_calls == []
        assert result == ""
