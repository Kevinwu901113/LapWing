"""TaskRuntime 的 registry + policy 集成测试。"""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

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
from src.core.task_runtime import LoopDetectionConfig, RuntimeDeps, TaskRuntime
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


def _args_hash(arguments: dict) -> str:
    canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _loop_config(
    *,
    enabled: bool,
    history_size: int = 30,
    warning_threshold: int = 10,
    critical_threshold: int = 20,
    global_circuit_breaker_threshold: int = 30,
    detector_generic_repeat: bool = True,
) -> LoopDetectionConfig:
    return LoopDetectionConfig(
        enabled=enabled,
        history_size=history_size,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        global_circuit_breaker_threshold=global_circuit_breaker_threshold,
        detector_generic_repeat=detector_generic_repeat,
        detector_ping_pong=True,
        detector_known_poll_no_progress=True,
    )


@pytest.mark.asyncio
async def test_chat_tools_from_registry():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())

    tools = runtime.chat_tools(shell_enabled=True)
    names = {item["function"]["name"] for item in tools}

    assert names == {"execute_shell", "read_file", "write_file", "web_search", "web_fetch", "memory_note"}


@pytest.mark.asyncio
async def test_chat_tools_excludes_web_when_disabled():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())

    tools = runtime.chat_tools(shell_enabled=True, web_enabled=False)
    names = {item["function"]["name"] for item in tools}

    assert names == {"execute_shell", "read_file", "write_file", "memory_note"}


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

    _, payload, _ = await runtime._execute_tool_call(
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

    _, payload, _ = await runtime._execute_tool_call(
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


@pytest.mark.asyncio
async def test_complete_chat_executes_multiple_tool_calls_in_one_turn_serially():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("看看当前目录和用户")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                    ToolCallRequest(
                        id="call_2",
                        name="execute_shell",
                        arguments={"command": "whoami"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}, {"id": "call_2"}],
                },
            ),
            SimpleNamespace(
                text="命令都执行完了。",
                tool_calls=[],
                continuation_message=None,
            ),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value=[
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "execute_shell",
                "content": '{"stdout": "/tmp"}',
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "name": "execute_shell",
                "content": '{"stdout": "kevin"}',
            },
        ]
    )

    mock_execute_shell = AsyncMock(
        side_effect=[
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="kevin\n", stderr="", return_code=0, cwd="/tmp"),
        ]
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "看看当前目录和用户"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=None,
    )

    assert result == "命令都执行完了。"
    assert router.complete_with_tools.await_count == 2
    mock_execute_shell.assert_has_awaits([call("pwd"), call("whoami")])
    router.build_tool_result_message.assert_called_once()
    tool_results = router.build_tool_result_message.call_args.kwargs["tool_results"]
    assert [item[0].id for item in tool_results] == ["call_1", "call_2"]

    second_turn_messages = router.complete_with_tools.await_args_list[1].args[0]
    second_turn_tool_messages = [
        message for message in second_turn_messages if message.get("role") == "tool"
    ]
    assert [item["tool_call_id"] for item in second_turn_tool_messages] == ["call_1", "call_2"]


