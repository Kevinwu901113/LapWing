"""InterestTracker 单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.memory.interest_tracker as interest_tracker_module
from src.memory.interest_tracker import InterestTracker


@pytest.fixture
def memory():
    m = MagicMock()
    m.get = AsyncMock(return_value=[
        {"role": "user", "content": "我最近在学 Python 和机器学习"},
        {"role": "assistant", "content": "听起来你挺投入的"},
    ])
    m.bump_interest = AsyncMock()
    return m


@pytest.fixture
def router():
    r = MagicMock()
    r.complete = AsyncMock(return_value="[]")
    return r


@pytest.fixture
async def tracker(memory, router, monkeypatch):
    monkeypatch.setattr(interest_tracker_module, "INTEREST_EXTRACT_TURN_THRESHOLD", 3)
    t = InterestTracker(memory, router)
    yield t
    await t.shutdown()


class TestInterestTracker:
    @pytest.mark.asyncio
    async def test_notify_triggers_extraction_at_threshold(self, tracker):
        tracker._extract = AsyncMock()
        tracker.notify("chat1")
        tracker.notify("chat1")
        tracker.notify("chat1")
        await asyncio.sleep(0)
        tracker._extract.assert_called_once_with("chat1")

    @pytest.mark.asyncio
    async def test_notify_does_not_trigger_before_threshold(self, tracker):
        tracker._extract = AsyncMock()
        tracker.notify("chat1")
        tracker.notify("chat1")
        await asyncio.sleep(0)
        tracker._extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_calls_bump_for_each_topic(self, tracker, memory, router):
        router.complete = AsyncMock(
            return_value='[{"topic": "Python编程", "weight": 2.0}, {"topic": "机器学习", "weight": 1.0}]'
        )
        await tracker._extract("chat1")
        memory.bump_interest.assert_any_await("chat1", "Python编程", 2.0)
        memory.bump_interest.assert_any_await("chat1", "机器学习", 1.0)
        assert memory.bump_interest.await_count == 2

    def test_parse_result_valid_json(self, tracker):
        result = tracker._parse_result('[{"topic": "Python", "weight": 1.5}]')
        assert result == [{"topic": "Python", "weight": 1.5}]

    def test_parse_result_strips_markdown_fence(self, tracker):
        result = tracker._parse_result('```json\n[{"topic": "摄影", "weight": 1.0}]\n```')
        assert result == [{"topic": "摄影", "weight": 1.0}]

    def test_parse_result_invalid_json_returns_empty(self, tracker):
        assert tracker._parse_result("not json") == []

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending_tasks(self, tracker):
        blocker = asyncio.Event()

        async def slow_extract(chat_id: str) -> None:
            await blocker.wait()

        tracker._extract = AsyncMock(side_effect=slow_extract)
        tracker.notify("chat1")
        tracker.notify("chat1")
        tracker.notify("chat1")
        await asyncio.sleep(0)
        task = next(iter(tracker._tasks))
        await tracker.shutdown()
        assert task.cancelled()
