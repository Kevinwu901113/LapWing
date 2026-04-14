"""Tests for tool loop reform: P0-P4."""

import json
import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.llm_exceptions import (
    APIConnectionError,
    APIOverloadError,
    APITimeoutError,
    EmptyResponseError,
    PromptTooLongError,
    classify_as_llm_exception,
)
from src.core.llm_router import ToolCallRequest
from src.core.task_runtime import (
    BUDGET_EXEMPT_TOOLS,
    TOOL_RESULT_BUDGET_MAX_CHARS,
    TOOL_RESULT_PREVIEW_CHARS,
    TaskRuntime,
)
from src.core.task_types import LoopRecoveryState
from src.tools.registry import build_default_tool_registry
from src.tools.types import ToolExecutionResult


# ── P0: Tool Result Budgeting ──────────────────────────────────────────────


class TestBudgetToolResult:
    def setup_method(self):
        self.runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
        self.tmp_dir = tempfile.mkdtemp()
        # Patch the result dir to use temp
        self._orig_dir = __import__("src.core.task_runtime", fromlist=["TOOL_RESULT_DIR"])
        import src.core.task_runtime as tr_mod
        self._orig_val = tr_mod.TOOL_RESULT_DIR
        tr_mod.TOOL_RESULT_DIR = self.tmp_dir

    def teardown_method(self):
        import src.core.task_runtime as tr_mod
        tr_mod.TOOL_RESULT_DIR = self._orig_val
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_small_result_passes_through(self):
        result = ToolExecutionResult(
            success=True,
            payload={"data": "small"},
        )
        budgeted = self.runtime._budget_tool_result("test_tool", result)
        assert budgeted.payload == {"data": "small"}
        assert not budgeted.payload.get("truncated")

    def test_large_result_is_budgeted(self):
        big_data = "x" * (TOOL_RESULT_BUDGET_MAX_CHARS + 1000)
        result = ToolExecutionResult(
            success=True,
            payload={"data": big_data},
        )
        budgeted = self.runtime._budget_tool_result("test_tool", result)
        assert budgeted.payload.get("truncated") is True
        assert "preview" in budgeted.payload
        assert len(budgeted.payload["preview"]) == TOOL_RESULT_PREVIEW_CHARS
        assert "full_result_path" in budgeted.payload
        # Verify file was actually written
        path = budgeted.payload["full_result_path"]
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            stored = f.read()
        assert big_data in stored

    def test_exempt_tools_not_budgeted(self):
        big_data = "x" * (TOOL_RESULT_BUDGET_MAX_CHARS + 1000)
        for tool_name in ("read_file", "memory_read"):
            result = ToolExecutionResult(
                success=True,
                payload={"data": big_data},
            )
            budgeted = self.runtime._budget_tool_result(tool_name, result)
            assert budgeted.payload == {"data": big_data}

    def test_budget_preserves_original_length(self):
        big_data = "y" * 60_000
        result = ToolExecutionResult(success=True, payload={"data": big_data})
        budgeted = self.runtime._budget_tool_result("web_search", result)
        assert budgeted.payload["original_chars"] > TOOL_RESULT_BUDGET_MAX_CHARS


# ── P1: Search Result Preprocessing ────────────────────────────────────────


class TestSearchPreprocessing:
    @pytest.mark.asyncio
    async def test_summarize_with_llm_router(self):
        """When llm_router is available, web_search_tool returns answer + sources."""
        from src.tools.handlers import web_search_tool
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest

        mock_router = MagicMock()
        mock_router.query_lightweight = AsyncMock(return_value="维斯塔潘获得冠军")

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={"router": mock_router},
        )
        req = ToolExecutionRequest(name="web_search", arguments={"query": "F1 最近一站"})

        with patch("src.tools.handlers.web_search.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [
                {"title": "F1结果", "url": "https://f1.com", "snippet": "维斯塔潘赢了"},
            ]
            result = await web_search_tool(req, ctx)

        assert result.success
        assert "answer" in result.payload
        assert result.payload["answer"] == "维斯塔潘获得冠军"
        assert "sources" in result.payload
        mock_router.query_lightweight.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_without_llm_router(self):
        """Without llm_router, web_search_tool returns lightweight result list."""
        from src.tools.handlers import web_search_tool
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={},  # no router
        )
        req = ToolExecutionRequest(name="web_search", arguments={"query": "test"})

        with patch("src.tools.handlers.web_search.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [
                {"title": "Test", "url": "https://test.com", "snippet": "a" * 500},
            ]
            result = await web_search_tool(req, ctx)

        assert result.success
        assert "results" in result.payload
        # Snippet should be truncated to 200 chars
        assert len(result.payload["results"][0]["snippet"]) <= 200

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back(self):
        """If LLM preprocessing fails, falls back to title list."""
        from src.tools.handlers import web_search_tool
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest

        mock_router = MagicMock()
        mock_router.query_lightweight = AsyncMock(side_effect=RuntimeError("LLM down"))

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={"router": mock_router},
        )
        req = ToolExecutionRequest(name="web_search", arguments={"query": "test"})

        with patch("src.tools.handlers.web_search.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [
                {"title": "Result 1", "url": "https://r1.com", "snippet": "content"},
            ]
            result = await web_search_tool(req, ctx)

        assert result.success
        # Should have an answer (the fallback text)
        assert "answer" in result.payload
        assert "未预处理" in result.payload["answer"]