@pytest.mark.asyncio
async def test_complete_chat_status_callback_uses_stage_messages():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("看看当前目录")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}],
                },
            ),
            SimpleNamespace(
                text="当前目录是 /tmp。",
                tool_calls=[],
                continuation_message=None,
            ),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }
    )

    deps = RuntimeDeps(
        execute_shell=AsyncMock(return_value=ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp")),
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    status_callback = AsyncMock()

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "看看当前目录"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        status_callback=status_callback,
        event_bus=None,
    )

    assert result == "当前目录是 /tmp。"
    status_texts = [call.args[1] for call in status_callback.await_args_list]
    assert status_texts[0] == "stage:planning"
    assert "stage:executing:execute_shell:1:1" in status_texts
    assert status_texts[-1] == "stage:finalizing"


@pytest.mark.asyncio
async def test_complete_chat_supports_web_tool_call_and_tool_result_roundtrip():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("查一下今天A股收盘")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_web_1",
                        name="web_search",
                        arguments={"query": "今天 A股 收盘", "max_results": 3},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_web_1"}],
                },
            ),
            SimpleNamespace(
                text="我查到了并整理好了来源。",
                tool_calls=[],
                continuation_message=None,
            ),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_web_1",
            "name": "web_search",
            "content": '{"count": 1}',
        }
    )

    with patch("src.tools.registry.web_search.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            {
                "title": "A股收盘快讯",
                "url": "https://finance.example/a-share-close",
                "snippet": "上证指数收盘上涨。",
            }
        ]
        result = await runtime.complete_chat(
            chat_id="chat_1",
            messages=[{"role": "user", "content": "查一下今天A股收盘"}],
            constraints=constraints,
            tools=runtime.chat_tools(shell_enabled=False, web_enabled=True),
            deps=RuntimeDeps(
                execute_shell=AsyncMock(),
                policy=_make_policy(AsyncMock()),
                shell_default_cwd="/tmp",
                shell_allow_sudo=True,
            ),
            event_bus=None,
        )

    assert result == "我查到了并整理好了来源。"
    mock_search.assert_awaited_once_with("今天 A股 收盘", max_results=3)
    assert router.complete_with_tools.await_count == 2
    second_turn_messages = router.complete_with_tools.await_args_list[1].args[0]
    second_turn_tool_messages = [
        message for message in second_turn_messages if message.get("role") == "tool"
    ]
    assert [item["tool_call_id"] for item in second_turn_tool_messages] == ["call_web_1"]


@pytest.mark.asyncio
async def test_complete_chat_emits_tool_execution_events_for_successful_call():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("看看当前目录")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}],
                },
            ),
            SimpleNamespace(text="当前目录是 /tmp。", tool_calls=[], continuation_message=None),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }
    )
    mock_execute_shell = AsyncMock(
        return_value=ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp")
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "看看当前目录"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=event_bus,
    )

    assert result == "当前目录是 /tmp。"
    event_calls = event_bus.publish.await_args_list
    event_types = [event_call.args[0] for event_call in event_calls]
    assert event_types[:6] == [
        "task.started",
        "task.planning",
        "task.executing",
        "task.tool_execution_start",
        "task.tool_execution_update",
        "task.tool_execution_end",
    ]
    assert event_types[-1] == "task.completed"

    end_payload = next(
        event_call.args[1]
        for event_call in event_calls
        if event_call.args[0] == "task.tool_execution_end"
    )
    assert end_payload["toolCallId"] == "call_1"
    assert end_payload["toolName"] == "execute_shell"
    assert end_payload["argsHash"] == _args_hash({"command": "pwd"})
    assert end_payload["stdoutBytes"] == len("/tmp\n".encode("utf-8"))
    assert end_payload["stderrBytes"] == 0
    assert end_payload["isError"] is False
    assert end_payload["durationMs"] >= 0


@pytest.mark.asyncio
async def test_complete_chat_emits_tool_execution_error_metrics_on_failure():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("执行失败命令")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "cat /not-found"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}],
                },
            ),
            SimpleNamespace(text="", tool_calls=[], continuation_message=None),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stderr": "not found"}',
        }
    )
    mock_execute_shell = AsyncMock(
        return_value=ShellResult(
            stdout="",
            stderr="cat: /not-found: No such file or directory\n",
            return_code=1,
            cwd="/tmp",
        )
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "执行失败命令"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=event_bus,
    )

    assert "退出码 1" in result
    event_calls = event_bus.publish.await_args_list
    event_types = [event_call.args[0] for event_call in event_calls]
    assert "task.tool_execution_end" in event_types
    assert "task.failed" in event_types

    end_payload = next(
        event_call.args[1]
        for event_call in event_calls
        if event_call.args[0] == "task.tool_execution_end"
    )
    assert end_payload["toolCallId"] == "call_1"
    assert end_payload["isError"] is True
    assert end_payload["stderrBytes"] > 0
    assert end_payload["durationMs"] >= 0


