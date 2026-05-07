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

from src.agents.budget import BudgetExhausted
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
        # 即时收集的 evidence——_execute_tool 在工具成功后追加。
        # 比从 messages 回猜 role/格式更可靠（旧的 _extract_evidence 在
        # Anthropic 风格 tool_result block 下永远拿不到）。
        self._collected_evidence: list[dict] = []
        self._collected_tool_errors: list[dict] = []

    async def execute(self, message: AgentMessage) -> AgentResult:
        """执行任务：独立 tool loop。"""

        start_ts = time.perf_counter()
        tool_calls_made = 0
        execution_trace: list[str] = []
        # 防止跨次调用污染——每次 execute 重置即时收集器。
        self._collected_evidence = []

        # Turn-shared budget ledger (Task 9 / blueprint §3 + §5).
        # If absent (older callers / tests), budget enforcement is skipped.
        ledger = self._services.get("budget_ledger") if self._services else None
        self._collected_tool_errors = []

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
            if ledger is not None:
                try:
                    ledger.charge_llm_call()
                except BudgetExhausted as exc:
                    return await self._finalize_budget_exhausted(
                        message, exc, start_ts, tool_calls_made,
                        execution_trace, partial_text="",
                    )
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
                    message, response.text, list(self._collected_evidence),
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

                if ledger is not None:
                    try:
                        ledger.charge_tool_call()
                    except BudgetExhausted as exc:
                        return await self._finalize_budget_exhausted(
                            message, exc, start_ts, tool_calls_made,
                            execution_trace,
                            partial_text=getattr(response, "text", "") or "",
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
        result_text, structured = self._postprocess_result(text, evidence)
        return AgentResult(
            task_id=message.task_id,
            status="done",
            result=result_text,
            evidence=evidence,
            execution_trace=execution_trace or [],
            tool_errors=list(self._collected_tool_errors),
            structured_result=structured,
        )

    def _postprocess_result(
        self, text: str, evidence: list[dict],
    ) -> tuple[str, dict | None]:
        """Hook for subclasses to wrap final text + evidence.

        Default: pass-through. Subclasses (e.g. Researcher) override to
        return a structured payload — ``result`` becomes the JSON form
        and ``structured_result`` holds the parsed dict.
        """
        return text, None

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
            tool_errors=list(self._collected_tool_errors),
        )

    async def _finalize_budget_exhausted(
        self,
        message: AgentMessage,
        exc: BudgetExhausted,
        start_ts: float,
        tool_calls_made: int,
        execution_trace: list[str],
        *,
        partial_text: str = "",
    ) -> AgentResult:
        duration = time.perf_counter() - start_ts
        await self._emit(
            MutationType.AGENT_BUDGET_EXHAUSTED,
            payload={
                "agent_id": getattr(self.spec, "name", ""),
                "agent_name": self.spec.name,
                "task_id": message.task_id,
                "dimension": exc.dimension,
                "used": exc.used,
                "limit": exc.limit,
                "partial_result": partial_text[:500],
                "duration_seconds": round(duration, 3),
                "tool_calls_made": tool_calls_made,
            },
        )
        execution_trace.append(f"budget_exhausted: {exc.dimension}")
        return AgentResult(
            task_id=message.task_id,
            status="done",
            result=partial_text or f"Budget exhausted before completion: {exc.dimension}",
            execution_trace=execution_trace,
            budget_status="budget_exhausted",
        )

    async def _emit(self, event_type: MutationType, payload: dict[str, Any]) -> None:
        event_bus = self._services.get("agent_event_bus") if self._services else None
        background_task_id = (
            payload.get("task_id")
            or (self._services or {}).get("background_task_id")
        )
        if event_bus is not None and background_task_id:
            try:
                from datetime import datetime, timezone
                from src.core.concurrent_bg_work.types import AgentEvent, AgentEventType
                mapping = {
                    MutationType.AGENT_STARTED: AgentEventType.AGENT_STARTED,
                    MutationType.AGENT_TOOL_CALL: AgentEventType.AGENT_TOOL_CALL,
                    MutationType.AGENT_COMPLETED: AgentEventType.AGENT_COMPLETED,
                    MutationType.AGENT_FAILED: AgentEventType.AGENT_FAILED,
                    MutationType.AGENT_BUDGET_EXHAUSTED: AgentEventType.AGENT_BUDGET_EXHAUSTED,
                }
                bg_type = mapping.get(event_type)
                if bg_type is not None:
                    task_id = str(background_task_id)
                    sequence = time.time_ns()
                    chat_id = (
                        payload.get("chat_id")
                        or payload.get("parent_chat_id")
                        or (self._services or {}).get("background_chat_id")
                        or ""
                    )
                    event_payload = dict(payload)
                    event_payload.setdefault("task_id", task_id)
                    if chat_id:
                        event_payload.setdefault("chat_id", str(chat_id))
                    await event_bus.emit(AgentEvent(
                        event_id=f"agent_evt_{task_id}_{event_type.value}_{sequence}",
                        task_id=task_id,
                        chat_id=str(chat_id),
                        type=bg_type,
                        occurred_at=datetime.now(timezone.utc),
                        summary_for_lapwing=str(
                            event_payload.get("summary")
                            or event_payload.get("content")
                            or event_payload.get("reason")
                            or event_type.value
                        )[:500],
                        summary_for_owner=None,
                        raw_payload_ref=None,
                        salience=None,
                        payload=event_payload,
                        sequence_in_task=sequence,
                    ))
                    return
            except Exception:
                logger.warning("AgentEventBus emit failed; falling back to mutation_log", exc_info=True)
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
        "你不能直接对用户说话；需要用户信息时必须通过 AGENT_NEEDS_INPUT 协议返回给 Lapwing。"
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
        from src.tools.types import ToolExecutionRequest
        services = dict(self._services) if self._services else {}

        req = ToolExecutionRequest(name=tool_call.name, arguments=tool_call.arguments)
        try:
            dispatcher = services.get("dispatcher")
            if dispatcher is None or not hasattr(dispatcher, "dispatch"):
                mutation_log = services.get("mutation_log")
                if mutation_log is not None:
                    from src.logging.state_mutation_log import MutationType
                    try:
                        await mutation_log.record(
                            MutationType.TOOL_DENIED,
                            {
                                "tool": tool_call.name,
                                "guard": "dispatcher_missing",
                                "reason": "missing_dispatcher",
                                "auth_level": 3,
                                "agent_name": self.spec.name,
                            },
                        )
                    except Exception:
                        logger.debug("dispatcher missing deny audit failed", exc_info=True)
                # Fail closed: no direct registry fallback, dispatcher is the only gate.
                return json.dumps(
                    {
                        "error": "tool_forbidden",
                        "tool": tool_call.name,
                        "reason": "missing_dispatcher",
                    },
                    ensure_ascii=False,
                )

            result = await dispatcher.dispatch(
                request=req,
                profile=self.spec.runtime_profile or "standard",
                services=services,
                adapter="agent",
                user_id=f"agent:{self.spec.name}",
                chat_id=f"agent-{message.task_id}",
                agent_spec=self._dispatch_agent_spec(),
            )
            
            if result.success and isinstance(result.payload, dict):
                entries = self._extract_evidence_from_payload(
                    tool_name=tool_call.name,
                    payload=result.payload,
                )
                self._collected_evidence.extend(entries)
            elif isinstance(result.payload, dict):
                self._collected_tool_errors.append({
                    "tool": tool_call.name,
                    "reason": result.payload.get("error") or result.reason or "tool_failed",
                    "payload": result.payload,
                })
            return json.dumps(result.payload, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.exception("Agent '%s' tool '%s' failed", self.spec.name, tool_call.name)
            tb = traceback.format_exc()
            tb_tail = "\n".join(tb.strip().splitlines()[-5:])
            return json.dumps(
                {"error": str(exc), "traceback": tb_tail},
                ensure_ascii=False,
            )

    def _dispatch_agent_spec(self):
        """Hook for dispatcher policy checks. DynamicAgent overrides this."""
        return self.spec

    @staticmethod
    def _extract_evidence_from_payload(
        *, tool_name: str, payload: dict,
    ) -> list[dict]:
        """从工具 payload 提取 evidence 条目。

        识别两类形态：
        - 顶层 source_url / url / link / file_path → 一条 evidence
        - 顶层 evidence: list[dict] 或 sources: list[dict] → 逐条展开

        没有以上字段时返回空列表，调用方据此判断是否值得记录。
        """
        entries: list[dict] = []

        nested_sources = payload.get("evidence") or payload.get("sources")
        if isinstance(nested_sources, list):
            for item in nested_sources:
                if not isinstance(item, dict):
                    continue
                source_url = (
                    item.get("source_url")
                    or item.get("url")
                    or item.get("link")
                )
                snippet = (
                    item.get("snippet")
                    or item.get("title")
                    or item.get("summary")
                )
                if source_url or snippet:
                    entries.append({
                        "tool": tool_name,
                        "source_url": source_url,
                        "snippet": snippet,
                    })

        top_url = (
            payload.get("source_url")
            or payload.get("url")
            or payload.get("link")
        )
        top_snippet = payload.get("snippet")
        if top_snippet is None:
            answer = payload.get("answer") or payload.get("summary")
            if isinstance(answer, str) and answer:
                top_snippet = answer[:200]

        top_file = payload.get("file_path")

        if top_url or top_snippet:
            entries.append({
                "tool": tool_name,
                "source_url": top_url,
                "snippet": top_snippet,
            })
        if top_file:
            entries.append({
                "tool": tool_name,
                "file_path": top_file,
            })

        return entries