# ── P2: Loop Error Recovery ────────────────────────────────────────────────


class TestLoopRecoveryState:
    def test_initial_state(self):
        state = LoopRecoveryState()
        assert state.turn_count == 0
        assert state.can_reactive_compact()
        assert state.can_output_recovery()
        assert state.can_retry_api()

    def test_compact_exhaustion(self):
        state = LoopRecoveryState()
        state.reactive_compact_attempts = 2
        assert not state.can_reactive_compact()

    def test_output_recovery_exhaustion(self):
        state = LoopRecoveryState()
        state.max_output_recovery_count = 2
        assert not state.can_output_recovery()

    def test_api_retry_exhaustion(self):
        state = LoopRecoveryState()
        state.consecutive_api_errors = 3
        assert not state.can_retry_api()

    def test_reset_api_errors(self):
        state = LoopRecoveryState()
        state.consecutive_api_errors = 2
        state.reset_api_errors()
        assert state.consecutive_api_errors == 0
        assert state.can_retry_api()

    def test_record_transition(self):
        state = LoopRecoveryState()
        state.record_transition("api_retry")
        assert state.turn_count == 1
        assert state.transition_reason == "api_retry"


class TestClassifyLLMException:
    def test_rate_limit_429(self):
        exc = Exception("rate limit")
        exc.status_code = 429
        result = classify_as_llm_exception(exc)
        assert isinstance(result, APIOverloadError)

    def test_overload_529(self):
        exc = Exception("overloaded")
        exc.status_code = 529
        result = classify_as_llm_exception(exc)
        assert isinstance(result, APIOverloadError)

    def test_prompt_too_long_400(self):
        exc = Exception("prompt is too long for context window")
        exc.status_code = 400
        result = classify_as_llm_exception(exc)
        assert isinstance(result, PromptTooLongError)

    def test_timeout(self):
        class TimeoutError(Exception):
            pass
        exc = TimeoutError("request timed out")
        result = classify_as_llm_exception(exc)
        assert isinstance(result, APITimeoutError)

    def test_connection_error(self):
        class ConnectError(Exception):
            pass
        exc = ConnectError("connection refused")
        result = classify_as_llm_exception(exc)
        assert isinstance(result, APIConnectionError)

    def test_unrecoverable_returns_none(self):
        exc = Exception("unknown error")
        exc.status_code = 500
        result = classify_as_llm_exception(exc)
        assert result is None

    def test_auth_error_returns_none(self):
        exc = Exception("unauthorized")
        exc.status_code = 401
        result = classify_as_llm_exception(exc)
        assert result is None


class TestReactiveCompact:
    def test_clears_old_tool_results(self):
        runtime = TaskRuntime(router=MagicMock())
        # Build a message list with 10 tool results
        messages = [{"role": "system", "content": "system"}]
        for i in range(10):
            messages.append({"role": "user", "content": f"msg {i}"})
            messages.append({"role": "tool", "content": f"result {i}", "tool_call_id": f"c{i}"})

        runtime._reactive_compact(messages)

        # Should have cleared all but last 6 tool results
        cleared = [m for m in messages if m.get("role") == "tool" and "已被清理" in str(m.get("content", ""))]
        kept = [m for m in messages if m.get("role") == "tool" and "已被清理" not in str(m.get("content", ""))]
        assert len(cleared) == 4  # 10 - 6
        assert len(kept) == 6

    def test_no_clearing_with_few_results(self):
        runtime = TaskRuntime(router=MagicMock())
        messages = [
            {"role": "tool", "content": "result 1", "tool_call_id": "c1"},
            {"role": "tool", "content": "result 2", "tool_call_id": "c2"},
        ]
        runtime._reactive_compact(messages)
        assert all("已被清理" not in str(m.get("content", "")) for m in messages)


