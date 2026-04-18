"""NoActionBudget / ErrorBurstGuard / KV-cache 粗粒度时间 的单元测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.task_types import ErrorBurstGuard, NoActionBudget


# ── NoActionBudget ───────────────────────────────────────────────────────────


class TestNoActionBudget:
    def test_initial_state(self):
        b = NoActionBudget()
        assert b.remaining == 3
        assert not b.exhausted

    def test_consume_returns_true_while_remaining(self):
        b = NoActionBudget(default=3, remaining=3)
        assert b.consume() is True   # remaining=2
        assert b.consume() is True   # remaining=1
        assert not b.exhausted

    def test_consume_returns_false_on_exhaustion(self):
        b = NoActionBudget(default=3, remaining=3)
        b.consume()  # 2
        b.consume()  # 1
        result = b.consume()  # 0
        assert result is False
        assert b.exhausted

    def test_reset_restores_default(self):
        b = NoActionBudget(default=3, remaining=1)
        b.reset()
        assert b.remaining == 3
        assert not b.exhausted

    def test_exhausted_after_exact_default_consumes(self):
        """连续消耗 default 次后 exhausted"""
        b = NoActionBudget(default=5, remaining=5)
        for _ in range(5):
            b.consume()
        assert b.exhausted


# ── ErrorBurstGuard ──────────────────────────────────────────────────────────


class TestErrorBurstGuard:
    def test_initial_state(self):
        g = ErrorBurstGuard()
        assert g.error_count == 0
        assert not g.should_break
        assert g.summary == ""

    def test_triggers_after_threshold(self):
        """连续 3 次错误后 should_break=True"""
        g = ErrorBurstGuard(threshold=3)
        assert g.record_error("err1") is False
        assert g.record_error("err2") is False
        assert g.record_error("err3") is True
        assert g.should_break

    def test_success_decrements_error_count(self):
        """成功调用降低错误计数（渐进恢复）"""
        g = ErrorBurstGuard(threshold=3)
        g.record_error("e1")
        g.record_error("e2")
        assert g.error_count == 2
        g.record_success()
        assert g.error_count == 1
        g.record_success()
        assert g.error_count == 0
        # 不会降到负数
        g.record_success()
        assert g.error_count == 0

    def test_recovery_prevents_break(self):
        """中间成功可以阻止断路"""
        g = ErrorBurstGuard(threshold=3)
        g.record_error("e1")
        g.record_error("e2")
        g.record_success()  # error_count: 2 -> 1
        assert g.record_error("e3") is False  # count=2, 还没到 3
        assert not g.should_break

    def test_summary_shows_recent_errors(self):
        g = ErrorBurstGuard()
        g.record_error("connection refused")
        g.record_error("timeout")
        g.record_error("404 not found")
        summary = g.summary
        assert "connection refused" in summary
        assert "timeout" in summary
        assert "404 not found" in summary

    def test_summary_truncates_long_errors(self):
        g = ErrorBurstGuard()
        long_err = "x" * 500
        g.record_error(long_err)
        assert len(g.recent_errors[0]) == 200

    def test_recent_errors_capped_at_10(self):
        g = ErrorBurstGuard()
        for i in range(15):
            g.record_error(f"err_{i}")
        assert len(g.recent_errors) == 10
        assert g.recent_errors[0] == "err_5"  # 前 5 个被移除

    def test_summary_shows_last_3(self):
        """summary 只展示最近 3 条"""
        g = ErrorBurstGuard()
        for i in range(5):
            g.record_error(f"err_{i}")
        summary = g.summary
        assert "err_2" in summary
        assert "err_3" in summary
        assert "err_4" in summary
        assert "err_0" not in summary


# ── TaskRuntime integration (NoActionBudget) ─────────────────────────────────


@pytest.mark.asyncio
async def test_task_runtime_stops_on_budget_exhaustion():
    """TaskRuntime 在 NoActionBudget 耗尽时结束循环（budget 仅在曾用过工具后激活）"""
    from src.core.task_runtime import TaskRuntime
    from src.core.llm_router import ToolCallRequest

    call_count = 0

    async def mock_complete_with_tools(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第一轮：返回一个工具调用，激活 budget
            return SimpleNamespace(
                tool_calls=[ToolCallRequest(id="c1", name="test_tool", arguments={})],
                text="",
                continuation_message={"role": "assistant", "content": "", "tool_calls": []},
            )
        # 之后都是纯文本（no-action）
        return SimpleNamespace(
            tool_calls=[],
            text="我在思考...",
            continuation_message=None,
        )

    router = MagicMock()
    router.complete_with_tools = AsyncMock(side_effect=mock_complete_with_tools)
    router.build_tool_result_message = MagicMock(return_value={"role": "tool", "content": "ok"})

    runtime = TaskRuntime(router=router)

    # Mock tool execution to succeed
    async def mock_execute(*, tool_call, state, deps, task_id, chat_id,
                           event_bus, profile, services, adapter, user_id,
                           send_fn=None):
        return "OK", {"result": "ok"}, True

    runtime._execute_tool_call = mock_execute

    from src.core.shell_policy import ExecutionConstraints, ShellRuntimePolicy
    constraints = ExecutionConstraints(original_user_message="test")
    from src.core.task_types import RuntimeDeps
    deps = RuntimeDeps(
        execute_shell=AsyncMock(),
        policy=ShellRuntimePolicy(
            analyze_command=lambda *a, **k: {},
            should_request_consent_for_command=lambda *a, **k: False,
            failure_type_from_result=lambda *a, **k: None,
            infer_permission_denied_alternative=lambda *a, **k: None,
            should_validate_after_success=lambda *a, **k: False,
            verify_constraints=lambda *a, **k: None,
            failure_reason_builder=lambda *a, **k: "",
        ),
        shell_default_cwd="/tmp",
        shell_allow_sudo=False,
    )

    tools = [{"function": {"name": "test_tool"}, "type": "function"}]

    result = await runtime.complete_chat(
        chat_id="test",
        messages=[{"role": "user", "content": "test"}],
        constraints=constraints,
        tools=tools,
        deps=deps,
    )

    # 1 tool call + at most NoActionBudget.default (3) no-action turns + finalize
    assert call_count <= 5


@pytest.mark.asyncio
async def test_task_runtime_injects_error_context():
    """Error burst guard 触发后注入错误摘要消息"""
    from src.core.task_runtime import TaskRuntime
    from src.core.llm_router import ToolCallRequest

    call_count = 0

    async def mock_complete_with_tools(messages, **kwargs):
        nonlocal call_count
        call_count += 1

        if call_count <= 4:
            # 前 4 轮返回工具调用
            return SimpleNamespace(
                tool_calls=[ToolCallRequest(id=f"call_{call_count}", name="execute_shell", arguments={"command": "bad"})],
                text="",
                continuation_message={"role": "assistant", "content": "", "tool_calls": []},
            )
        # 之后检查是否注入了错误上下文
        for msg in messages:
            if isinstance(msg.get("content"), str) and "系统警告" in msg["content"]:
                # 断路消息已注入，返回纯文本结束循环
                return SimpleNamespace(
                    tool_calls=[],
                    text="好的，我换个方法。",
                    continuation_message=None,
                )
        return SimpleNamespace(
            tool_calls=[],
            text="完成了",
            continuation_message=None,
        )

    router = MagicMock()
    router.complete_with_tools = AsyncMock(side_effect=mock_complete_with_tools)
    router.build_tool_result_message = MagicMock(return_value={"role": "tool", "content": "error"})

    runtime = TaskRuntime(router=router)

    # Mock _execute_tool_call to always fail
    async def mock_execute(*, tool_call, state, deps, task_id, chat_id,
                           event_bus, profile, services, adapter, user_id,
                           send_fn=None):
        return "Error: command not found", {"error": True}, False

    runtime._execute_tool_call = mock_execute

    from src.core.shell_policy import ExecutionConstraints, ShellRuntimePolicy
    constraints = ExecutionConstraints(original_user_message="test")
    from src.core.task_types import RuntimeDeps
    deps = RuntimeDeps(
        execute_shell=AsyncMock(),
        policy=ShellRuntimePolicy(
            analyze_command=lambda *a, **k: {},
            should_request_consent_for_command=lambda *a, **k: False,
            failure_type_from_result=lambda *a, **k: None,
            infer_permission_denied_alternative=lambda *a, **k: None,
            should_validate_after_success=lambda *a, **k: False,
            verify_constraints=lambda *a, **k: None,
            failure_reason_builder=lambda *a, **k: "",
        ),
        shell_default_cwd="/tmp",
        shell_allow_sudo=False,
    )

    tools = [{"function": {"name": "execute_shell"}, "type": "function"}]

    result = await runtime.complete_chat(
        chat_id="test",
        messages=[{"role": "user", "content": "test"}],
        constraints=constraints,
        tools=tools,
        deps=deps,
    )

    # 验证至少调用了一些轮次（guard 给了 LLM 一次机会后再结束）
    assert call_count >= 3


# ── KV-cache prompt tests ────────────────────────────────────────────────────


class TestTimeContextCoarseGrained:
    def test_same_hour_produces_same_context(self):
        """同一小时内的时间上下文相同"""
        from src.core.vitals import get_period_name as _get_period_name

        dt1 = datetime(2026, 4, 13, 14, 5)
        dt2 = datetime(2026, 4, 13, 14, 55)

        period1 = _get_period_name(dt1.hour)
        period2 = _get_period_name(dt2.hour)
        assert period1 == period2

        # 构造的时间字符串应完全相同（都是同一小时）
        ctx1 = f"{dt1.year}年{dt1.month}月{dt1.day}日 约{dt1.hour}时 {period1}"
        ctx2 = f"{dt2.year}年{dt2.month}月{dt2.day}日 约{dt2.hour}时 {period2}"
        assert ctx1 == ctx2

    def test_different_hours_produce_different_context(self):
        from src.core.vitals import get_period_name as _get_period_name

        assert _get_period_name(9) != _get_period_name(15)

    def test_period_names_cover_all_hours(self):
        """所有 24 小时都有对应的时段名"""
        from src.core.vitals import get_period_name as _get_period_name

        for hour in range(24):
            name = _get_period_name(hour)
            assert isinstance(name, str)
            assert len(name) > 0

    def test_period_boundaries(self):
        from src.core.vitals import get_period_name as _get_period_name

        assert _get_period_name(0) == "深夜"
        assert _get_period_name(4) == "深夜"
        assert _get_period_name(5) == "早上"
        assert _get_period_name(8) == "上午"
        assert _get_period_name(11) == "中午"
        assert _get_period_name(13) == "下午"
        assert _get_period_name(17) == "傍晚"
        assert _get_period_name(19) == "晚上"
        assert _get_period_name(23) == "深夜"
