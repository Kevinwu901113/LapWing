"""TaskRuntime 的 registry + policy 集成测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.llm_router import ToolCallRequest
from src.core.shell_policy import (
    ExecutionSessionState,
    VerificationStatus,
    analyze_command,
    extract_execution_constraints,
    failure_reason_from_result,
    failure_type_from_result,
    infer_permission_denied_alternative,
    should_request_consent_for_command,
    should_validate_after_success,
)
from src.core.task_runtime import RuntimeDeps, TaskRuntime
from src.policy.shell_runtime_policy import ShellRuntimePolicy
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import ShellResult
from src.tools.types import ToolExecutionRequest


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


@pytest.mark.asyncio
async def test_chat_tools_from_registry():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())

    tools = runtime.chat_tools(shell_enabled=True)
    names = {item["function"]["name"] for item in tools}

    assert names == {"execute_shell", "read_file", "write_file"}


@pytest.mark.asyncio
async def test_tools_for_profile_hides_internal_verify_tools():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    tools = runtime.tools_for_profile("coder_snippet")
    names = {item["function"]["name"] for item in tools}
    assert "run_python_code" in names
    assert "verify_code_result" not in names


@pytest.mark.asyncio
async def test_execute_tool_rejects_tool_outside_profile():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    result = await runtime.execute_tool(
        request=ToolExecutionRequest(name="execute_shell", arguments={"command": "pwd"}),
        profile="file_ops",
    )
    assert result.success is False
    assert result.payload["blocked"] is True


@pytest.mark.asyncio
async def test_execute_tool_allows_internal_verify_tool_for_coder_profile():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    result = await runtime.execute_tool(
        request=ToolExecutionRequest(
            name="verify_code_result",
            arguments={
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
            },
        ),
        profile="coder_snippet",
    )
    assert result.success is True
    assert result.payload["passed"] is True


@pytest.mark.asyncio
async def test_execute_tool_call_read_file_payload_compatible():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("读取文件")
    state = ExecutionSessionState(constraints=constraints)
    mock_execute_shell = AsyncMock(
        return_value=ShellResult(stdout="hello", stderr="", return_code=0, cwd="/tmp")
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )

    _, payload = await runtime._execute_tool_call(
        tool_call=ToolCallRequest(
            id="call_1",
            name="read_file",
            arguments={"path": "/tmp/a.txt"},
        ),
        state=state,
        deps=deps,
        task_id="task_1",
        chat_id="chat_1",
        event_bus=None,
    )

    assert payload["path"] == "/tmp/a.txt"
    assert payload["return_code"] == 0
    mock_execute_shell.assert_awaited_once_with("cat /tmp/a.txt")


@pytest.mark.asyncio
async def test_execute_shell_triggers_verifying_event_and_completion():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints(
        "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
    )
    state = ExecutionSessionState(constraints=constraints)
    mock_execute_shell = AsyncMock(
        return_value=ShellResult(stdout="", stderr="", return_code=0, cwd="/tmp")
    )
    verify_mock = MagicMock(
        return_value=VerificationStatus(
            completed=True,
            directory_path="/home/Lapwing",
            file_path="/home/Lapwing/note.txt",
            file_content="hello",
        )
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(verify_mock),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    _, payload = await runtime._execute_tool_call(
        tool_call=ToolCallRequest(
            id="call_1",
            name="execute_shell",
            arguments={
                "command": "mkdir -p /home/Lapwing && printf 'hello\\n' > /home/Lapwing/note.txt"
            },
        ),
        state=state,
        deps=deps,
        task_id="task_1",
        chat_id="chat_1",
        event_bus=event_bus,
    )

    assert payload["return_code"] == 0
    assert state.completed is True
    event_types = [call.args[0] for call in event_bus.publish.await_args_list]
    assert event_types == ["task.verifying"]
