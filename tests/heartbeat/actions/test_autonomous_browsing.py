"""AutonomousBrowsingAction 测试。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.heartbeat import SenseContext
from src.heartbeat.actions.autonomous_browsing import AutonomousBrowsingAction
from src.tools.web_fetcher import FetchResult


def make_ctx(now: datetime | None = None) -> SenseContext:
    return SenseContext(
        beat_type="fast",
        now=now or datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc),
        last_interaction=None,
        silence_hours=4.0,
        user_facts_summary="- 偏好: 技术深度内容",
        recent_memory_summary="",
        chat_id="c1",
        top_interests_summary="- Python（3.0）",
    )


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.memory = MagicMock()
    brain.memory.get_top_interests = AsyncMock(return_value=[{"topic": "Python", "weight": 3.0}])
    brain.memory.add_discovery = AsyncMock()
    brain.memory.bump_interest = AsyncMock()
    brain.router = MagicMock()
    brain.router.complete = AsyncMock(return_value="这是一段简要知识笔记。")
    brain.knowledge_manager = MagicMock()
    brain.knowledge_manager.save_note = MagicMock()
    brain.event_bus = MagicMock()
    brain.event_bus.publish = AsyncMock()
    return brain


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.asyncio
class TestAutonomousBrowsingAction:
    async def test_skips_when_browse_disabled(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", False), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock()) as mock_search:
            await action.execute(make_ctx(), mock_brain, mock_bot)

        mock_search.assert_not_called()
        mock_brain.memory.add_discovery.assert_not_awaited()

    async def test_respects_cooldown_per_chat(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        results = [{"title": "t", "url": "https://example.com/1", "snippet": "s"}]
        fetch_result = FetchResult(
            url="https://example.com/1",
            title="Title 1",
            text="Body 1",
            success=True,
            error="",
        )

        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", True), \
             patch("src.heartbeat.actions.autonomous_browsing.BROWSE_INTERVAL_HOURS", 2), \
             patch("src.heartbeat.actions.autonomous_browsing.load_prompt", return_value="{query} {title} {url} {page_text} {user_facts_summary} {top_interests_summary}"), \
             patch("src.heartbeat.actions.autonomous_browsing.random.random", return_value=0.1), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock(return_value=results)) as mock_search, \
             patch("src.heartbeat.actions.autonomous_browsing.web_fetcher.fetch", AsyncMock(return_value=fetch_result)):
            first = make_ctx(now=datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc))
            second = make_ctx(now=first.now + timedelta(minutes=30))
            await action.execute(first, mock_brain, mock_bot)
            await action.execute(second, mock_brain, mock_bot)

        mock_search.assert_awaited_once_with("Python", max_results=5)

    async def test_uses_interest_query_when_probability_hit(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", True), \
             patch("src.heartbeat.actions.autonomous_browsing.random.random", return_value=0.2), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock(return_value=[])) as mock_search:
            await action.execute(make_ctx(), mock_brain, mock_bot)

        mock_search.assert_awaited_once_with("Python", max_results=5)

    async def test_uses_source_query_when_no_interests(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        mock_brain.memory.get_top_interests = AsyncMock(return_value=[])

        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", True), \
             patch("src.heartbeat.actions.autonomous_browsing.BROWSE_SOURCES", ["reddit/technology"]), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock(return_value=[])) as mock_search:
            await action.execute(make_ctx(), mock_brain, mock_bot)

        mock_search.assert_awaited_once_with("Reddit r/technology hot posts", max_results=5)

    async def test_fetch_retries_until_success(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        results = [
            {"title": "t1", "url": "https://example.com/1", "snippet": "s1"},
            {"title": "t2", "url": "https://example.com/2", "snippet": "s2"},
            {"title": "t3", "url": "https://example.com/3", "snippet": "s3"},
        ]
        fail_1 = FetchResult(url="https://example.com/1", title="", text="", success=False, error="fail")
        fail_2 = FetchResult(url="https://example.com/2", title="", text="", success=False, error="fail")
        ok_3 = FetchResult(url="https://example.com/3", title="第三篇", text="正文", success=True, error="")

        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", True), \
             patch("src.heartbeat.actions.autonomous_browsing.load_prompt", return_value="{query} {title} {url} {page_text} {user_facts_summary} {top_interests_summary}"), \
             patch("src.heartbeat.actions.autonomous_browsing.random.random", return_value=0.1), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock(return_value=results)), \
             patch("src.heartbeat.actions.autonomous_browsing.web_fetcher.fetch", AsyncMock(side_effect=[fail_1, fail_2, ok_3])) as mock_fetch:
            await action.execute(make_ctx(), mock_brain, mock_bot)

        assert mock_fetch.await_count == 3
        call_kwargs = mock_brain.memory.add_discovery.call_args.kwargs
        assert call_kwargs["url"] == "https://example.com/3"

    async def test_does_not_write_when_all_fetch_failed(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        results = [
            {"title": "t1", "url": "https://example.com/1", "snippet": "s1"},
            {"title": "t2", "url": "https://example.com/2", "snippet": "s2"},
            {"title": "t3", "url": "https://example.com/3", "snippet": "s3"},
        ]
        fail = FetchResult(url="https://example.com/x", title="", text="", success=False, error="fail")

        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", True), \
             patch("src.heartbeat.actions.autonomous_browsing.random.random", return_value=0.1), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock(return_value=results)), \
             patch("src.heartbeat.actions.autonomous_browsing.web_fetcher.fetch", AsyncMock(side_effect=[fail, fail, fail])):
            await action.execute(make_ctx(), mock_brain, mock_bot)

        mock_brain.memory.add_discovery.assert_not_awaited()

    async def test_success_path_writes_all_side_effects(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        results = [{"title": "Python 新闻", "url": "https://example.com/python", "snippet": "s"}]
        fetch_result = FetchResult(
            url="https://example.com/python",
            title="Python 新闻",
            text="正文内容",
            success=True,
            error="",
        )

        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", True), \
             patch("src.heartbeat.actions.autonomous_browsing.load_prompt", return_value="{query} {title} {url} {page_text} {user_facts_summary} {top_interests_summary}"), \
             patch("src.heartbeat.actions.autonomous_browsing.random.random", return_value=0.1), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock(return_value=results)), \
             patch("src.heartbeat.actions.autonomous_browsing.web_fetcher.fetch", AsyncMock(return_value=fetch_result)):
            await action.execute(make_ctx(), mock_brain, mock_bot)

        discovery_kwargs = mock_brain.memory.add_discovery.call_args.kwargs
        assert discovery_kwargs["source"] == "autonomous_browsing"
        assert discovery_kwargs["title"] == "Python 新闻"
        assert discovery_kwargs["url"] == "https://example.com/python"

        mock_brain.knowledge_manager.save_note.assert_called_once_with(
            topic="Python",
            source_url="https://example.com/python",
            content="这是一段简要知识笔记。",
        )
        mock_brain.memory.bump_interest.assert_awaited_once_with("c1", "Python", increment=0.3)
        mock_brain.event_bus.publish.assert_awaited_once_with(
            "autonomous_browsing",
            {
                "chat_id": "c1",
                "query": "Python",
                "title": "Python 新闻",
                "url": "https://example.com/python",
            },
        )

    async def test_never_sends_message_directly(self, mock_brain, mock_bot):
        action = AutonomousBrowsingAction()
        results = [{"title": "Python 新闻", "url": "https://example.com/python", "snippet": "s"}]
        fetch_result = FetchResult(
            url="https://example.com/python",
            title="Python 新闻",
            text="正文内容",
            success=True,
            error="",
        )

        with patch("src.heartbeat.actions.autonomous_browsing.BROWSE_ENABLED", True), \
             patch("src.heartbeat.actions.autonomous_browsing.load_prompt", return_value="{query} {title} {url} {page_text} {user_facts_summary} {top_interests_summary}"), \
             patch("src.heartbeat.actions.autonomous_browsing.random.random", return_value=0.1), \
             patch("src.heartbeat.actions.autonomous_browsing.web_search.search", AsyncMock(return_value=results)), \
             patch("src.heartbeat.actions.autonomous_browsing.web_fetcher.fetch", AsyncMock(return_value=fetch_result)):
            await action.execute(make_ctx(), mock_brain, mock_bot)

        mock_bot.send_message.assert_not_called()