# ── P3: Search/Fetch Separation ────────────────────────────────────────────


class TestWebFetchWithQuestion:
    @pytest.mark.asyncio
    async def test_fetch_with_question_uses_llm(self):
        from src.tools.handlers import web_fetch_tool
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.web_fetcher import FetchResult

        mock_router = MagicMock()
        mock_router.query_lightweight = AsyncMock(return_value="维斯塔潘赢了巴林站")

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={"router": mock_router},
        )
        req = ToolExecutionRequest(
            name="web_fetch",
            arguments={"url": "https://f1.com/results", "question": "谁赢了上一站F1"},
        )

        with patch("src.tools.handlers.web_fetcher.fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResult(
                url="https://f1.com/results",
                title="F1 Results",
                text="Verstappen won the race..." * 100,
                success=True,
                error="",
            )
            result = await web_fetch_tool(req, ctx)

        assert result.success
        assert "answer" in result.payload
        assert result.payload["answer"] == "维斯塔潘赢了巴林站"
        mock_router.query_lightweight.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_without_question_returns_text(self):
        from src.tools.handlers import web_fetch_tool
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.web_fetcher import FetchResult

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={},
        )
        req = ToolExecutionRequest(
            name="web_fetch",
            arguments={"url": "https://example.com"},
        )

        with patch("src.tools.handlers.web_fetcher.fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResult(
                url="https://example.com",
                title="Example",
                text="Hello world",
                success=True,
                error="",
            )
            result = await web_fetch_tool(req, ctx)

        assert result.success
        assert "text" in result.payload
        assert result.payload["text"] == "Hello world"
        assert "answer" not in result.payload

    @pytest.mark.asyncio
    async def test_fetch_llm_failure_falls_back_to_raw(self):
        from src.tools.handlers import web_fetch_tool
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.web_fetcher import FetchResult

        mock_router = MagicMock()
        mock_router.query_lightweight = AsyncMock(side_effect=RuntimeError("LLM down"))

        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd="/tmp",
            services={"router": mock_router},
        )
        req = ToolExecutionRequest(
            name="web_fetch",
            arguments={"url": "https://example.com", "question": "what is this?"},
        )

        with patch("src.tools.handlers.web_fetcher.fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResult(
                url="https://example.com",
                title="Example",
                text="Page content",
                success=True,
                error="",
            )
            result = await web_fetch_tool(req, ctx)

        assert result.success
        assert "text" in result.payload  # Falls back to raw text


# ── P4: Loop State Observability ───────────────────────────────────────────


class TestLoopRecoveryStateTracking:
    def test_total_result_chars_accumulates(self):
        state = LoopRecoveryState()
        state.total_result_chars += 500
        state.total_result_chars += 1200
        assert state.total_result_chars == 1700

    def test_transition_recording(self):
        state = LoopRecoveryState()
        state.record_transition("tool_turn")
        assert state.turn_count == 1
        state.record_transition("api_retry")
        assert state.turn_count == 2
        assert state.transition_reason == "api_retry"


# ── Heartbeat cleanup ─────────────────────────────────────────────────────


class TestToolResultCleanup:
    def test_cleanup_removes_old_files(self):
        from src.heartbeat.actions.memory_maintenance import MemoryMaintenanceAction

        tmp_dir = tempfile.mkdtemp()
        try:
            # Create old and new files
            old_file = os.path.join(tmp_dir, "old.txt")
            new_file = os.path.join(tmp_dir, "new.txt")
            with open(old_file, "w") as f:
                f.write("old")
            with open(new_file, "w") as f:
                f.write("new")
            # Make old file appear old
            os.utime(old_file, (0, 0))

            with patch("src.heartbeat.actions.memory_maintenance._TOOL_RESULT_DIR", tmp_dir):
                MemoryMaintenanceAction._cleanup_tool_results()

            assert not os.path.exists(old_file)
            assert os.path.exists(new_file)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
