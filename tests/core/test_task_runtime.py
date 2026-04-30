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
from src.core.shell_policy import ShellRuntimePolicy
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import ShellResult
from src.tools.types import ToolExecutionRequest


@pytest.fixture(autouse=True)
def _disable_no_action_budget():
    """禁用 NoActionBudget 避免测试需要多轮文本响应"""
    import config.settings as _s
    orig = _s.TASK_NO_ACTION_BUDGET
    _s.TASK_NO_ACTION_BUDGET = 0
    yield
    _s.TASK_NO_ACTION_BUDGET = orig


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
    blocking: bool = True,
    history_size: int = 30,
    warning_threshold: int = 10,
    critical_threshold: int = 20,
    global_circuit_breaker_threshold: int = 30,
    detector_generic_repeat: bool = True,
) -> LoopDetectionConfig:
    return LoopDetectionConfig(
        enabled=enabled,
        blocking=blocking,
        history_size=history_size,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        global_circuit_breaker_threshold=global_circuit_breaker_threshold,
        detector_generic_repeat=detector_generic_repeat,
        detector_ping_pong=True,
        detector_known_poll_no_progress=True,
    )


def _chat_ready_registry():
    """Register the full chat tool surface that chat_tools() references.

    Step 1i: chat_tools() now raises if any whitelisted name isn't registered
    (see ToolRegistry.list_tools). AppContainer sets this up in production;
    tests must do the equivalent to exercise chat_tools.
    """
    from src.tools.registry import build_default_tool_registry
    from src.tools.personal_tools import register_personal_tools
    from src.tools.research_tool import register_research_tool
    from src.tools.agent_tools import register_agent_tools
    from src.core.durable_scheduler import DURABLE_SCHEDULER_EXECUTORS
    from src.tools.types import ToolSpec

    registry = build_default_tool_registry()
    register_personal_tools(registry, {})
    register_research_tool(registry)
    register_agent_tools(registry)
    for name in ("set_reminder", "view_reminders", "cancel_reminder"):
        registry.register(ToolSpec(
            name=name,
            description="reminder tool",
            json_schema={"type": "object", "properties": {}},
            executor=DURABLE_SCHEDULER_EXECUTORS[name],
            capability="schedule",
        ))
    return registry


@pytest.mark.asyncio
async def test_chat_tools_from_registry():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=_chat_ready_registry())

    tools = runtime.chat_tools(shell_enabled=True)
    names = {item["function"]["name"] for item in tools}

    # Shell + personal + reminder tools exposed; research goes through
    # delegate_to_researcher (no web layering anymore).
    assert {"execute_shell", "read_file", "write_file"}.issubset(names)
    assert {"get_time", "send_message", "send_image", "view_image"}.issubset(names)
    assert {"set_reminder", "view_reminders", "cancel_reminder"}.issubset(names)
    assert {"delegate_to_researcher", "delegate_to_coder"}.issubset(names)
    # Agents-as-tools refactor: raw research/browse no longer reach the
    # chat surface — every external info question goes via delegate.
    assert "research" not in names
    assert "browse" not in names
    assert "delegate_to_agent" not in names
    assert "list_agents" in names
    # `get_weather` + `image_search` are gone from the whitelist (Step 1i)
    assert "get_weather" not in names
    assert "image_search" not in names


@pytest.mark.asyncio
async def test_chat_tools_no_raw_web_at_chat_tier():
    """research/browse used to layer in via web_enabled=True. After
    the agents-as-tools refactor they're confined to the Researcher;
    the chat surface always reaches them via delegate_to_researcher.
    """
    runtime = TaskRuntime(router=MagicMock(), tool_registry=_chat_ready_registry())

    tools = runtime.chat_tools(shell_enabled=True)
    names = {item["function"]["name"] for item in tools}

    assert "research" not in names
    assert "browse" not in names
    assert "execute_shell" in names
    assert "send_message" in names
    assert "delegate_to_researcher" in names


@pytest.mark.asyncio
async def test_chat_tools_includes_browser_tools_when_enabled():
    """browser_enabled=True exposes browser_open etc. when they're registered."""
    from unittest.mock import AsyncMock as _AM
    from src.tools.browser_tools import register_browser_tools

    registry = _chat_ready_registry()
    register_browser_tools(registry, _AM())  # mock browser_manager
    runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)

    tools = runtime.chat_tools(shell_enabled=False, browser_enabled=True)
    names = {item["function"]["name"] for item in tools}

    assert "browser_open" in names
    assert "browser_click" in names
    assert "browser_type" in names
    assert "browser_login" in names
    assert "browser_scroll" in names


@pytest.mark.asyncio
async def test_chat_tools_excludes_browser_tools_when_disabled():
    """browser_enabled=False (default) hides browser tools even if registered."""
    from unittest.mock import AsyncMock as _AM
    from src.tools.browser_tools import register_browser_tools

    registry = _chat_ready_registry()
    register_browser_tools(registry, _AM())
    runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)

    tools = runtime.chat_tools(shell_enabled=False, browser_enabled=False)
    names = {item["function"]["name"] for item in tools}

    assert "browser_open" not in names
    assert "browser_click" not in names


