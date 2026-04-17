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
        budgeted = self.runtime._budget_tool_result("research", result)
        assert budgeted.payload["original_chars"] > TOOL_RESULT_BUDGET_MAX_CHARS


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


# TestToolResultCleanup removed (Phase 1: memory_maintenance action deleted)
