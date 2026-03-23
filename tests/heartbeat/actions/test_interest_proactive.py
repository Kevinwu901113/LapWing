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
    )


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.memory = MagicMock()
    brain.memory.get_top_interests = AsyncMock(return_value=[{"topic": "Python", "weight": 3.0}])
    brain.memory.add_discovery = AsyncMock()
    brain.memory.append = AsyncMock()
    brain.memory.decay_interests = AsyncMock()
    brain.router = MagicMock()
    brain.router.complete = AsyncMock(return_value="刚看到一篇关于 Python 的文章，感觉你会喜欢。")
    return brain


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.asyncio
class TestInterestProactiveAction:
    async def test_skips_when_no_interests(self, mock_brain, mock_bot):
        mock_brain.memory.get_top_interests = AsyncMock(return_value=[])
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic} {search_results} {user_facts_summary}"):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_bot)
        mock_bot.send_message.assert_not_called()

    async def test_skips_when_search_empty(self, mock_brain, mock_bot):
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic} {search_results} {user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.web_search.search", AsyncMock(return_value=[])):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_bot)
        mock_bot.send_message.assert_not_called()

    async def test_sends_message_with_topic(self, mock_brain, mock_bot):
        results = [{"title": "Python 文章", "url": "https://example.com/python", "snippet": "最新趋势"}]
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}\n{search_results}\n{user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.web_search.search", AsyncMock(return_value=results)) as mock_search:
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_bot)
        mock_search.assert_awaited_once_with("Python", max_results=3)
        mock_bot.send_message.assert_awaited_once()

    async def test_saves_discovery(self, mock_brain, mock_bot):
        results = [{"title": "Python 文章", "url": "https://example.com/python", "snippet": "最新趋势"}]
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}\n{search_results}\n{user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.web_search.search", AsyncMock(return_value=results)):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_bot)
        call_kwargs = mock_brain.memory.add_discovery.call_args.kwargs
        assert call_kwargs["source"] == "interest_search"

    async def test_appends_to_memory(self, mock_brain, mock_bot):
        results = [{"title": "Python 文章", "url": "https://example.com/python", "snippet": "最新趋势"}]
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}\n{search_results}\n{user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.web_search.search", AsyncMock(return_value=results)):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_bot)
        mock_brain.memory.append.assert_awaited_once_with(
            "c1", "assistant", "刚看到一篇关于 Python 的文章，感觉你会喜欢。"
        )

    async def test_decays_interests_after_share(self, mock_brain, mock_bot):
        results = [{"title": "Python 文章", "url": "https://example.com/python", "snippet": "最新趋势"}]
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}\n{search_results}\n{user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.web_search.search", AsyncMock(return_value=results)):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_bot)
        mock_brain.memory.decay_interests.assert_awaited_once_with("c1", factor=0.9)

    async def test_skips_during_quiet_hours(self, mock_brain, mock_bot):
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}"):
            await InterestProactiveAction().execute(make_ctx(hour=23), mock_brain, mock_bot)
        mock_bot.send_message.assert_not_called()

    async def test_skips_when_silence_too_short(self, mock_brain, mock_bot):
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}"):
            await InterestProactiveAction().execute(make_ctx(silence_hours=1.0), mock_brain, mock_bot)
        mock_bot.send_message.assert_not_called()

    async def test_uses_heartbeat_purpose(self, mock_brain, mock_bot):
        results = [{"title": "Python 文章", "url": "https://example.com/python", "snippet": "最新趋势"}]
        with patch("src.heartbeat.actions.interest_proactive.load_prompt", return_value="{topic}\n{search_results}\n{user_facts_summary}"), \
             patch("src.heartbeat.actions.interest_proactive.web_search.search", AsyncMock(return_value=results)):
            await InterestProactiveAction().execute(make_ctx(), mock_brain, mock_bot)
        assert mock_brain.router.complete.call_args.kwargs["purpose"] == "heartbeat"
