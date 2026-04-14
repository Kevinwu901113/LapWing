"""InterestProactiveAction 测试。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.heartbeat import SenseContext
from src.heartbeat.actions.interest_proactive import InterestProactiveAction


def make_ctx(*, hour: int = 12, silence_hours: float = 3.0) -> SenseContext:
    return SenseContext(
        beat_type="fast",
        now=datetime(2026, 3, 23, hour, 0, tzinfo=timezone.utc),
        last_interaction=None,
        silence_hours=silence_hours,
        user_facts_summary="- 偏好: 书卷气",
        recent_memory_summary="",
        chat_id="c1",
        now_taipei_hour=(hour + 8) % 24,
    )


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.event_bus = None
    brain.memory = MagicMock()
    brain.memory.get_top_interests = AsyncMock(return_value=[{"topic": "Python", "weight": 3.0}])
    brain.memory.add_discovery = AsyncMock()
    brain.memory.append = AsyncMock()
    brain.memory.decay_interests = AsyncMock()
    brain.router = MagicMock()
    brain.router.query_lightweight = AsyncMock(return_value="PASS")
    brain.compose_proactive = AsyncMock(return_value="刚看到一篇关于 Python 的文章，感觉你会喜欢。")
    return brain


@pytest.fixture
def mock_send_fn():
    return AsyncMock()


# 公共 patch 上下文：让 random 不跳过、filter 通过
def _base_patches():
    return [
        patch("src.heartbeat.actions.interest_proactive.load_prompt",
              return_value="{topic}\n{search_results}\n{user_facts_summary}"),
        patch("src.heartbeat.actions.interest_proactive.filter_proactive_message",
              AsyncMock(return_value=(True, "PASS"))),
        patch("src.heartbeat.actions.interest_proactive.random.random",
              return_value=0.5),  # >= 0.4，不跳过
    ]


@pytest.mark.asyncio
class TestInterestProactiveAction:
    async def test_skips_when_no_interests(self, mock_brain, mock_send_fn):
        mock_brain.memory.get_top_interests = AsyncMock(return_value=[])
        with patch("src.heartbeat.actions.interest_proactive.load_prompt",
                   return_value="{topic} {search_results} {user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.random.random", return_value=0.5):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_skips_when_compose_returns_none(self, mock_brain, mock_send_fn):
        mock_brain.compose_proactive = AsyncMock(return_value=None)
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_sends_message_with_topic(self, mock_brain, mock_send_fn):
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        mock_brain.compose_proactive.assert_awaited_once()
        call_kwargs = mock_brain.compose_proactive.call_args.kwargs
        assert call_kwargs["tools"] == ["web_search", "image_search"]
        assert call_kwargs["chat_id"] == "c1"
        mock_send_fn.assert_awaited_once()

    async def test_saves_discovery(self, mock_brain, mock_send_fn):
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        call_kwargs = mock_brain.memory.add_discovery.call_args.kwargs
        assert call_kwargs["source"] == "interest_search"

    async def test_appends_to_memory(self, mock_brain, mock_send_fn):
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        # 记忆写入时附加了来源标注
        call_args = mock_brain.memory.append.call_args
        assert call_args.args[0] == "c1"
        assert call_args.args[1] == "assistant"
        assert call_args.args[2].startswith("刚看到一篇关于 Python 的文章，感觉你会喜欢。")
        assert "[source:" in call_args.args[2]

    async def test_decays_interests_after_share(self, mock_brain, mock_send_fn):
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        mock_brain.memory.decay_interests.assert_awaited_once_with("c1", factor=0.9)

    async def test_skips_during_quiet_hours(self, mock_brain, mock_send_fn):
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}"):
            await InterestProactiveAction().execute(make_ctx(hour=23), mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_skips_when_silence_too_short(self, mock_brain, mock_send_fn):
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}"):
            await InterestProactiveAction().execute(make_ctx(silence_hours=1.0), mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_skips_randomly(self, mock_brain, mock_send_fn):
        with patch("src.heartbeat.actions.interest_proactive.load_prompt",
                   return_value="{topic}\n{search_results}\n{user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.random.random", return_value=0.1):  # < 0.4
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()

    async def test_uses_compose_proactive_with_correct_purpose(self, mock_brain, mock_send_fn):
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        call_kwargs = mock_brain.compose_proactive.call_args.kwargs
        assert call_kwargs["purpose"] == "兴趣分享"

    async def test_forwards_compose_proactive_output(self, mock_brain, mock_send_fn):
        mock_brain.compose_proactive = AsyncMock(return_value="这条给你")
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)

        mock_send_fn.assert_awaited_once_with("这条给你")
        call_args = mock_brain.memory.append.call_args
        assert call_args.args[2].startswith("这条给你")
        assert "[source:" in call_args.args[2]

    async def test_publishes_desktop_event(self, mock_brain, mock_send_fn):
        mock_brain.event_bus = MagicMock()
        mock_brain.event_bus.publish = AsyncMock()
        patches = _base_patches()
        with patches[0], patches[1], patches[2]:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)

        mock_brain.event_bus.publish.assert_awaited_once_with(
            "interest_proactive",
            {
                "chat_id": "c1",
                "text": "刚看到一篇关于 Python 的文章，感觉你会喜欢。",
                "topic": "Python",
            },
        )

    async def test_discards_when_filter_fails(self, mock_brain, mock_send_fn):
        with patch("src.heartbeat.actions.interest_proactive.load_prompt",
                   return_value="{topic}\n{search_results}\n{user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.filter_proactive_message",
                   AsyncMock(return_value=(False, "FAIL 信息密度过高"))), \
             patch("src.heartbeat.actions.interest_proactive.random.random", return_value=0.5):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_send_fn)
        mock_send_fn.assert_not_called()
