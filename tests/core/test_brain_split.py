"""brain.think_conversational 的 Step 5 契约测试。

Step 5 之前：模型直接返回的文本会通过 ``_send_with_split``（按 [SPLIT]
拆分）调用 send_fn 发给用户。

Step 5：tell_user 是模型唯一对外说话的工具。模型直接返回的文本（裸
文本）属于内心独白（inner_monologue），不会发给用户，只写 trajectory。
multi-message 回复通过多次 tell_user 调用实现，不再通过 [SPLIT] 标记。

旧的 [SPLIT] / fallback newline / typing 节奏测试在 Step 5 被
新契约取代，原文件归档进 git history。
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
    """模拟 _ThinkCtx + _complete_chat：模型只返回裸文本，没有调 tell_user。"""
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


def _make_ctx_for_tell_user(messages_to_send: list[str]):
    """模拟 _ThinkCtx + _complete_chat：模型通过 tell_user 调用发送一组消息。"""
    from src.core.brain import _ThinkCtx
    ctx = _ThinkCtx(
        messages=[],
        effective_user_message="hi",
        approved_directory=None,
        early_reply=None,
    )

    async def fake_complete_chat(chat_id, messages, user_msg, **kwargs):
        # 模拟 tell_user 工具被多次调用，append 到 buffer 并直接 send
        send_fn = kwargs.get("send_fn")
        buffer = kwargs.get("tell_user_buffer")
        for msg in messages_to_send:
            if send_fn is not None:
                await send_fn(msg)
            if buffer is not None:
                buffer.append(msg)
        return ""  # tell_user 走完后裸文本通常为空

    return ctx, fake_complete_chat


@pytest.mark.asyncio
class TestStep5InnerMonologueContract:
    """裸文本 → inner_monologue trajectory entry，不发给用户。"""

    async def test_bare_text_does_not_reach_send_fn(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("hello world")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        # Step 5 contract: 裸文本永远不发给用户
        assert send_calls == []

    async def test_bare_text_written_as_inner_monologue_trajectory(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("我在想要不要查一下")

        async def send_fn(text: str) -> None:
            pass

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        # trajectory 至少有一条 INNER_THOUGHT 写入
        assert brain.trajectory_store.append.call_count >= 1
        from src.core.trajectory_store import TrajectoryEntryType
        # 找到 INNER_THOUGHT 调用
        inner_calls = [
            c for c in brain.trajectory_store.append.call_args_list
            if c.args[0] == TrajectoryEntryType.INNER_THOUGHT
        ]
        assert len(inner_calls) >= 1
        # content 包含原文
        content = inner_calls[-1].args[3]
        assert "我在想要不要查一下" in content["text"]
        assert content["source"] == "llm_bare_text"

    async def test_bare_text_split_markers_not_split(self):
        """旧 [SPLIT] 行为已废弃：含 [SPLIT] 的裸文本只是普通 inner_monologue。"""
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("hello [SPLIT] world")
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert send_calls == []

    async def test_memory_assistant_empty_when_no_tell_user(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_bare_text("just thinking")

        async def send_fn(text: str) -> None:
            pass

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            result = await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert result == ""


@pytest.mark.asyncio
class TestStep5TellUserContract:
    """tell_user 工具调用 → 真实 send_fn 调用，buffer 累积，memory 记录。"""

    async def test_single_tell_user_reaches_send_fn(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_tell_user(["hello"])
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert send_calls == ["hello"]

    async def test_multiple_tell_user_calls_each_send_separately(self):
        """连发多条：每次 tell_user 调用独立发送一条。"""
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_tell_user(["第一条", "第二条", "第三条"])
        send_calls: list[str] = []

        async def send_fn(text: str) -> None:
            send_calls.append(text)

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert send_calls == ["第一条", "第二条", "第三条"]

    async def test_trajectory_records_joined_tell_user_text(self):
        brain = _make_brain()
        ctx, fake_complete = _make_ctx_for_tell_user(["一", "二"])
        traj_calls: list[tuple] = []
        brain.trajectory_store.append = AsyncMock(
            side_effect=lambda *a, **kw: traj_calls.append((a, kw))
        )

        async def send_fn(text: str) -> None:
            pass

        with patch.object(brain, "_prepare_think", AsyncMock(return_value=ctx)), \
             patch.object(brain, "_complete_chat", fake_complete):
            result = await brain.think_conversational("chat1", "hi", send_fn=send_fn)

        assert result == "一\n\n二"
        assistant_calls = [
            c for c in traj_calls
            if len(c[0]) >= 4 and c[0][3].get("text") == "一\n\n二"
        ]
        assert len(assistant_calls) == 1
