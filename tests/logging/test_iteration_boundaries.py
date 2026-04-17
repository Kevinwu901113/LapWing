"""Verify TaskRuntime.complete_chat records ITERATION_STARTED + ITERATION_ENDED.

Step 1d of Blueprint v2.0. complete_chat() wraps a single iteration at the
current scope; Step 4 will reshape this when the main loop lands.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.shell_policy import (
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
from src.logging.state_mutation_log import MutationType, StateMutationLog
from src.tools.registry import build_default_tool_registry


def _make_policy():
    return ShellRuntimePolicy(
        analyze_command=analyze_command,
        should_request_consent_for_command=should_request_consent_for_command,
        failure_type_from_result=failure_type_from_result,
        infer_permission_denied_alternative=infer_permission_denied_alternative,
        should_validate_after_success=should_validate_after_success,
        verify_constraints=AsyncMock(),
        failure_reason_builder=failure_reason_from_result,
    )


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(tmp_path / "ml.db", logs_dir=tmp_path / "logs")
    await log.init()
    yield log
    await log.close()


async def test_complete_chat_records_iteration_pair_on_success(mutation_log):
    router = MagicMock()
    router.complete = AsyncMock(return_value="fine thanks")
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("hi")
    deps = RuntimeDeps(
        execute_shell=AsyncMock(),
        policy=_make_policy(),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    services = {"mutation_log": mutation_log}

    reply = await runtime.complete_chat(
        chat_id="chat-1",
        messages=[{"role": "user", "content": "hi"}],
        constraints=constraints,
        tools=[],  # triggers the no-tool early-return path
        deps=deps,
        services=services,
        adapter="telegram",
        user_id="u-1",
    )
    assert reply == "fine thanks"

    starts = await mutation_log.query_by_type(MutationType.ITERATION_STARTED)
    ends = await mutation_log.query_by_type(MutationType.ITERATION_ENDED)
    assert len(starts) == 1 and len(ends) == 1

    start_payload = starts[0].payload
    end_payload = ends[0].payload
    assert start_payload["iteration_id"] == end_payload["iteration_id"]
    assert start_payload["trigger_type"] == "user_message"
    assert start_payload["trigger_detail"]["adapter"] == "telegram"
    assert start_payload["trigger_detail"]["user_id"] == "u-1"

    assert end_payload["end_reason"] == "completed"
    assert end_payload["duration_ms"] >= 0
    # No LLM/tool records on the no-tools path (router.complete bypasses _tracked_call
    # because router has no mutation_log installed here), so counts should be 0
    assert end_payload["llm_calls_count"] == 0
    assert end_payload["tool_calls_count"] == 0


async def test_complete_chat_records_iteration_ended_error_on_exception(mutation_log):
    router = MagicMock()
    router.complete = AsyncMock(side_effect=RuntimeError("network down"))
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("hi")
    deps = RuntimeDeps(
        execute_shell=AsyncMock(),
        policy=_make_policy(),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    services = {"mutation_log": mutation_log}

    with pytest.raises(RuntimeError, match="network down"):
        await runtime.complete_chat(
            chat_id="chat-1",
            messages=[{"role": "user", "content": "hi"}],
            constraints=constraints,
            tools=[],
            deps=deps,
            services=services,
            adapter="telegram",
        )

    starts = await mutation_log.query_by_type(MutationType.ITERATION_STARTED)
    ends = await mutation_log.query_by_type(MutationType.ITERATION_ENDED)
    assert len(starts) == 1
    assert len(ends) == 1
    assert ends[0].payload["end_reason"] == "error"
