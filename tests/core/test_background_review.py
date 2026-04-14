"""测试背景自动回顾。"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.background_review import BackgroundReviewer, REVIEW_PROMPT


class TestBackgroundReviewerTick:
    def test_tick_counts_correctly(self):
        reviewer = BackgroundReviewer(interval=3)
        assert reviewer.tick() is False  # 1
        assert reviewer.tick() is False  # 2
        assert reviewer.tick() is True   # 3 → 触发
        assert reviewer.tick() is False  # 重置，1

    def test_tick_resets_after_trigger(self):
        reviewer = BackgroundReviewer(interval=2)
        reviewer.tick()  # 1
        assert reviewer.tick() is True  # 2 → 触发
        assert reviewer._turns_since_review == 0

    def test_interval_minimum_is_1(self):
        reviewer = BackgroundReviewer(interval=0)
        assert reviewer._interval == 1


@pytest.mark.asyncio
class TestBackgroundReviewerMaybeReview:
    async def test_calls_run_review_when_due(self):
        reviewer = BackgroundReviewer(interval=1)  # 每轮都触发
        mock_router = MagicMock()
        mock_memory = AsyncMock()
        mock_memory.get = AsyncMock(return_value=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "嗨！"},
        ])
        mock_router.complete = AsyncMock(return_value="NOTHING")

        await reviewer.maybe_review(
            router=mock_router,
            memory=mock_memory,
            chat_id="test_chat",
        )
        # 等后台任务完成
        await asyncio.sleep(0.2)

        mock_router.complete.assert_called_once()

    async def test_no_concurrent_reviews(self):
        reviewer = BackgroundReviewer(interval=1)
        reviewer._review_running = True
        # Manually tick to make it "due"
        reviewer._turns_since_review = 0

        mock_router = MagicMock()
        mock_router.complete = AsyncMock()
        mock_memory = AsyncMock()

        await reviewer.maybe_review(
            router=mock_router,
            memory=mock_memory,
            chat_id="test",
        )
        await asyncio.sleep(0.1)
        mock_router.complete.assert_not_called()

    async def test_review_failure_is_non_fatal(self):
        reviewer = BackgroundReviewer(interval=1)
        mock_router = MagicMock()
        mock_router.complete = AsyncMock(side_effect=Exception("LLM error"))
        mock_memory = AsyncMock()
        mock_memory.get = AsyncMock(return_value=[
            {"role": "user", "content": "test"},
        ])

        await reviewer.maybe_review(
            router=mock_router,
            memory=mock_memory,
            chat_id="test",
        )
        await asyncio.sleep(0.2)
        # review_running 应该被重置
        assert reviewer._review_running is False

    async def test_skips_when_no_recent_messages(self):
        reviewer = BackgroundReviewer(interval=1)
        mock_router = MagicMock()
        mock_router.complete = AsyncMock()
        mock_memory = AsyncMock()
        mock_memory.get = AsyncMock(return_value=[])

        await reviewer.maybe_review(
            router=mock_router,
            memory=mock_memory,
            chat_id="test",
        )
        await asyncio.sleep(0.1)
        mock_router.complete.assert_not_called()


@pytest.mark.asyncio
class TestProcessReviewResult:
    async def test_nothing_result_skips(self):
        reviewer = BackgroundReviewer()
        with patch("src.tools.memory_note.write_note") as mock_write:
            await reviewer._process_review_result("NOTHING")
            mock_write.assert_not_called()

    async def test_remember_line_calls_write_note(self):
        reviewer = BackgroundReviewer()
        with patch("src.tools.memory_note.write_note", new_callable=AsyncMock) as mock_write:
            mock_write.return_value = {"success": True}
            await reviewer._process_review_result(
                "REMEMBER: kevin | 喜欢看棒球"
            )
            mock_write.assert_called_once_with("kevin", "喜欢看棒球")

    async def test_multiple_remember_lines(self):
        reviewer = BackgroundReviewer()
        with patch("src.tools.memory_note.write_note", new_callable=AsyncMock) as mock_write:
            mock_write.return_value = {"success": True}
            await reviewer._process_review_result(
                "REMEMBER: kevin | 喜欢看棒球\nREMEMBER: self | 要多用日语表达"
            )
            assert mock_write.call_count == 2

    async def test_invalid_target_ignored(self):
        reviewer = BackgroundReviewer()
        with patch("src.tools.memory_note.write_note", new_callable=AsyncMock) as mock_write:
            await reviewer._process_review_result(
                "REMEMBER: invalid_target | some content"
            )
            mock_write.assert_not_called()

    async def test_malformed_line_ignored(self):
        reviewer = BackgroundReviewer()
        with patch("src.tools.memory_note.write_note", new_callable=AsyncMock) as mock_write:
            await reviewer._process_review_result(
                "REMEMBER: no pipe here"
            )
            mock_write.assert_not_called()


class TestReviewPrompt:
    def test_review_prompt_is_chinese(self):
        assert "Kevin" in REVIEW_PROMPT
        assert "记住" in REVIEW_PROMPT or "保存" in REVIEW_PROMPT

    def test_review_prompt_mentions_targets(self):
        assert "kevin" in REVIEW_PROMPT
        assert "self" in REVIEW_PROMPT
