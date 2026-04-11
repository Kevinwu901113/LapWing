"""进度汇报器单元测试 + check_and_report 集成测试。"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.progress_reporter import (
    MAX_REPORTS_PER_TASK,
    MIN_INTERVAL_BETWEEN_REPORTS,
    MIN_STEPS_BEFORE_CHECK,
    NO_REPORT_MARKER,
    ProgressState,
    _brief_args,
    build_progress_context,
    check_and_report,
)


# ── ProgressState 单元测试 ────────────────────────────────────


class TestProgressState:
    def test_should_check_returns_false_when_steps_below_minimum(self):
        state = ProgressState(user_request="搜一下")
        state.record_step("web_search", {"query": "test"}, "结果")
        assert not state.should_check()

    def test_should_check_returns_true_when_conditions_met(self):
        state = ProgressState(user_request="搜一下")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"test{i}"}, f"结果{i}")
        assert state.should_check()

    def test_should_check_returns_false_when_max_reports_reached(self):
        state = ProgressState(user_request="搜一下")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"test{i}"}, f"结果{i}")
        for _ in range(MAX_REPORTS_PER_TASK):
            state.record_report("已汇报")
        assert not state.should_check()

    def test_should_check_returns_false_when_interval_too_short(self):
        state = ProgressState(user_request="搜一下")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"test{i}"}, f"结果{i}")
        state.record_report("已汇报")
        # last_report_time 刚刚设置，间隔不够
        assert not state.should_check()

    def test_should_check_returns_true_after_sufficient_interval(self):
        state = ProgressState(user_request="搜一下")
        for i in range(MIN_STEPS_BEFORE_CHECK + 1):
            state.record_step("web_search", {"query": f"test{i}"}, f"结果{i}")
        state.record_report("已汇报")
        # 手动回拨时间
        state.last_report_time = time.time() - MIN_INTERVAL_BETWEEN_REPORTS - 1
        assert state.should_check()

    def test_record_step_appends_to_completed_steps(self):
        state = ProgressState()
        state.record_step("web_search", {"query": "hello"}, "找到了")
        assert len(state.completed_steps) == 1
        assert state.completed_steps[0]["tool"] == "web_search"
        assert "hello" in state.completed_steps[0]["args_brief"]

    def test_record_report_updates_state(self):
        state = ProgressState()
        state.record_report("搜到了一些")
        assert len(state.sent_reports) == 1
        assert state.sent_reports[0] == "搜到了一些"
        assert state.last_report_time > 0


# ── _brief_args 单元测试 ──────────────────────────────────────


class TestBriefArgs:
    def test_short_args_preserved(self):
        result = _brief_args({"query": "hello"})
        assert result == "query=hello"

    def test_long_args_truncated(self):
        long_val = "x" * 100
        result = _brief_args({"query": long_val})
        assert len(result) < 100
        assert result.endswith("...")

    def test_empty_args(self):
        assert _brief_args({}) == ""


# ── build_progress_context 单元测试 ───────────────────────────


class TestBuildProgressContext:
    def test_basic_context(self):
        state = ProgressState(user_request="帮我查天气")
        state.record_step("web_search", {"query": "北京天气"}, "晴转多云")
        ctx = build_progress_context(state)
        assert ctx["user_request"] == "帮我查天气"
        assert "web_search" in ctx["completed_steps"]
        assert "晴转多云" in ctx["latest_result"]
        assert "还没有" in ctx["sent_messages"]

    def test_context_with_prior_reports(self):
        state = ProgressState(user_request="整理信息")
        state.record_step("web_search", {"query": "a"}, "结果a")
        state.record_report("搜到了一些")
        ctx = build_progress_context(state)
        assert "搜到了一些" in ctx["sent_messages"]


# ── check_and_report 集成测试 ─────────────────────────────────


class TestCheckAndReport:
    @pytest.mark.asyncio
    async def test_no_report_when_steps_below_minimum(self):
        state = ProgressState(user_request="测试")
        state.record_step("web_search", {"query": "a"}, "结果")
        router = MagicMock()
        result = await check_and_report(
            state=state,
            llm_router=router,
            on_interim_text=AsyncMock(),
            messages=[],
        )
        assert result is False
        router.query_lightweight.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_report_when_on_interim_text_is_none(self):
        state = ProgressState(user_request="测试")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"q{i}"}, f"r{i}")
        result = await check_and_report(
            state=state,
            llm_router=MagicMock(),
            on_interim_text=None,
            messages=[],
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_no_report_when_llm_returns_marker(self):
        state = ProgressState(user_request="测试")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"q{i}"}, f"r{i}")

        router = MagicMock()
        router.query_lightweight = AsyncMock(return_value=NO_REPORT_MARKER)

        on_interim = AsyncMock()
        messages = []
        result = await check_and_report(
            state=state,
            llm_router=router,
            on_interim_text=on_interim,
            messages=messages,
        )
        assert result is False
        on_interim.assert_not_called()
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_sends_report_when_llm_generates_text(self):
        state = ProgressState(user_request="帮我整理信息")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"q{i}"}, f"r{i}")

        router = MagicMock()
        router.query_lightweight = AsyncMock(return_value="搜到了一些，我再找找")

        on_interim = AsyncMock()
        messages = []
        with patch("src.core.prompt_builder.build_progress_prompt", return_value=("sys", "user")):
            result = await check_and_report(
                state=state,
                llm_router=router,
                on_interim_text=on_interim,
                messages=messages,
            )

        assert result is True
        on_interim.assert_awaited_once_with("搜到了一些，我再找找", bypass_monologue_filter=True)
        assert len(state.sent_reports) == 1
        assert state.sent_reports[0] == "搜到了一些，我再找找"

    @pytest.mark.asyncio
    async def test_inserts_system_reminder_into_messages(self):
        state = ProgressState(user_request="帮我查")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"q{i}"}, f"r{i}")

        router = MagicMock()
        router.query_lightweight = AsyncMock(return_value="找到了一些")

        messages = [{"role": "user", "content": "帮我查"}]
        with patch("src.core.prompt_builder.build_progress_prompt", return_value=("sys", "user")):
            await check_and_report(
                state=state,
                llm_router=router,
                on_interim_text=AsyncMock(),
                messages=messages,
            )

        assert len(messages) == 2
        reminder = messages[1]
        assert reminder["role"] == "user"
        assert "系统提醒" in reminder["content"]
        assert "找到了一些" in reminder["content"]
        assert "不要重复" in reminder["content"]

    @pytest.mark.asyncio
    async def test_handles_llm_failure_gracefully(self):
        state = ProgressState(user_request="测试")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"q{i}"}, f"r{i}")

        router = MagicMock()
        router.query_lightweight = AsyncMock(side_effect=RuntimeError("LLM down"))

        on_interim = AsyncMock()
        with patch("src.core.prompt_builder.build_progress_prompt", return_value=("sys", "user")):
            result = await check_and_report(
                state=state,
                llm_router=router,
                on_interim_text=on_interim,
                messages=[],
            )

        assert result is False
        on_interim.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_response_after_marker_stripped_returns_false(self):
        state = ProgressState(user_request="测试")
        for i in range(MIN_STEPS_BEFORE_CHECK):
            state.record_step("web_search", {"query": f"q{i}"}, f"r{i}")

        router = MagicMock()
        router.query_lightweight = AsyncMock(return_value=f"  {NO_REPORT_MARKER}  ")

        with patch("src.core.prompt_builder.build_progress_prompt", return_value=("sys", "user")):
            result = await check_and_report(
                state=state,
                llm_router=router,
                on_interim_text=AsyncMock(),
                messages=[],
            )
        assert result is False
