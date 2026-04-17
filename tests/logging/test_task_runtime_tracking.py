"""Verify TaskRuntime._execute_tool_call records TOOL_CALLED + TOOL_RESULT.

Step 1c of Blueprint v2.0. Mutation log records run alongside the existing
dispatcher pub/sub — they don't replace it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.llm_router import ToolCallRequest
from src.core.shell_policy import (
    ExecutionSessionState,
    ShellRuntimePolicy,
    analyze_command,
    extract_execution_constraints,
    failure_reason_from_result,
    failure_type_from_result,
    infer_permission_denied_alternative,
    should_request_consent_for_command,
    should_validate_after_success,
)
from src.core.task_runtime import TaskRuntime
from src.core.task_types import RuntimeDeps
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    iteration_context,
)
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import ShellResult


def _make_policy(verify_constraints):
    return ShellRuntimePolicy(
        analyze_command=analyze_command,
        should_request_consent_for_command=should_request_consent_for_command,
        failure_type_from_result=failure_type_from_result,
        infer_permission_denied_alternative=infer_permission_denied_alternative,
        should_validate_after_success=should_validate_after_success,
        verify_constraints=verify_constraints,
        failure_reason_builder=failure_reason_from_result,
    )


@pytest.fixture(autouse=True)
def _disable_no_action_budget():
    """Silence NoActionBudget so single-shot tests don't trip it."""
    import config.settings as _s
    orig = _s.TASK_NO_ACTION_BUDGET
    _s.TASK_NO_ACTION_BUDGET = 0
    yield
    _s.TASK_NO_ACTION_BUDGET = orig


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(tmp_path / "ml.db", logs_dir=tmp_path / "logs")
    await log.init()
    yield log
    await log.close()


async def test_tool_called_and_result_recorded(mutation_log):
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    state = ExecutionSessionState(constraints=extract_execution_constraints("read file"))
    mock_shell = AsyncMock(
        return_value=ShellResult(stdout="ok", stderr="", return_code=0, cwd="/tmp")
    )
    deps = RuntimeDeps(
        execute_shell=mock_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    services = {"mutation_log": mutation_log}

    with iteration_context("iter-xyz", chat_id="chat-abc"):
        await runtime._execute_tool_call(
            tool_call=ToolCallRequest(
                id="call_42",
                name="read_file",
                arguments={"path": "/tmp/a.txt"},
            ),
            state=state,
            deps=deps,
            task_id="task_1",
            chat_id="chat-abc",
            event_bus=None,
            services=services,
        )

    called = await mutation_log.query_by_type(MutationType.TOOL_CALLED)
    results = await mutation_log.query_by_type(MutationType.TOOL_RESULT)
    assert len(called) == 1 and len(results) == 1

    c = called[0].payload
    assert c["tool_name"] == "read_file"
    assert c["tool_call_id"] == "call_42"
    assert c["arguments"] == {"path": "/tmp/a.txt"}
    assert c["called_from_iteration"] == "iter-xyz"
    assert c["task_id"] == "task_1"
    assert called[0].iteration_id == "iter-xyz"
    assert called[0].chat_id == "chat-abc"

    r = results[0].payload
    assert r["tool_call_id"] == "call_42"
    assert r["tool_name"] == "read_file"
    assert r["success"] is True
    assert r["is_error"] is False
    assert r["elapsed_ms"] >= 0


async def test_absence_of_mutation_log_does_not_break_tool_execution(mutation_log):
    """If services lacks `mutation_log`, tool execution still runs."""
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    state = ExecutionSessionState(constraints=extract_execution_constraints("read file"))
    mock_shell = AsyncMock(
        return_value=ShellResult(stdout="ok", stderr="", return_code=0, cwd="/tmp")
    )
    deps = RuntimeDeps(
        execute_shell=mock_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )

    _, payload, success = await runtime._execute_tool_call(
        tool_call=ToolCallRequest(id="c", name="read_file", arguments={"path": "/x"}),
        state=state,
        deps=deps,
        task_id="t",
        chat_id="c",
        event_bus=None,
        services={},  # no mutation_log
    )
    assert success is True
    # The test-local mutation_log never received anything
    assert await mutation_log.query_by_type(MutationType.TOOL_CALLED) == []