@pytest.mark.asyncio
async def test_complete_chat_emits_tool_execution_events_for_each_tool_call():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints("看看当前目录和用户")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                    ToolCallRequest(
                        id="call_2",
                        name="execute_shell",
                        arguments={"command": "whoami"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}, {"id": "call_2"}],
                },
            ),
            SimpleNamespace(text="命令都执行完了。", tool_calls=[], continuation_message=None),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value=[
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "execute_shell",
                "content": '{"stdout": "/tmp"}',
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "name": "execute_shell",
                "content": '{"stdout": "kevin"}',
            },
        ]
    )
    mock_execute_shell = AsyncMock(
        side_effect=[
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="kevin\n", stderr="", return_code=0, cwd="/tmp"),
        ]
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "看看当前目录和用户"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=event_bus,
    )

    assert result == "命令都执行完了。"
    event_calls = event_bus.publish.await_args_list
    start_payloads = [
        event_call.args[1]
        for event_call in event_calls
        if event_call.args[0] == "task.tool_execution_start"
    ]
    end_payloads = [
        event_call.args[1]
        for event_call in event_calls
        if event_call.args[0] == "task.tool_execution_end"
    ]
    assert len(start_payloads) == 2
    assert len(end_payloads) == 2
    assert [item["toolCallId"] for item in start_payloads] == ["call_1", "call_2"]
    assert [item["toolCallId"] for item in end_payloads] == ["call_1", "call_2"]
    assert [item["argsHash"] for item in end_payloads] == [
        _args_hash({"command": "pwd"}),
        _args_hash({"command": "whoami"}),
    ]


@pytest.mark.asyncio
async def test_loop_detection_generic_repeat_warns_without_blocking():
    router = MagicMock()
    runtime = TaskRuntime(
        router=router,
        tool_registry=build_default_tool_registry(),
        loop_detection_config=_loop_config(
            enabled=True,
            history_size=10,
            warning_threshold=2,
            critical_threshold=3,
            global_circuit_breaker_threshold=5,
        ),
    )
    constraints = extract_execution_constraints("连续执行两次 pwd")
    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}],
                },
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_2",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_2"}],
                },
            ),
            SimpleNamespace(text="执行完成。", tool_calls=[], continuation_message=None),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }
    )
    mock_execute_shell = AsyncMock(
        side_effect=[
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
        ]
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "连续执行两次 pwd"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=event_bus,
    )

    assert result == "执行完成。"
    warning_events = [
        event_call.args[1]
        for event_call in event_bus.publish.await_args_list
        if event_call.args[0] == "task.executing"
        and event_call.args[1].get("loop_detection_warning") is True
    ]
    assert len(warning_events) >= 1
    assert warning_events[0]["loop_detection_repeat_count"] >= 2
    assert warning_events[0]["loop_detection_detector"] == "genericRepeat"
    assert mock_execute_shell.await_count == 2


@pytest.mark.asyncio
async def test_loop_detection_global_breaker_blocks_before_executing_threshold_call():
    router = MagicMock()
    runtime = TaskRuntime(
        router=router,
        tool_registry=build_default_tool_registry(),
        loop_detection_config=_loop_config(
            enabled=True,
            history_size=10,
            warning_threshold=1,
            critical_threshold=2,
            global_circuit_breaker_threshold=3,
        ),
    )
    constraints = extract_execution_constraints("一直执行 pwd")
    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}],
                },
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_2",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_2"}],
                },
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_3",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_3"}],
                },
            ),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }
    )
    mock_execute_shell = AsyncMock(
        side_effect=[
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
        ]
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "一直执行 pwd"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=event_bus,
    )

    assert "需用户介入" in result
    assert mock_execute_shell.await_count == 2
    assert router.complete_with_tools.await_count == 3
    blocked_events = [
        event_call.args[1]
        for event_call in event_bus.publish.await_args_list
        if event_call.args[0] == "task.blocked"
        and "全局断路器" in str(event_call.args[1].get("reason", ""))
    ]
    assert len(blocked_events) == 1
    assert blocked_events[0]["loop_detection_repeat_count"] == 3


