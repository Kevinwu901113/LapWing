"""Agent 基类：所有 Agent 的 tool loop 实现。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from .types import AgentMessage, AgentResult, AgentSpec

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry

logger = logging.getLogger("lapwing.agents.base")


class BaseAgent:
    """通用 Agent：接收 AgentMessage，跑独立 tool loop，返回 AgentResult。"""

    def __init__(
        self,
        spec: AgentSpec,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        dispatcher: "Dispatcher",
        services: dict[str, Any] | None = None,
    ):
        self.spec = spec
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.dispatcher = dispatcher
        self._services = services or {}

    async def execute(self, message: AgentMessage) -> AgentResult:
        """执行任务：独立 tool loop。"""

        await self.dispatcher.submit(
            event_type="agent.task_started",
            actor=self.spec.name,
            task_id=message.task_id,
            payload={"task_request": "", "message": message.content},
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt(message)},
            {"role": "user", "content": message.content},
        ]

        available_tools = self._get_tools()

        for round_num in range(self.spec.max_rounds):
            try:
                response = await asyncio.wait_for(
                    self.llm_router.complete_with_tools(
                        messages=messages,
                        tools=available_tools,
                        slot=self.spec.model_slot,
                        max_tokens=min(self.spec.max_tokens // 2, 4096),
                        origin=f"agent:{self.spec.name}",
                    ),
                    timeout=self.spec.timeout_seconds,
                )
            except asyncio.TimeoutError:
                return AgentResult(
                    task_id=message.task_id,
                    status="failed",
                    result="",
                    reason="LLM 调用超时",
                )
            except Exception as exc:
                logger.exception("Agent '%s' LLM 调用失败", self.spec.name)
                return AgentResult(
                    task_id=message.task_id,
                    status="failed",
                    result="",
                    reason=f"LLM error: {exc}",
                )

            # 无 tool_calls → 任务完成
            if not response.tool_calls:
                return AgentResult(
                    task_id=message.task_id,
                    status="done",
                    result=response.text,
                    evidence=self._extract_evidence(messages),
                )

            # 追加 assistant continuation
            if response.continuation_message:
                messages.append(response.continuation_message)

            # 执行工具
            tool_results: list[tuple] = []
            for tc in response.tool_calls:
                output = await self._execute_tool(tc, message)
                tool_results.append((tc, output))

                preview = output if len(output) <= 800 else output[:800] + "...（截断）"
                await self.dispatcher.submit(
                    event_type="agent.tool_called",
                    actor=self.spec.name,
                    task_id=message.task_id,
                    payload={
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "success": True,
                        "result_preview": preview,
                    },
                )

            # 追加 tool results — build_tool_result_message expects list[tuple[ToolCallRequest, str]]
            result_msg = self.llm_router.build_tool_result_message(
                tool_results, slot=self.spec.model_slot,
            )
            if isinstance(result_msg, list):
                messages.extend(result_msg)
            elif result_msg:
                messages.append(result_msg)

        # 超出 max_rounds
        return AgentResult(
            task_id=message.task_id,
            status="failed",
            result="",
            reason=f"超过最大轮数 {self.spec.max_rounds}",
        )

    def _build_system_prompt(self, message: AgentMessage) -> str:
        return f"""{self.spec.system_prompt}

## 当前任务

Task ID: {message.task_id}
来源: {message.from_agent}

请完成任务后直接返回结果文本。不需要再调用工具时，输出最终结果即可。"""

    def _get_tools(self) -> list[dict]:
        tools = []
        for tool_name in self.spec.tools:
            spec = self.tool_registry.get(tool_name)
            if spec:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.json_schema,
                    },
                })
        return tools

    async def _execute_tool(self, tool_call, message: AgentMessage) -> str:
        """执行工具并返回 JSON 字符串结果。"""
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd=".",
            adapter="agent",
            user_id=f"agent:{self.spec.name}",
            auth_level=1,  # TRUSTED
            chat_id=f"agent-{message.task_id}",
            services=self._services,
        )

        req = ToolExecutionRequest(name=tool_call.name, arguments=tool_call.arguments)
        try:
            result = await self.tool_registry.execute(req, context=ctx)
            return json.dumps(result.payload, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.exception("Agent '%s' tool '%s' failed", self.spec.name, tool_call.name)
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    def _extract_evidence(self, messages: list[dict]) -> list[dict]:
        evidence = []
        for msg in messages:
            if msg.get("role") == "tool":
                try:
                    content = json.loads(msg.get("content", "{}"))
                    if isinstance(content, dict):
                        if "url" in content:
                            evidence.append({"type": "url", "value": content["url"]})
                        if "file_path" in content:
                            evidence.append({"type": "file", "value": content["file_path"]})
                except Exception:
                    pass
        return evidence


async def _noop_shell(cmd: str):
    """Agent 不允许直接执行 shell。"""
    from src.tools.shell_executor import ShellResult
    return ShellResult(stdout="", stderr="Shell disabled for agents", return_code=1)