@pytest.mark.asyncio
async def test_chat_tools_browser_enabled_safe_when_not_registered():
    """browser_enabled=True is safe even if browser tools aren't registered."""
    runtime = TaskRuntime(router=MagicMock(), tool_registry=_chat_ready_registry())

    tools = runtime.chat_tools(shell_enabled=False, browser_enabled=True)
    names = {item["function"]["name"] for item in tools}

    assert "browser_open" not in names
    assert "send_message" in names


@pytest.mark.asyncio
async def test_chat_tools_silently_skips_unregistered_profile_tools():
    """After centralization (commit 7), chat_tools resolves through
    COMPOSE_PROACTIVE_PROFILE which silently filters unregistered tool
    names. Subsystems that aren't wired (e.g. personal_tools without
    register_personal_tools) leave their tools out of the surface
    instead of raising — production callers register on demand and the
    profile is the source of truth."""
    from src.tools.registry import build_default_tool_registry

    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    tools = runtime.chat_tools(shell_enabled=False)
    names = {item["function"]["name"] for item in tools}
    # Tools that ARE registered in build_default_tool_registry survive…
    assert {"commit_promise", "fulfill_promise", "abandon_promise"}.issubset(names)
    assert {"plan_task", "update_plan", "add_correction"}.issubset(names)
    assert {"close_focus", "recall_focus"}.issubset(names)
    # …and tools whose subsystem isn't wired (personal_tools / research
    # / agent_tools / scheduler) are silently skipped, not raised on.
    assert "send_message" not in names
    assert "research" not in names
    assert "delegate_to_researcher" not in names
    assert "set_reminder" not in names


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


def test_format_tool_result_for_llm_truncates_oversized_payload():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    text = runtime._format_tool_result_for_llm(
        tool_name="custom_tool",
        payload={"blob": "x" * 20000},
    )
    # 截断后应该是自然语言收尾，不包含工程化标记
    assert len(text) <= 12100  # _TOOL_RESULT_MAX_CHARS + 余量
    assert "只显示了一部分" in text
    assert "_truncated" not in text
    assert "_original_chars" not in text


@pytest.mark.asyncio
async def test_execute_shell_triggers_verifying_event_and_completion():
    runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
    constraints = extract_execution_constraints(
        "在/home下新建一个Lapwing文件夹，然后在文件夹里面新建一个txt文件"
    )
    constraints.target_directory = "/home/Lapwing"  # LLM 工具参数提供
    constraints.is_write_request = True              # LLM 通过工具选择表达写入意图
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
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry(), no_action_budget=0)
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
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry(), no_action_budget=0)
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
async def test_complete_chat_does_not_emit_final_reply_as_interim_text():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry(), no_action_budget=0)
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
    on_interim_text = AsyncMock()

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "看看当前目录"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=None,
        on_interim_text=on_interim_text,
    )

    assert result == "当前目录是 /tmp。"
    on_interim_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_chat_supports_web_tool_call_and_tool_result_roundtrip():
    """research 工具一轮调用 + tool_result 回传 + 第二轮收尾。"""
    from src.research.types import ResearchResult
    from src.tools.research_tool import register_research_tool

    router = MagicMock()
    registry = _chat_ready_registry()
    runtime = TaskRuntime(router=router, tool_registry=registry, no_action_budget=0)
    constraints = extract_execution_constraints("查一下今天A股收盘")

    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_research_1",
                        name="research",
                        arguments={"question": "今天 A股 收盘"},
                    ),
                ],
                continuation_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call_research_1"}],
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
            "tool_call_id": "call_research_1",
            "name": "research",
            "content": '{"answer": "上证收涨"}',
        }
    )

    fake_engine = MagicMock()
    fake_engine.research = AsyncMock(return_value=ResearchResult(
        answer="上证指数收涨。",
        confidence="high",
    ))
    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "查一下今天A股收盘"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=False),
        deps=RuntimeDeps(
            execute_shell=AsyncMock(),
            policy=_make_policy(AsyncMock()),
            shell_default_cwd="/tmp",
            shell_allow_sudo=True,
        ),
        event_bus=None,
        services={"research_engine": fake_engine},
    )

    assert result == "我查到了并整理好了来源。"
    fake_engine.research.assert_awaited_once_with("今天 A股 收盘", scope="auto")
    assert router.complete_with_tools.await_count == 2
    second_turn_messages = router.complete_with_tools.await_args_list[1].args[0]
    second_turn_tool_messages = [
        message for message in second_turn_messages if message.get("role") == "tool"
    ]
    assert [item["tool_call_id"] for item in second_turn_tool_messages] == ["call_research_1"]


@pytest.mark.asyncio
async def test_complete_chat_emits_tool_execution_events_for_successful_call():
    router = MagicMock()
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry(), no_action_budget=0)
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
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry(), no_action_budget=0)
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
            SimpleNamespace(text="命令执行失败了", tool_calls=[], continuation_message=None),
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

    assert "命令执行失败了" in result
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
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry(), no_action_budget=0)
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
        tool_registry=_chat_ready_registry(),
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