@pytest.mark.asyncio
async def test_loop_detection_non_consecutive_repeat_resets_counter():
    router = MagicMock()
    runtime = TaskRuntime(
        router=router,
        tool_registry=build_default_tool_registry(),
        loop_detection_config=_loop_config(
            enabled=True,
            history_size=10,
            warning_threshold=2,
            critical_threshold=3,
            global_circuit_breaker_threshold=6,
        ),
    )
    constraints = extract_execution_constraints("按顺序执行 pwd、pwd、whoami、pwd")
    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_2",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_2"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_3",
                        name="execute_shell",
                        arguments={"command": "whoami"},
                    ),
                ],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_3"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_4",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_4"}]},
            ),
            SimpleNamespace(text="执行完成。", tool_calls=[], continuation_message=None),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }
    )
    mock_execute_shell = AsyncMock(
        side_effect=[
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="kevin\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
        ]
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "按顺序执行 pwd、pwd、whoami、pwd"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=event_bus,
    )

    assert result == "执行完成。"
    warning_events = [
        event_call.args[1]
        for event_call in event_bus.publish.await_args_list
        if event_call.args[0] == "task.executing"
        and event_call.args[1].get("loop_detection_warning") is True
    ]
    assert len(warning_events) == 1
    assert warning_events[0]["loop_detection_repeat_count"] == 2
    assert mock_execute_shell.await_count == 4


@pytest.mark.asyncio
async def test_loop_detection_disabled_does_not_warn_or_block():
    router = MagicMock()
    runtime = TaskRuntime(
        router=router,
        tool_registry=build_default_tool_registry(),
        loop_detection_config=_loop_config(
            enabled=False,
            history_size=10,
            warning_threshold=1,
            critical_threshold=2,
            global_circuit_breaker_threshold=3,
        ),
    )
    constraints = extract_execution_constraints("连续执行三次 pwd")
    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_2",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_2"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_3",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_3"}]},
            ),
            SimpleNamespace(text="执行完成。", tool_calls=[], continuation_message=None),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }
    )
    mock_execute_shell = AsyncMock(
        side_effect=[
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
        ]
    )
    deps = RuntimeDeps(
        execute_shell=mock_execute_shell,
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "连续执行三次 pwd"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=event_bus,
    )

    assert result == "执行完成。"
    warning_events = [
        event_call
        for event_call in event_bus.publish.await_args_list
        if event_call.args[0] == "task.executing"
        and event_call.args[1].get("loop_detection_warning") is True
    ]
    loop_blocked_events = [
        event_call
        for event_call in event_bus.publish.await_args_list
        if event_call.args[0] == "task.blocked"
        and "全局断路器" in str(event_call.args[1].get("reason", ""))
    ]
    assert len(warning_events) == 0
    assert len(loop_blocked_events) == 0
    assert mock_execute_shell.await_count == 3


@pytest.mark.asyncio
async def test_complete_chat_records_tool_loop_round_latency():
    router = MagicMock()
    latency_monitor = MagicMock()
    runtime = TaskRuntime(
        router=router,
        tool_registry=build_default_tool_registry(),
        latency_monitor=latency_monitor,
    )
    constraints = extract_execution_constraints("看看当前目录")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="execute_shell",
                        arguments={"command": "pwd"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_1"}],
                },
            ),
            SimpleNamespace(text="当前目录是 /tmp。", tool_calls=[], continuation_message=None),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }
    )

    deps = RuntimeDeps(
        execute_shell=AsyncMock(
            return_value=ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp")
        ),
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "看看当前目录"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=None,
    )

    assert result == "当前目录是 /tmp。"
    latency_monitor.record_tool_loop_round.assert_called_once()
    kwargs = latency_monitor.record_tool_loop_round.call_args.kwargs
    assert kwargs["bucket"] == "shell_local"
    assert kwargs["duration_ms"] >= 0
