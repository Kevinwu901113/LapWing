"""_extract_user_visible() 单元测试 + 中间轮次过滤集成测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.task_runtime import (
    _extract_user_visible,
    RuntimeDeps,
    TaskRuntime,
)
from src.core.llm_router import ToolCallRequest
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
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import ShellResult


def _chat_ready_registry():
    """See tests/core/test_task_runtime.py — registers the full chat surface."""
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


@pytest.fixture(autouse=True)
def _disable_no_action_budget():
    """禁用 NoActionBudget 避免测试需要多轮文本响应"""
    import config.settings as _s
    orig = _s.TASK_NO_ACTION_BUDGET
    _s.TASK_NO_ACTION_BUDGET = 0
    yield
    _s.TASK_NO_ACTION_BUDGET = orig


# ── 单元测试：_extract_user_visible ──────────────────────────


def test_no_tag_returns_empty():
    assert _extract_user_visible("等我查一下") == ""


def test_single_tag():
    assert _extract_user_visible("<user_visible>等一下</user_visible>") == "等一下"


def test_tag_with_surrounding_text():
    text = "让我看看 <user_visible>等一下</user_visible> 然后执行搜索"
    assert _extract_user_visible(text) == "等一下"


def test_multiple_tags():
    text = "<user_visible>先查天气</user_visible> 内部思考 <user_visible>再看日程</user_visible>"
    assert _extract_user_visible(text) == "先查天气\n再看日程"


def test_empty_tag():
    assert _extract_user_visible("<user_visible></user_visible>") == ""
    assert _extract_user_visible("<user_visible>   </user_visible>") == ""


def test_nested_content_preserved():
    text = "<user_visible>道奇的比赛我帮你查一下</user_visible>"
    assert _extract_user_visible(text) == "道奇的比赛我帮你查一下"


def test_json_without_tag_filtered():
    """模拟工具调用 JSON 泄露场景"""
    text = '{"tool": "web_search", "args": {"query": "Dodgers schedule"}}'
    assert _extract_user_visible(text) == ""


def test_code_without_tag_filtered():
    """模拟源码泄露场景"""
    text = "import asyncio\nimport time\n\nasync def execute():\n    pass"
    assert _extract_user_visible(text) == ""


def test_multiline_content_in_tag():
    text = "<user_visible>等我\n查一下</user_visible>"
    assert _extract_user_visible(text) == "等我\n查一下"


# ── 集成测试：中间轮次过滤 ───────────────────────────────────


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


def _make_deps():
    return RuntimeDeps(
        execute_shell=AsyncMock(
            return_value=ShellResult(stdout="/tmp\n", stderr="", return_code=0, cwd="/tmp"),
        ),
        policy=_make_policy(),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )


def _make_router():
    router = MagicMock()
    router.build_tool_result_message = MagicMock(
        return_value=[{
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "execute_shell",
            "content": '{"stdout": "/tmp"}',
        }]
    )
    return router


@pytest.mark.asyncio
async def test_interim_text_with_user_visible_tag_sent():
    """中间轮次有 <user_visible> 标签 → on_interim_text 只收到标签内容"""
    router = _make_router()
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry())

    router.complete_with_tools = AsyncMock(side_effect=[
        SimpleNamespace(
            text='内部思考 <user_visible>等我看看</user_visible> 还有工具参数',
            tool_calls=[
                ToolCallRequest(id="call_1", name="execute_shell", arguments={"command": "pwd"}),
            ],
            continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        ),
        SimpleNamespace(text="结果是 /tmp", tool_calls=[], continuation_message=None),
    ])

    on_interim = AsyncMock()
    result = await runtime.complete_chat(
        chat_id="test",
        messages=[{"role": "user", "content": "看看当前目录"}],
        constraints=extract_execution_constraints("看看当前目录"),
        tools=runtime.chat_tools(shell_enabled=True),
        deps=_make_deps(),
        event_bus=None,
        on_interim_text=on_interim,
    )

    # 第一次调用：中间轮次 → 只有 "等我看看"
    # 第二次调用：最终轮次 → "结果是 /tmp"
    assert on_interim.await_count == 2
    assert on_interim.await_args_list[0].args[0] == "等我看看"
    assert on_interim.await_args_list[1].args[0] == "结果是 /tmp"


@pytest.mark.asyncio
async def test_interim_text_without_tag_not_sent():
    """中间轮次没有 <user_visible> 标签 → on_interim_text 不被调用（中间轮次部分）"""
    router = _make_router()
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry())

    router.complete_with_tools = AsyncMock(side_effect=[
        SimpleNamespace(
            text='让我搜索一下道奇的赛程，使用 web_search 工具',
            tool_calls=[
                ToolCallRequest(id="call_1", name="execute_shell", arguments={"command": "pwd"}),
            ],
            continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        ),
        SimpleNamespace(text="搜完了", tool_calls=[], continuation_message=None),
    ])

    on_interim = AsyncMock()
    result = await runtime.complete_chat(
        chat_id="test",
        messages=[{"role": "user", "content": "查一下"}],
        constraints=extract_execution_constraints("查一下"),
        tools=runtime.chat_tools(shell_enabled=True),
        deps=_make_deps(),
        event_bus=None,
        on_interim_text=on_interim,
    )

    # 只有最终轮次调用了 on_interim_text
    assert on_interim.await_count == 1
    assert on_interim.await_args_list[0].args[0] == "搜完了"


@pytest.mark.asyncio
async def test_interim_empty_visible_tag_not_sent():
    """中间轮次有标签但内容为空 → on_interim_text 不被调用（中间轮次部分）"""
    router = _make_router()
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry())

    router.complete_with_tools = AsyncMock(side_effect=[
        SimpleNamespace(
            text='<user_visible>  </user_visible>',
            tool_calls=[
                ToolCallRequest(id="call_1", name="execute_shell", arguments={"command": "pwd"}),
            ],
            continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        ),
        SimpleNamespace(text="完成", tool_calls=[], continuation_message=None),
    ])

    on_interim = AsyncMock()
    result = await runtime.complete_chat(
        chat_id="test",
        messages=[{"role": "user", "content": "做事"}],
        constraints=extract_execution_constraints("做事"),
        tools=runtime.chat_tools(shell_enabled=True),
        deps=_make_deps(),
        event_bus=None,
        on_interim_text=on_interim,
    )

    assert on_interim.await_count == 1
    assert on_interim.await_args_list[0].args[0] == "完成"


@pytest.mark.asyncio
async def test_final_reply_strips_user_visible_tags():
    """最终回复中残留的 <user_visible> 标签被清理"""
    router = _make_router()
    runtime = TaskRuntime(router=router, tool_registry=_chat_ready_registry())

    router.complete_with_tools = AsyncMock(side_effect=[
        SimpleNamespace(
            text='<user_visible>好</user_visible>',
            tool_calls=[
                ToolCallRequest(id="call_1", name="execute_shell", arguments={"command": "pwd"}),
            ],
            continuation_message={"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        ),
        SimpleNamespace(
            text="结果是 <user_visible>这个</user_visible> 目录",
            tool_calls=[],
            continuation_message=None,
        ),
    ])

    result = await runtime.complete_chat(
        chat_id="test",
        messages=[{"role": "user", "content": "看看"}],
        constraints=extract_execution_constraints("看看"),
        tools=runtime.chat_tools(shell_enabled=True),
        deps=_make_deps(),
        event_bus=None,
    )

    assert "<user_visible>" not in result
    assert "</user_visible>" not in result
    assert "结果是 这个 目录" in result
