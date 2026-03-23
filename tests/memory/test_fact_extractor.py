"""FactExtractor 单元测试。"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.memory.fact_extractor import FactExtractor


@pytest.fixture
def memory():
    m = MagicMock()
    m.get = AsyncMock(return_value=[
        {"role": "user", "content": "我不喜欢吃辣"},
        {"role": "assistant", "content": "好的，我记住了"},
    ])
    m.get_user_facts = AsyncMock(return_value=[])
    m.set_user_fact = AsyncMock()
    return m


@pytest.fixture
def router():
    r = MagicMock()
    r.complete = AsyncMock(return_value="[]")
    return r


@pytest.fixture
def extractor(memory, router):
    return FactExtractor(memory, router)


# ===== notify() 行为 =====

class TestNotify:
    async def test_triggers_extraction_when_turn_threshold_reached(self, extractor):
        """达到轮次阈值时触发提取。"""
        extractor._run_extraction = AsyncMock()
        extractor.notify("chat1")
        extractor.notify("chat1")
        extractor.notify("chat1")  # 第 3 轮
        await asyncio.sleep(0)
        extractor._run_extraction.assert_called_once_with("chat1")

    async def test_does_not_trigger_extraction_before_threshold(self, extractor):
        """未达到轮次阈值时不触发提取。"""
        extractor._run_extraction = AsyncMock()
        extractor.notify("chat1")
        extractor.notify("chat1")
        await asyncio.sleep(0)
        extractor._run_extraction.assert_not_called()

    async def test_cancels_previous_idle_timer_on_new_message(self, extractor):
        """新消息到达时取消旧的空闲计时器。"""
        extractor.notify("chat1")
        first_task = extractor._idle_tasks.get("chat1")
        assert first_task is not None
        extractor.notify("chat1")
        await asyncio.sleep(0)
        assert first_task.cancelled()

    async def test_resets_turn_count_after_threshold(self, extractor):
        """达到阈值触发提取后，轮次计数重置，下一批仍能触发。"""
        extractor._run_extraction = AsyncMock()
        for _ in range(6):  # 两个完整阈值周期
            extractor.notify("chat1")
        await asyncio.sleep(0)
        assert extractor._run_extraction.call_count == 2

    async def test_independent_counts_per_chat(self, extractor):
        """不同 chat_id 的轮次计数相互独立。"""
        extractor._run_extraction = AsyncMock()
        extractor.notify("chat1")
        extractor.notify("chat2")
        extractor.notify("chat1")
        extractor.notify("chat1")  # chat1 达到阈值
        await asyncio.sleep(0)
        extractor._run_extraction.assert_called_once_with("chat1")

    async def test_creates_idle_timer_on_notify(self, extractor):
        """每次 notify 都会创建空闲计时器。"""
        extractor.notify("chat1")
        assert "chat1" in extractor._idle_tasks
        assert extractor._idle_tasks["chat1"] is not None

    async def test_cancels_idle_timer_when_turn_threshold_fires(self, extractor):
        """达到轮次阈值触发提取时，同时取消刚创建的空闲计时器，避免重复提取。"""
        extractor._run_extraction = AsyncMock()
        extractor.notify("chat1")
        extractor.notify("chat1")
        extractor.notify("chat1")  # 第 3 轮：触发提取，应同时取消空闲计时器
        await asyncio.sleep(0)
        # 空闲计时器应已被取消（不再存在于 _idle_tasks 中）
        assert "chat1" not in extractor._idle_tasks


# ===== _run_extraction() 行为 =====

class TestRunExtraction:
    async def test_stores_extracted_facts_in_memory(self, extractor, memory, router):
        """成功提取时将 facts 写入 memory。"""
        router.complete = AsyncMock(
            return_value='[{"fact_key": "偏好_食物_不吃辣", "fact_value": "不喜欢吃辣的食物"}]'
        )
        await extractor._run_extraction("chat1")
        memory.set_user_fact.assert_called_once_with("chat1", "偏好_食物_不吃辣", "不喜欢吃辣的食物")

    async def test_stores_multiple_facts(self, extractor, memory, router):
        """一次提取多条 facts 时全部写入。"""
        router.complete = AsyncMock(
            return_value='[{"fact_key": "偏好_食物_不吃辣", "fact_value": "不喜欢辣"}, '
                         '{"fact_key": "项目_Lapwing", "fact_value": "AI伴侣项目"}]'
        )
        await extractor._run_extraction("chat1")
        assert memory.set_user_fact.call_count == 2

    async def test_handles_empty_extraction_result(self, extractor, memory, router):
        """LLM 返回空数组时不写入任何 fact。"""
        router.complete = AsyncMock(return_value="[]")
        await extractor._run_extraction("chat1")
        memory.set_user_fact.assert_not_called()

    async def test_handles_llm_failure_silently(self, extractor, memory, router):
        """LLM 调用失败时静默处理，不崩溃。"""
        router.complete = AsyncMock(side_effect=Exception("API error"))
        await extractor._run_extraction("chat1")  # 不应抛出异常
        memory.set_user_fact.assert_not_called()

    async def test_handles_malformed_json_silently(self, extractor, memory, router):
        """LLM 返回非 JSON 内容时静默处理，不崩溃。"""
        router.complete = AsyncMock(return_value="这不是JSON内容")
        await extractor._run_extraction("chat1")  # 不应抛出异常
        memory.set_user_fact.assert_not_called()

    async def test_skips_if_extraction_already_running(self, extractor, memory, router):
        """同一 chat_id 已在提取中时跳过，不重复调用 LLM。"""
        extractor._extracting.add("chat1")
        await extractor._run_extraction("chat1")
        router.complete.assert_not_called()

    async def test_uses_tool_purpose_for_llm_call(self, extractor, router):
        """LLM 调用使用 purpose='tool' 以节省成本。"""
        router.complete = AsyncMock(return_value="[]")
        await extractor._run_extraction("chat1")
        router.complete.assert_called_once()
        assert router.complete.call_args.kwargs.get("purpose") == "tool"

    async def test_clears_extracting_flag_after_completion(self, extractor):
        """提取完成后 _extracting 标记被清除。"""
        await extractor._run_extraction("chat1")
        assert "chat1" not in extractor._extracting

    async def test_clears_extracting_flag_on_error(self, extractor, router):
        """提取出错时 _extracting 标记仍被清除。"""
        router.complete = AsyncMock(side_effect=Exception("API error"))
        await extractor._run_extraction("chat1")
        assert "chat1" not in extractor._extracting

    async def test_force_extraction_delegates_to_run_extraction(self, extractor):
        """force_extraction 是 _run_extraction 的公开封装。"""
        extractor._run_extraction = AsyncMock()
        await extractor.force_extraction("chat1")
        extractor._run_extraction.assert_called_once_with("chat1")


# ===== _parse_result() 行为 =====

class TestParseResult:
    def test_parses_valid_json_array(self, extractor):
        result = extractor._parse_result('[{"fact_key": "k", "fact_value": "v"}]')
        assert result == [{"fact_key": "k", "fact_value": "v"}]

    def test_returns_empty_list_for_empty_array(self, extractor):
        result = extractor._parse_result("[]")
        assert result == []

    def test_strips_markdown_code_fence(self, extractor):
        text = '```json\n[{"fact_key": "k", "fact_value": "v"}]\n```'
        result = extractor._parse_result(text)
        assert result == [{"fact_key": "k", "fact_value": "v"}]

    def test_strips_plain_code_fence(self, extractor):
        text = '```\n[{"fact_key": "k", "fact_value": "v"}]\n```'
        result = extractor._parse_result(text)
        assert result == [{"fact_key": "k", "fact_value": "v"}]

    def test_returns_empty_list_for_malformed_json(self, extractor):
        result = extractor._parse_result("这不是JSON")
        assert result == []

    def test_returns_empty_list_for_missing_fact_key(self, extractor):
        result = extractor._parse_result('[{"wrong_key": "value"}]')
        assert result == []

    def test_filters_entries_with_empty_fact_key(self, extractor):
        result = extractor._parse_result('[{"fact_key": "", "fact_value": "v"}]')
        assert result == []

    def test_filters_entries_with_empty_fact_value(self, extractor):
        result = extractor._parse_result('[{"fact_key": "k", "fact_value": ""}]')
        assert result == []

    def test_returns_empty_list_if_not_a_list(self, extractor):
        result = extractor._parse_result('{"fact_key": "k", "fact_value": "v"}')
        assert result == []


# ===== shutdown() 行为 =====

class TestShutdown:
    async def test_cancels_all_pending_idle_tasks(self, extractor):
        """shutdown 取消所有待处理的空闲计时器。"""
        extractor.notify("chat1")
        extractor.notify("chat2")
        task1 = extractor._idle_tasks["chat1"]
        task2 = extractor._idle_tasks["chat2"]
        await extractor.shutdown()
        assert task1.cancelled()
        assert task2.cancelled()
