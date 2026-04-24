"""Agent 基类：所有 Agent 的 tool loop 实现。

v2.0 Step 6：观测改由 ``StateMutationLog`` 承担——每个 Agent 执行发射
``AGENT_STARTED`` / ``AGENT_TOOL_CALL`` / ``AGENT_COMPLETED`` / ``AGENT_FAILED``
四类 mutation。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from typing import TYPE_CHECKING, Any

from src.logging.state_mutation_log import MutationType
from src.utils.loop_detection import (
    LoopDetector,
    LoopDetectorConfig,
    LoopVerdict,
)

from .types import AgentMessage, AgentResult, AgentSpec

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.core.runtime_profiles import RuntimeProfile
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry

logger = logging.getLogger("lapwing.agents.base")


class BaseAgent:
    """通用 Agent：接收 AgentMessage，跑独立 tool loop，返回 AgentResult。"""

    def __init__(
        self,
        spec: AgentSpec,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        mutation_log: "StateMutationLog | None",
        services: dict[str, Any] | None = None,
    ):
        self.spec = spec
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.mutation_log = mutation_log
        self._services = services or {}

    async def execute(self, message: AgentMessage) -> AgentResult:
        """执行任务：独立 tool loop。"""

        start_ts = time.perf_counter()
        tool_calls_made = 0
        execution_trace: list[str] = []

        loop_detector = LoopDetector(LoopDetectorConfig(
            warning_threshold=max(3, self.spec.max_rounds // 3),
            global_circuit_breaker_threshold=max(5, self.spec.max_rounds // 2),
        ))
        loop_state = loop_detector.new_state()

        await self._emit(
            MutationType.AGENT_STARTED,
            payload={
                "task_id": message.task_id,
                "agent_name": self.spec.name,
                "actor": self.spec.name,
                "parent_task_id": message.parent_task_id,
                "title": message.content[:200],
                "request": message.content,
            },
        )

        execution_trace.append(f"started: {self.spec.name}")

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
                return await self._finalize_failed(
                    message, "LLM 调用超时", start_ts, tool_calls_made,
                    execution_trace=execution_trace,
                    error_detail="asyncio.TimeoutError during LLM call",
                )
            except Exception as exc:
                logger.exception("Agent '%s' LLM 调用失败", self.spec.name)
                tb = traceback.format_exc()
                tb_tail = "\n".join(tb.strip().splitlines()[-5:])
                return await self._finalize_failed(
                    message, f"LLM error: {exc}", start_ts, tool_calls_made,
                    execution_trace=execution_trace,
                    error_detail=tb_tail,
                )

            if not response.tool_calls:
                execution_trace.append(f"completed: final text ({len(response.text)} chars)")
                return await self._finalize_done(
                    message, response.text, self._extract_evidence(messages),
                    start_ts, tool_calls_made,
                    execution_trace=execution_trace,
                )

            if response.continuation_message:
                messages.append(response.continuation_message)

            tool_results: list[tuple] = []
            for tc in response.tool_calls:
                check = loop_detector.check(loop_state, tc.name, tc.arguments)

                if check.should_block:
                    reason = check.block_reason + f"（Agent: {self.spec.name}）"
                    logger.warning(
                        "[agent] 循环检测触发断路: agent=%s, tool=%s, "
                        "gr=%d, pp=%d",
                        self.spec.name, tc.name,
                        check.generic_repeat_count, check.ping_pong_count,
                    )
                    execution_trace.append(f"circuit_break: {tc.name}")
                    return await self._finalize_failed(
                        message, reason, start_ts, tool_calls_made,
                        execution_trace=execution_trace,
                        error_detail=f"Loop detected on tool '{tc.name}'",
                    )

                if check.has_warning:
                    logger.warning(
                        "[agent] 循环检测警告: agent=%s, tool=%s, "
                        "gr=%d, pp=%d",
                        self.spec.name, tc.name,
                        check.generic_repeat_count, check.ping_pong_count,
                    )

                output = await self._execute_tool(tc, message)
                tool_results.append((tc, output))
                tool_calls_made += 1
                loop_detector.record(loop_state, tc.name, tc.arguments)

                execution_trace.append(f"tool: {tc.name}")

                preview = output if len(output) <= 800 else output[:800] + "...（截断）"
                await self._emit(
                    MutationType.AGENT_TOOL_CALL,
                    payload={
                        "task_id": message.task_id,
                        "agent_name": self.spec.name,
                        "actor": self.spec.name,
                        "parent_task_id": message.parent_task_id,
                        "tool_name": tc.name,
                        "tool_args": tc.arguments,
                        "success": True,
                        "content": preview,
                    },
                )

            result_msg = self.llm_router.build_tool_result_message(
                tool_results, slot=self.spec.model_slot,
            )
            if isinstance(result_msg, list):
                messages.extend(result_msg)
            elif result_msg:
                messages.append(result_msg)

        execution_trace.append(f"max_rounds_exceeded: {self.spec.max_rounds}")
        return await self._finalize_failed(
            message, f"超过最大轮数 {self.spec.max_rounds}",
            start_ts, tool_calls_made,
            execution_trace=execution_trace,
            error_detail=f"Exceeded max_rounds={self.spec.max_rounds}",
        )

    async def _finalize_done(
        self,
        message: AgentMessage,
        text: str,
        evidence: list[dict],
        start_ts: float,
        tool_calls_made: int,
        *,
        execution_trace: list[str] | None = None,
    ) -> AgentResult:
        duration = time.perf_counter() - start_ts
        await self._emit(
            MutationType.AGENT_COMPLETED,
            payload={
                "task_id": message.task_id,
                "agent_name": self.spec.name,
                "actor": self.spec.name,
                "parent_task_id": message.parent_task_id,
                "summary": text[:500],
                "content": text[:500],
                "duration_seconds": round(duration, 3),
                "tool_calls_made": tool_calls_made,
            },
        )
        return AgentResult(
            task_id=message.task_id,
            status="done",
            result=text,
            evidence=evidence,
            execution_trace=execution_trace or [],
        )

    async def _finalize_failed(
        self,
        message: AgentMessage,
        reason: str,
        start_ts: float,
        tool_calls_made: int,
        *,
        execution_trace: list[str] | None = None,
        error_detail: str | None = None,
    ) -> AgentResult:
        duration = time.perf_counter() - start_ts
        await self._emit(
            MutationType.AGENT_FAILED,
            payload={
                "task_id": message.task_id,
                "agent_name": self.spec.name,
                "actor": self.spec.name,
                "parent_task_id": message.parent_task_id,
                "reason": reason,
                "content": reason,
                "duration_seconds": round(duration, 3),
                "tool_calls_made": tool_calls_made,
            },
        )
        return AgentResult(
            task_id=message.task_id,
            status="failed",
            result="",
            reason=reason,
            error_detail=error_detail,
            execution_trace=execution_trace or [],
        )

    async def _emit(self, event_type: MutationType, payload: dict[str, Any]) -> None:
        if self.mutation_log is None:
            return
        try:
            await self.mutation_log.record(event_type, payload)
        except Exception:
            logger.warning(
                "Agent mutation_log emit 失败 (%s)", event_type.value, exc_info=True,
            )

    _AGENT_PERSONA_ANCHOR: str = (
        "记住：你是 Lapwing 的一部分。"
        "输出风格保持温暖自然，短句为主，不列清单，不用加粗标题。"
    )

    def _build_system_prompt(self, message: AgentMessage) -> str:
        parts = [
            self.spec.system_prompt,
            "",
            self._AGENT_PERSONA_ANCHOR,
        ]

        if message.context_digest:
            parts.extend([
                "",
                "## 来自主人格的上下文",
                "",
                message.context_digest,
            ])

        parts.extend([
            "",
            "## 当前任务",
            "",
            f"Task ID: {message.task_id}",
            f"来源: {message.from_agent}",
            "",
            "请完成任务后直接返回结果文本。不需要再调用工具时，输出最终结果即可。",
        ])

        return "\n".join(parts)

    def _get_tools(self) -> list[dict]:
        """根据 runtime_profile 或 tools 白名单返回 OpenAI function 列表。"""
        profile = self.spec.runtime_profile
        if profile is not None:
            return self.tool_registry.function_tools(
                capabilities=set(profile.capabilities) if profile.capabilities else None,
                tool_names=set(profile.tool_names) if profile.tool_names else None,
                include_internal=profile.include_internal,
            )
        tools = []
        for tool_name in self.spec.tools or []:
            spec = self.tool_registry.get(tool_name)
            if spec:
                tools.append(spec.to_function_tool())
        return tools

    async def _execute_tool(self, tool_call, message: AgentMessage) -> str:
        """执行工具并返回 JSON 字符串结果。"""
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.shell_executor import ShellResult

        async def _noop_shell(cmd: str):
            return ShellResult(stdout="", stderr="Shell disabled for agents", return_code=1)

        services = dict(self._services) if self._services else {}

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd=".",
            adapter="agent",
            user_id=f"agent:{self.spec.name}",
            auth_level=1,  # TRUSTED
            chat_id=f"agent-{message.task_id}",
            services=services,
        )

        req = ToolExecutionRequest(name=tool_call.name, arguments=tool_call.arguments)
        try:
            result = await self.tool_registry.execute(req, context=ctx)
            return json.dumps(result.payload, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.exception("Agent '%s' tool '%s' failed", self.spec.name, tool_call.name)
            tb = traceback.format_exc()
            tb_tail = "\n".join(tb.strip().splitlines()[-5:])
            return json.dumps(
                {"error": str(exc), "traceback": tb_tail},
                ensure_ascii=False,
            )

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