def test_loop_detection_blocking_flag_disables_global_breaker():
    runtime = TaskRuntime(
        router=MagicMock(),
        tool_registry=_chat_ready_registry(),
        loop_detection_config=_loop_config(
            enabled=True,
            blocking=False,
            warning_threshold=1,
            global_circuit_breaker_threshold=1,
        ),
    )
    assert runtime._should_emit_generic_repeat_warning(1) is True
    assert runtime._should_block_by_global_circuit_breaker(1) is False


@pytest.mark.asyncio
async def test_loop_detection_global_breaker_blocks_before_executing_threshold_call():
    router = MagicMock()
    runtime = TaskRuntime(
        router=router,
        tool_registry=_chat_ready_registry(),
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
        tool_registry=_chat_ready_registry(),
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
        tool_registry=_chat_ready_registry(),
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
        tool_registry=_chat_ready_registry(),
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


@pytest.mark.asyncio
async def test_on_circuit_breaker_open_called_when_breaker_fires():
    """全局断路器触发时，on_circuit_breaker_open 回调应被调用，且不影响断路行为。"""
    router = MagicMock()
    callback = MagicMock()
    runtime = TaskRuntime(
        router=router,
        tool_registry=_chat_ready_registry(),
        loop_detection_config=_loop_config(
            enabled=True,
            history_size=10,
            warning_threshold=1,
            critical_threshold=2,
            global_circuit_breaker_threshold=3,
        ),
        on_circuit_breaker_open=callback,
    )
    constraints = extract_execution_constraints("一直执行 pwd")
    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[ToolCallRequest(id="c1", name="execute_shell", arguments={"command": "pwd"})],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[ToolCallRequest(id="c2", name="execute_shell", arguments={"command": "pwd"})],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[ToolCallRequest(id="c3", name="execute_shell", arguments={"command": "pwd"})],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "c3"}]},
            ),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={"role": "tool", "tool_call_id": "c1", "name": "execute_shell", "content": "{}"}
    )
    deps = RuntimeDeps(
        execute_shell=AsyncMock(
            side_effect=[
                ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
                ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ]
        ),
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )

    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "一直执行 pwd"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=SimpleNamespace(publish=AsyncMock()),
    )

    # 断路器触发，返回需用户介入的消息
    assert "需用户介入" in result
    # 回调应被调用一次，参数为触发工具名和重复次数
    callback.assert_called_once_with("execute_shell", 3)


@pytest.mark.asyncio
async def test_on_circuit_breaker_open_exception_does_not_break_circuit_behavior():
    """断路器回调抛出异常时，断路器行为不受影响。"""
    router = MagicMock()

    def _raising_callback(tool_name, repeat_count):
        raise RuntimeError("回调异常")

    runtime = TaskRuntime(
        router=router,
        tool_registry=_chat_ready_registry(),
        loop_detection_config=_loop_config(
            enabled=True,
            history_size=10,
            warning_threshold=1,
            critical_threshold=2,
            global_circuit_breaker_threshold=3,
        ),
        on_circuit_breaker_open=_raising_callback,
    )
    constraints = extract_execution_constraints("一直执行 pwd")
    router.complete_with_tools = AsyncMock(
        side_effect=[
            SimpleNamespace(
                text="",
                tool_calls=[ToolCallRequest(id="c1", name="execute_shell", arguments={"command": "pwd"})],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[ToolCallRequest(id="c2", name="execute_shell", arguments={"command": "pwd"})],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
            ),
            SimpleNamespace(
                text="",
                tool_calls=[ToolCallRequest(id="c3", name="execute_shell", arguments={"command": "pwd"})],
                continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "c3"}]},
            ),
        ]
    )
    router.build_tool_result_message = MagicMock(
        return_value={"role": "tool", "tool_call_id": "c1", "name": "execute_shell", "content": "{}"}
    )
    deps = RuntimeDeps(
        execute_shell=AsyncMock(
            side_effect=[
                ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
                ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
            ]
        ),
        policy=_make_policy(AsyncMock()),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )

    # 即使回调抛异常，complete_chat 也应正常返回断路消息
    result = await runtime.complete_chat(
        chat_id="chat_1",
        messages=[{"role": "user", "content": "一直执行 pwd"}],
        constraints=constraints,
        tools=runtime.chat_tools(shell_enabled=True),
        deps=deps,
        event_bus=SimpleNamespace(publish=AsyncMock()),
    )
    assert "需用户介入" in result


# Note: the current-info gate (TestCurrentInfoFallback +
# test_current_info_gate_*) was removed in the agents-as-tools refactor.
# Real-time info now goes through delegate_to_researcher; the
# Researcher decides which retrieval API to call. The brittle gate
# that overwrote model replies based on required_tool_names /
# current_info_domain is no longer needed and has been deleted along
# with the underlying RuntimeOptions fields.
