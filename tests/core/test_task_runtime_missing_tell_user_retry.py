"""Regression test: bare text without tell_user gets one retry.

Step 5 design: bare text is inner monologue, never sent to user — only
``tell_user`` calls reach the user. When MiniMax outputs natural-language
filler (e.g. "等我查一下") without calling ``tell_user`` or any tool, the
loop used to terminate silently and the user saw nothing. This test
locks in the recovery behaviour: one retry with a system reminder so
the model can recover and actually speak.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.llm_types import ToolTurnResult
from src.core.shell_policy import (
    ExecutionSessionState,
    extract_execution_constraints,
)
from src.core.task_runtime import TaskRuntime
from src.core.task_types import (
    ErrorBurstGuard,
    LoopRecoveryState,
    NoActionBudget,
    RuntimeDeps,
    ToolLoopContext,
)


def _make_runtime():
    router = MagicMock()
    router.complete_with_tools = AsyncMock()
    return TaskRuntime(router=router), router


def _make_ctx(
    *,
    tools: list[dict],
    send_fn=AsyncMock(),
    has_used_tools: bool = False,
) -> ToolLoopContext:
    constraints = extract_execution_constraints("查一下最近道奇的比赛")
    state = ExecutionSessionState(constraints=constraints)
    deps = RuntimeDeps(
        execute_shell=AsyncMock(),
        policy=SimpleNamespace(),
        shell_default_cwd="/tmp",
        shell_allow_sudo=False,
    )
    profile = SimpleNamespace(
        capabilities=set(),
        tool_names=None,
        shell_default_cwd="/tmp",
        shell_allow_sudo=False,
    )
    return ToolLoopContext(
        messages=[{"role": "user", "content": "查一下最近道奇的比赛"}],
        tools=tools,
        constraints=constraints,
        chat_id="kev",
        task_id="t1",
        deps=deps,
        profile_obj=profile,
        status_callback=None,
        event_bus=None,
        on_consent_required=None,
        on_interim_text=AsyncMock(),
        on_typing=None,
        services={},
        adapter="qq",
        user_id="kev",
        state=state,
        loop_detection_state=None,
        recovery=LoopRecoveryState(),
        no_action_budget=NoActionBudget(default=3, remaining=3),
        error_guard=ErrorBurstGuard(threshold=3),
        send_fn=send_fn,
        has_used_tools=has_used_tools,
    )


def _tools_with(*names: str) -> list[dict]:
    return [{"function": {"name": n}} for n in names]


async def test_retries_once_when_bare_text_and_no_tell_user_call():
    """The exact production failure mode from 2026-04-19 13:05:43."""
    rt, router = _make_runtime()
    router.complete_with_tools.return_value = ToolTurnResult(
        text="等我查一下", tool_calls=[],
    )
    ctx = _make_ctx(tools=_tools_with("tell_user", "research", "browse"))

    step = await rt._run_step(ctx, 0)

    assert step.completed is False, "retry must not complete the loop"
    assert ctx.missing_tell_user_retries == 1
    # A system reminder was appended for the next LLM turn.
    assert ctx.messages[-1]["role"] == "user"
    assert "tell_user" in ctx.messages[-1]["content"]


async def test_retry_cap_is_one_then_finalize():
    rt, router = _make_runtime()
    router.complete_with_tools.return_value = ToolTurnResult(
        text="等我查一下", tool_calls=[],
    )
    ctx = _make_ctx(tools=_tools_with("tell_user", "research"))

    # First call triggers retry.
    step1 = await rt._run_step(ctx, 0)
    assert step1.completed is False
    assert ctx.missing_tell_user_retries == 1

    # Second call: budget exhausted, loop finalizes.
    step2 = await rt._run_step(ctx, 1)
    assert step2.completed is True
    assert ctx.missing_tell_user_retries == 1  # did NOT increment again


async def test_no_retry_when_send_fn_missing():
    """Inner-tick / agent callers have no send_fn — their bare text is
    inner monologue by design, not a failed conversation turn."""
    rt, router = _make_runtime()
    router.complete_with_tools.return_value = ToolTurnResult(
        text="无事 [NEXT: 30m]", tool_calls=[],
    )
    ctx = _make_ctx(
        tools=_tools_with("tell_user", "research"),
        send_fn=None,
    )

    step = await rt._run_step(ctx, 0)

    assert step.completed is True
    assert ctx.missing_tell_user_retries == 0


async def test_no_retry_when_tell_user_not_in_tools():
    """Callers that don't expose tell_user (agent profile) shouldn't be
    told to call a tool they don't have."""
    rt, router = _make_runtime()
    router.complete_with_tools.return_value = ToolTurnResult(
        text="done.", tool_calls=[],
    )
    ctx = _make_ctx(tools=_tools_with("execute_shell"))

    step = await rt._run_step(ctx, 0)

    assert step.completed is True
    assert ctx.missing_tell_user_retries == 0


async def test_no_retry_after_tools_were_used_in_iteration():
    """If the model already used tools this iteration and is now winding
    down with a trailing bare text, the No-Action-Budget path owns that
    case — don't double-handle it."""
    rt, router = _make_runtime()
    router.complete_with_tools.return_value = ToolTurnResult(
        text="done", tool_calls=[],
    )
    ctx = _make_ctx(
        tools=_tools_with("tell_user", "research"),
        has_used_tools=True,
    )

    await rt._run_step(ctx, 0)

    assert ctx.missing_tell_user_retries == 0


async def test_no_retry_when_text_is_empty():
    """Empty response takes the output-recovery branch, not this one."""
    rt, router = _make_runtime()
    router.complete_with_tools.return_value = ToolTurnResult(
        text="", tool_calls=[],
    )
    ctx = _make_ctx(tools=_tools_with("tell_user", "research"))

    await rt._run_step(ctx, 0)

    assert ctx.missing_tell_user_retries == 0
