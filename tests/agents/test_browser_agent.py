"""BrowserAgent 单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.agents.browser_agent import BrowserAgent
from src.core.agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentEmit,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
)
from src.core.llm_types import ToolCallRequest, ToolTurnResult
from src.tools.types import ToolExecutionResult


# ---------- Fixtures ----------

def _make_command(task: str = "打开 example.com 并截图", **kw) -> AgentCommand:
    return AgentCommand(
        target_agent="browser",
        intent=AgentCommandIntent.EXECUTE,
        task_description=task,
        max_steps=kw.get("max_steps", 5),
        timeout_seconds=kw.get("timeout_seconds", 300),
    )


def _make_runtime(
    *,
    tool_schemas: list[dict] | None = None,
    turns: list[ToolTurnResult] | None = None,
) -> MagicMock:
    """构建 mock TaskRuntime。"""
    runtime = MagicMock()

    # tool_registry.function_tools 返回浏览器工具 schema
    if tool_schemas is None:
        tool_schemas = [
            {"type": "function", "function": {"name": "browser_open", "parameters": {}}},
            {"type": "function", "function": {"name": "browser_screenshot", "parameters": {}}},
        ]
    runtime.tool_registry = MagicMock()
    runtime.tool_registry.function_tools = MagicMock(return_value=tool_schemas)

    # tool_registry.execute 返回成功
    async def _execute(request, *, context):
        return ToolExecutionResult(
            success=True,
            payload={"text": "页面内容已提取", "url": "https://example.com"},
        )
    runtime.tool_registry.execute = _execute

    # llm_router.complete_with_tools 按顺序返回
    if turns is None:
        turns = [
            # 第一轮：调用 browser_open
            ToolTurnResult(
                text="",
                tool_calls=[ToolCallRequest(id="tc1", name="browser_open", arguments={"url": "https://example.com"})],
                continuation_message={"role": "assistant", "content": "", "tool_calls": []},
            ),
            # 第二轮：返回最终文本（无工具调用）
            ToolTurnResult(
                text="已打开 example.com，页面包含示例内容。",
                tool_calls=[],
            ),
        ]
    turn_iter = iter(turns)

    async def _complete_with_tools(messages, tools, purpose="chat", max_tokens=1024, **kw):
        try:
            return next(turn_iter)
        except StopIteration:
            return ToolTurnResult(text="完成", tool_calls=[])

    runtime.llm_router = MagicMock()
    runtime.llm_router.complete_with_tools = _complete_with_tools

    runtime.create_agent_context = MagicMock(return_value=MagicMock())

    return runtime


async def _collect_events(agent, command, runtime) -> list[AgentEmit | AgentNotify]:
    events = []
    async for ev in agent.execute(command, runtime):
        events.append(ev)
    return events


# ---------- Tests ----------

async def test_browser_agent_requires_browser_manager():
    """BrowserAgent 需要 BrowserManager 实例。"""
    bm = MagicMock()
    agent = BrowserAgent(bm)
    assert agent.browser is bm
    assert agent.name == "browser"


async def test_browser_agent_capabilities():
    """BrowserAgent 声明正确的能力。"""
    agent = BrowserAgent(MagicMock())
    caps = agent.capabilities
    assert "browse_web" in caps
    assert "screenshot" in caps


async def test_browser_agent_no_tools_available():
    """没有浏览器工具时返回错误。"""
    agent = BrowserAgent(MagicMock())
    command = _make_command()
    runtime = _make_runtime(tool_schemas=[])

    events = await _collect_events(agent, command, runtime)

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    assert notifies[0].kind == AgentNotifyKind.ERROR
    assert "不可用" in notifies[0].headline


async def test_browser_agent_tool_loop():
    """BrowserAgent 通过工具循环自主浏览网页。"""
    agent = BrowserAgent(MagicMock())
    command = _make_command()
    runtime = _make_runtime()

    events = await _collect_events(agent, command, runtime)

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    assert notifies[0].kind == AgentNotifyKind.RESULT
    assert "浏览完成" in notifies[0].headline
    assert notifies[0].detail  # 应该有内容


async def test_browser_agent_emits_progress():
    """浏览过程中 emit 进度更新。"""
    agent = BrowserAgent(MagicMock())
    command = _make_command()
    runtime = _make_runtime()

    events = await _collect_events(agent, command, runtime)

    working_emits = [
        e for e in events
        if isinstance(e, AgentEmit) and e.state == AgentEmitState.WORKING
    ]
    assert len(working_emits) >= 1


async def test_browser_agent_uses_browser_profile():
    """BrowserAgent 请求 browser 能力的工具。"""
    agent = BrowserAgent(MagicMock())
    command = _make_command()
    runtime = _make_runtime()

    await _collect_events(agent, command, runtime)

    # 验证 function_tools 被以 browser 能力调用
    runtime.tool_registry.function_tools.assert_called_once_with(
        capabilities=frozenset({"browser"}),
        include_internal=False,
    )


async def test_browser_agent_stops_on_no_tool_calls():
    """LLM 不再调用工具时停止循环。"""
    agent = BrowserAgent(MagicMock())
    command = _make_command(max_steps=10)
    # 第一轮就不调用工具
    runtime = _make_runtime(turns=[
        ToolTurnResult(text="不需要浏览器，直接回答。", tool_calls=[]),
    ])

    events = await _collect_events(agent, command, runtime)

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert notifies[0].detail == "不需要浏览器，直接回答。"


async def test_browser_agent_respects_cancel():
    """取消后停止浏览循环。"""
    agent = BrowserAgent(MagicMock())
    await agent.cancel()
    command = _make_command()
    # 提供多轮工具调用
    runtime = _make_runtime(turns=[
        ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="browser_open", arguments={"url": "https://example.com"})],
            continuation_message={"role": "assistant", "content": ""},
        ),
    ] * 5)

    events = await _collect_events(agent, command, runtime)

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    # 应该提前停止，不执行全部 5 轮
