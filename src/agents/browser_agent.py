"""BrowserAgent：网页浏览和内容提取。

与 ResearcherAgent 不同，BrowserAgent 不是预定义流程，
而是给 LLM 浏览器工具让它自主决定浏览策略（LLM-driven tool loop）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.core.agent_base import BaseAgent
from src.core.agent_protocol import (
    AgentCommand,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
    EmitCallback,
)
from src.tools.types import ToolExecutionRequest

if TYPE_CHECKING:
    from src.core.browser_manager import BrowserManager
    from src.core.task_runtime import TaskRuntime

logger = logging.getLogger("lapwing.agents.browser")

_BROWSER_SYSTEM = """你是 Lapwing 的浏览器助手。你的任务是：

{task}

你可以使用浏览器工具来完成任务：导航到网页、截图、提取内容、点击元素等。
每次只调用一个工具，根据结果决定下一步。
完成后，请用简洁的中文总结你找到的信息。不要再调用任何工具。"""


class BrowserAgent(BaseAgent):
    """网页浏览和内容提取 Agent。

    使用 LLM-driven tool loop：给 LLM 浏览器工具 schema，
    让它自主决定浏览策略，每轮执行一个工具调用。
    """

    def __init__(self, browser_manager: BrowserManager):
        super().__init__(
            name="browser",
            description="浏览网页、截图、提取页面内容、与页面交互",
        )
        self.browser = browser_manager

    @property
    def capabilities(self) -> list[str]:
        return ["browse_web", "screenshot", "dom_extract", "page_interact"]

    async def _execute_task(
        self,
        command: AgentCommand,
        task_runtime: TaskRuntime,
        emit: EmitCallback,
    ) -> AgentNotify:
        task = command.task_description
        emit(AgentEmitState.WORKING, "正在启动浏览器...")

        # 获取 browser 类型的工具 schema
        browser_tools = task_runtime.tool_registry.function_tools(
            capabilities=frozenset({"browser"}),
            include_internal=False,
        )
        if not browser_tools:
            return AgentNotify(
                agent_name=self.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline="浏览器工具不可用",
                detail="未找到已注册的浏览器工具，请确认 BROWSER_ENABLED=true",
                ref_command_id=command.id,
            )

        # 构建消息和上下文
        system_prompt = _BROWSER_SYSTEM.format(task=task)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        context = task_runtime.create_agent_context(self.name)
        max_steps = command.max_steps
        last_text = ""

        for step in range(max_steps):
            if self.is_cancel_requested:
                break

            progress = min(0.1 + (step / max_steps) * 0.8, 0.9)
            emit(AgentEmitState.WORKING, f"浏览中（步骤 {step + 1}/{max_steps}）...", progress)

            # 让 LLM 决定下一个工具调用
            turn = await task_runtime.llm_router.complete_with_tools(
                messages,
                browser_tools,
                purpose="agent_execution",
                max_tokens=1024,
                origin="browser_agent",
            )

            last_text = turn.text

            # 没有工具调用 → LLM 认为任务完成
            if not turn.tool_calls:
                break

            # 执行工具调用（一次只执行第一个）
            tc = turn.tool_calls[0]
            # 追加 assistant 消息（含工具调用）
            if turn.continuation_message:
                messages.append(turn.continuation_message)

            result = await task_runtime.tool_registry.execute(
                ToolExecutionRequest(name=tc.name, arguments=tc.arguments),
                context=context,
            )

            # 将工具结果追加到对话
            result_text = result.payload.get("text", "") or result.payload.get("summary", "")
            if not result_text:
                result_text = f"{'成功' if result.success else '失败'}: {result.reason or str(result.payload)}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_text[:2000],
            })

        emit(AgentEmitState.WORKING, "浏览完成，正在整理结果...", 0.95)

        return AgentNotify(
            agent_name=self.name,
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.SOON,
            headline=f"浏览完成：{task[:50]}",
            detail=last_text or "浏览已完成但未生成摘要",
            ref_command_id=command.id,
        )
