"""任务执行运行时：封装 tool loop、工具执行和任务生命周期事件。"""

from __future__ import annotations

from collections import deque
import hashlib
import json
import logging
from pathlib import Path
import time
import uuid
from typing import Any, Awaitable, Callable

from config.settings import (
    MEMORY_CRUD_ENABLED,
    ROOT_DIR,
    SELF_SCHEDULE_ENABLED,
    SHELL_DEFAULT_CWD,
    TASK_MAX_TOOL_ROUNDS,
)
from src.core.llm_router import ToolCallRequest

# Re-export types for backward compatibility
from src.core.task_types import (  # noqa: F401
    LoopDetectionConfig,
    LoopDetectionState,
    RuntimeDeps,
    TaskLoopStep,
    TaskLoopResult,
    _refresh_voice_reminder,
)
from src.core.runtime_profiles import RuntimeProfile, get_runtime_profile
from src.core.shell_policy import (
    ExecutionConstraints,
    ExecutionSessionState,
    PendingShellConfirmation,
    build_followup_message,
    is_confirmation_message,
    is_rejection_message,
)
from src.core.shell_policy import ShellRuntimePolicy
from src.core.authority_gate import AuthLevel, authorize, identify as identify_auth
from src.core.vital_guard import (
    Verdict,
    auto_backup,
    check_compound,
    check_file_target,
    extract_vital_shell_targets,
)
from src.tools.registry import ToolRegistry, build_default_tool_registry
from src.tools.shell_executor import ShellResult, execute as default_execute_shell
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.core.task_runtime")


_MAX_TOOL_ROUNDS = TASK_MAX_TOOL_ROUNDS
_TOOL_RESULT_MAX_CHARS = 12000
_WEB_SEARCH_SNIPPET_MAX_CHARS = 280

# VitalGuard 对命令类型的分类（模块级常量，避免每次 execute_tool() 重建）
_SHELL_TOOLS: frozenset[str] = frozenset({"execute_shell", "run_python_code"})
_FILE_WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file", "file_write", "file_append", "apply_workspace_patch",
})


class TaskRuntime:
    """负责执行工具轮次、统一工具执行和任务级事件发布。"""

    def __init__(
        self,
        router,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
        tool_registry: ToolRegistry | None = None,
        loop_detection_config: LoopDetectionConfig | None = None,
        latency_monitor: Any | None = None,
        memory: Any | None = None,
    ) -> None:
        self._router = router
        self._max_tool_rounds = max_tool_rounds
        self._tool_registry = tool_registry or build_default_tool_registry()
        self._pending_shell_confirmations: dict[str, PendingShellConfirmation] = {}
        self._loop_detection_config = loop_detection_config or LoopDetectionConfig()
        self._latency_monitor = latency_monitor
        self._memory = memory
        self._memory_index: Any | None = None

    def set_browser_guard(self, browser_guard: Any | None) -> None:
        self._browser_guard = browser_guard

    def set_latency_monitor(self, latency_monitor: Any | None) -> None:
        self._latency_monitor = latency_monitor

    def set_memory_index(self, memory_index: Any | None) -> None:
        self._memory_index = memory_index

    def clear_chat_state(self, chat_id: str) -> None:
        self._pending_shell_confirmations.pop(chat_id, None)

    def resolve_pending_confirmation(
        self,
        chat_id: str,
        user_message: str,
    ) -> tuple[str, str | None, str | None]:
        pending = self._pending_shell_confirmations.get(chat_id)
        if pending is None:
            return user_message, None, None

        if (
            is_confirmation_message(user_message)
            or pending.alternative_directory in user_message
        ):
            self._pending_shell_confirmations.pop(chat_id, None)
            return (
                build_followup_message(pending),
                pending.alternative_directory,
                None,
            )

        if is_rejection_message(user_message):
            self._pending_shell_confirmations.pop(chat_id, None)
            return (
                user_message,
                None,
                "好，我先不改到那个替代位置。原请求还没有完成。",
            )

        self._pending_shell_confirmations.pop(chat_id, None)
        return user_message, None, None

    def record_pending_confirmation(
        self,
        chat_id: str,
        state: ExecutionSessionState,
    ) -> str:
        alternative = state.alternative
        if alternative is None:
            return state.failure_message()

        self._pending_shell_confirmations[chat_id] = PendingShellConfirmation(
            original_user_message=state.constraints.original_user_message,
            alternative_directory=alternative.directory,
            reason=state.failure_reason or alternative.reason,
        )
        return state.consent_message()

    def _resolve_profile(self, profile: str | RuntimeProfile) -> RuntimeProfile:
        if isinstance(profile, RuntimeProfile):
            return profile
        return get_runtime_profile(profile)

    def _tool_names_for_profile(
        self,
        profile: RuntimeProfile,
        *,
        include_internal: bool,
    ) -> set[str]:
        specs = self._tool_registry.list_tools(
            capabilities=set(profile.capabilities),
            include_internal=include_internal,
            tool_names=set(profile.tool_names) if profile.tool_names else None,
        )
        return {spec.name for spec in specs}

    def tools_for_profile(self, profile: str | RuntimeProfile) -> list[dict[str, Any]]:
        profile_obj = self._resolve_profile(profile)
        return self._tool_registry.function_tools(
            capabilities=set(profile_obj.capabilities),
            include_internal=False,
            tool_names=set(profile_obj.tool_names) if profile_obj.tool_names else None,
        )

    def chat_tools(
        self,
        shell_enabled: bool,
        *,
        web_enabled: bool = True,
        skill_activation_enabled: bool = False,
    ) -> list[dict[str, Any]]:
        """chat 场景工具集：按需暴露 shell / web / activate_skill，memory_note 始终可用。"""
        tool_names: set[str] = {"memory_note", "get_weather", "send_image", "send_proactive_message"}  # always available
        if shell_enabled:
            tool_names.update({"execute_shell", "read_file", "write_file"})
        if web_enabled:
            tool_names.update({"web_search", "web_fetch"})
        if skill_activation_enabled:
            tool_names.add("activate_skill")
        if MEMORY_CRUD_ENABLED:
            tool_names.update({"memory_list", "memory_read", "memory_edit", "memory_delete", "memory_search"})
        if SELF_SCHEDULE_ENABLED:
            tool_names.update({"schedule_task", "list_scheduled_tasks", "cancel_scheduled_task"})
        return self._tool_registry.function_tools(
            include_internal=False,
            tool_names=tool_names,
        )

    async def run_task_loop(
        self,
        *,
        max_rounds: int,
        step_runner: Callable[[int], Awaitable[TaskLoopStep]],
    ) -> TaskLoopResult:
        last_payload: dict[str, Any] | None = None
        for round_index in range(max_rounds):
            step = await step_runner(round_index)
            if step.payload is not None:
                last_payload = step.payload

            if step.completed or step.stop:
                return TaskLoopResult(
                    completed=step.completed,
                    stopped=step.stop,
                    attempts=round_index + 1,
                    reason=step.reason,
                    last_payload=last_payload,
                )

        return TaskLoopResult(
            completed=False,
            stopped=False,
            attempts=max_rounds,
            reason="max_rounds_exceeded",
            last_payload=last_payload,
        )

    async def complete_chat(
        self,
        *,
        chat_id: str,
        messages: list[dict[str, Any]],
        constraints: ExecutionConstraints,
        tools: list[dict[str, Any]],
        deps: RuntimeDeps,
        status_callback=None,
        event_bus=None,
        on_consent_required: Callable[[ExecutionSessionState], str] | None = None,
        services: dict[str, Any] | None = None,
        profile: str | RuntimeProfile = "chat_shell",
        on_interim_text: Callable[[str], "Awaitable[None]"] | None = None,
        on_typing: Callable[[], "Awaitable[None]"] | None = None,
        adapter: str = "",
        user_id: str = "",
    ) -> str:
        async def _emit_status(text: str) -> None:
            if status_callback is None:
                return
            try:
                await status_callback(chat_id, text)
            except Exception:
                pass

        if not tools:
            await _emit_status("stage:planning")
            reply = await self._router.complete(
                messages,
                slot="main_conversation",
                session_key=f"chat:{chat_id}",
                origin="task_runtime.chat",
            )
            await _emit_status("stage:finalizing")
            return reply

        profile_obj = self._resolve_profile(profile)
        state = ExecutionSessionState(constraints=constraints)
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        await _emit_status("stage:planning")
        await self._publish_task_event(
            event_bus,
            "task.started",
            task_id=task_id,
            chat_id=chat_id,
            phase="started",
            text="任务开始执行。",
        )
        await self._publish_task_event(
            event_bus,
            "task.planning",
            task_id=task_id,
            chat_id=chat_id,
            phase="planning",
            text="正在规划执行步骤。",
        )

        last_payload: dict[str, Any] | None = None
        final_reply: str | None = None
        interim_parts: list[str] = []  # 收集已通过 on_interim_text 发出的中间文字
        loop_detection_state = self._new_loop_detection_state()
        simulated_tool_retries = 0

        async def _step_runner(round_index: int) -> TaskLoopStep:
            nonlocal last_payload, final_reply, simulated_tool_retries
            round_started_at = time.perf_counter()
            turn = await self._router.complete_with_tools(
                self._with_shell_state_context(messages, state),
                tools=tools,
                slot="main_conversation",
                session_key=f"chat:{chat_id}",
                origin="task_runtime.chat",
            )

            if not turn.tool_calls:
                # ── 模拟工具调用检测 ──
                model_text = (turn.text or "").strip()
                if simulated_tool_retries < 1 and model_text:
                    available_tool_names = [t["function"]["name"] for t in tools]
                    if self._detect_simulated_tool_call(model_text, available_tool_names):
                        simulated_tool_retries += 1
                        logger.info("[runtime] 检测到模拟工具调用，注入提醒（retry %d）", simulated_tool_retries)
                        messages.append({
                            "role": "user",
                            "content": (
                                "[系统提醒] 你刚才在文字中描述了工具调用，但没有真正调用。"
                                "请直接使用工具，不要用文字描述。"
                            ),
                        })
                        return TaskLoopStep()  # continue loop

                await _emit_status("stage:finalizing")
                final_text = model_text
                if final_text and on_interim_text is not None:
                    try:
                        await on_interim_text(final_text)
                        interim_parts.append(final_text)
                    except Exception:
                        pass
                final_reply = await self._finalize_without_tool_calls(
                    chat_id=chat_id,
                    task_id=task_id,
                    state=state,
                    model_text=turn.text,
                    last_payload=last_payload,
                    event_bus=event_bus,
                    on_consent_required=on_consent_required,
                )
                return TaskLoopStep(completed=True, payload=last_payload)

            # LLM 返回了 tool_call，文字部分是中间回复（"等一下，我看看"）
            interim_text = (turn.text or "").strip()
            if interim_text and on_interim_text is not None:
                try:
                    await on_interim_text(interim_text)
                    interim_parts.append(interim_text)
                except Exception:
                    pass

            tool_names = [tool_call.name for tool_call in turn.tool_calls]
            executed_tool_names: list[str] = []

            def _record_round_latency() -> None:
                names = executed_tool_names if executed_tool_names else tool_names
                self._record_tool_loop_latency(
                    round_started_at=round_started_at,
                    tool_names=names,
                )

            if len(turn.tool_calls) > 1:
                logger.info(
                    "[runtime] 模型返回了 %s 个 tool calls，当前按顺序串行执行。",
                    len(turn.tool_calls),
                )

            if turn.continuation_message is not None:
                messages.append(turn.continuation_message)

            tool_results: list[tuple[ToolCallRequest, str]] = []
            last_tool_name: str | None = None
            for tool_index, tool_call in enumerate(turn.tool_calls):
                await _emit_status(
                    f"stage:executing:{tool_call.name}:{tool_index + 1}:{len(turn.tool_calls)}"
                )
                tool_args_hash = self._tool_args_hash(tool_call.arguments)
                tool_signature = (tool_call.name, tool_args_hash)
                generic_repeat_count = self._generic_repeat_count(
                    loop_detection_state=loop_detection_state,
                    current_signature=tool_signature,
                )
                tool_event_common = {
                    "tool_name": tool_call.name,
                    "round": round_index + 1,
                    "turn_tool_index": tool_index + 1,
                    "turn_tool_total": len(turn.tool_calls),
                    "toolCallId": tool_call.id,
                    "toolName": tool_call.name,
                    "argsHash": tool_args_hash,
                }
                loop_detection_common = {
                    "loop_detection_detector": "genericRepeat",
                    "loop_detection_repeat_count": generic_repeat_count,
                    "loop_detection_warning_threshold": self._loop_detection_config.warning_threshold,
                    "loop_detection_critical_threshold": self._loop_detection_config.critical_threshold,
                    "loop_detection_global_circuit_breaker_threshold": (
                        self._loop_detection_config.global_circuit_breaker_threshold
                    ),
                }

                if self._should_emit_generic_repeat_warning(generic_repeat_count):
                    logger.warning(
                        (
                            "[runtime] 检测到 genericRepeat 警告: tool=%s, repeat=%s, "
                            "warning=%s, critical=%s, global=%s, args_hash=%s"
                        ),
                        tool_call.name,
                        generic_repeat_count,
                        self._loop_detection_config.warning_threshold,
                        self._loop_detection_config.critical_threshold,
                        self._loop_detection_config.global_circuit_breaker_threshold,
                        tool_args_hash,
                    )
                    await self._publish_task_event(
                        event_bus,
                        "task.executing",
                        task_id=task_id,
                        chat_id=chat_id,
                        phase="executing",
                        text=(
                            "检测到可能无进展的重复调用，继续执行并观察："
                            f"{tool_call.name}（连续 {generic_repeat_count} 次）"
                        ),
                        **tool_event_common,
                        loop_detection_warning=True,
                        **loop_detection_common,
                    )

                if self._should_block_by_global_circuit_breaker(generic_repeat_count):
                    reason = (
                        "检测到无进展重复循环（同一工具与参数连续重复），"
                        "已触发全局断路器，需用户介入（提供新策略/新指令）。"
                    )
                    logger.warning(
                        (
                            "[runtime] 触发 loop global circuit breaker: tool=%s, repeat=%s, "
                            "global_threshold=%s, args_hash=%s"
                        ),
                        tool_call.name,
                        generic_repeat_count,
                        self._loop_detection_config.global_circuit_breaker_threshold,
                        tool_args_hash,
                    )
                    state.record_failure(reason, "blocked")
                    await self._publish_task_event(
                        event_bus,
                        "task.blocked",
                        task_id=task_id,
                        chat_id=chat_id,
                        phase="blocked",
                        text="检测到无进展重复循环，已停止自动执行，等待用户介入。",
                        reason=reason,
                        **tool_event_common,
                        **loop_detection_common,
                    )
                    final_reply = (
                        "检测到无进展重复循环（同一工具与参数连续重复），"
                        "我已停止当前自动执行，需用户介入。请提供新的策略或更具体的指令后我再继续。"
                    )
                    await _emit_status("stage:finalizing")
                    _record_round_latency()
                    return TaskLoopStep(completed=True, payload=last_payload)

                # ── ping-pong 检测（A→B→A→B 交替模式）──────────────────────
                ping_pong_count = self._ping_pong_count(
                    loop_detection_state=loop_detection_state,
                    current_signature=tool_signature,
                )
                if self._should_emit_ping_pong_warning(ping_pong_count):
                    logger.warning(
                        "[runtime] 检测到 pingPong 警告: tool=%s, count=%s, args_hash=%s",
                        tool_call.name, ping_pong_count, tool_args_hash,
                    )
                if self._should_block_by_ping_pong(ping_pong_count):
                    reason = (
                        "检测到无进展交替循环（两个工具交替重复调用），"
                        "已触发断路器，需用户介入（提供新策略/新指令）。"
                    )
                    logger.warning(
                        "[runtime] 触发 pingPong circuit breaker: tool=%s, count=%s",
                        tool_call.name, ping_pong_count,
                    )
                    state.record_failure(reason, "blocked")
                    await self._publish_task_event(
                        event_bus,
                        "task.blocked",
                        task_id=task_id,
                        chat_id=chat_id,
                        phase="blocked",
                        text="检测到无进展交替循环，已停止自动执行，等待用户介入。",
                        reason=reason,
                        **tool_event_common,
                    )
                    final_reply = (
                        "检测到无进展交替循环（两个工具交替重复调用），"
                        "我已停止当前自动执行，需用户介入。请提供新的策略或更具体的指令后我再继续。"
                    )
                    await _emit_status("stage:finalizing")
                    _record_round_latency()
                    return TaskLoopStep(completed=True, payload=last_payload)

                # 当前先走串行执行，后续可按 provider 能力升级到并行分发。
                await self._publish_task_event(
                    event_bus,
                    "task.executing",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="executing",
                    text=f"正在执行工具：{tool_call.name}",
                    **tool_event_common,
                )
                await self._publish_task_event(
                    event_bus,
                    "task.tool_execution_start",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="executing",
                    text=f"工具开始执行：{tool_call.name}",
                    stdoutBytes=0,
                    stderrBytes=0,
                    isError=False,
                    durationMs=0,
                    **tool_event_common,
                )

                # 工具执行前触发 typing indicator
                if on_typing is not None:
                    try:
                        await on_typing()
                    except Exception:
                        pass

                tool_started_at = time.perf_counter()
                tool_result_text, payload, execution_success = await self._execute_tool_call(
                    tool_call=tool_call,
                    state=state,
                    deps=deps,
                    task_id=task_id,
                    chat_id=chat_id,
                    event_bus=event_bus,
                    profile=profile_obj,
                    services=services,
                    adapter=adapter,
                    user_id=user_id,
                )
                duration_ms = max(int((time.perf_counter() - tool_started_at) * 1000), 0)
                stdout_bytes = self._text_utf8_bytes(payload.get("stdout"))
                stderr_bytes = self._text_utf8_bytes(payload.get("stderr"))
                is_error = self._tool_execution_is_error(
                    payload=payload,
                    execution_success=execution_success,
                )
                tool_event_metrics = {
                    "stdoutBytes": stdout_bytes,
                    "stderrBytes": stderr_bytes,
                    "isError": is_error,
                    "durationMs": duration_ms,
                }
                await self._publish_task_event(
                    event_bus,
                    "task.tool_execution_update",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="executing",
                    text=f"工具执行进度：{tool_call.name}",
                    **tool_event_common,
                    **tool_event_metrics,
                )
                await self._publish_task_event(
                    event_bus,
                    "task.tool_execution_end",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="executing",
                    text=f"工具执行结束：{tool_call.name}",
                    **tool_event_common,
                    **tool_event_metrics,
                )
                tool_results.append((tool_call, tool_result_text))
                executed_tool_names.append(tool_call.name)
                self._record_tool_signature(
                    loop_detection_state=loop_detection_state,
                    signature=tool_signature,
                )
                last_payload = payload
                last_tool_name = tool_call.name
                logger.info(
                    "[runtime] 第 %s 轮完成 tool call %s/%s: %s",
                    round_index + 1,
                    tool_index + 1,
                    len(turn.tool_calls),
                    tool_call.name,
                )

                if state.consent_required:
                    await self._publish_task_event(
                        event_bus,
                        "task.blocked",
                        task_id=task_id,
                        chat_id=chat_id,
                        phase="blocked",
                        text="任务被阻塞，等待用户确认替代路径。",
                        reason=state.failure_reason or "需要用户确认",
                        tool_name=tool_call.name,
                    )
                    if on_consent_required is not None:
                        final_reply = on_consent_required(state)
                    else:
                        final_reply = state.consent_message()
                    await _emit_status("stage:finalizing")
                    _record_round_latency()
                    return TaskLoopStep(completed=True, payload=last_payload)

                if state.completed:
                    break

            result_message = self._router.build_tool_result_message(
                slot="main_conversation",
                tool_results=tool_results,
                session_key=f"chat:{chat_id}",
            )
            if isinstance(result_message, list):
                messages.extend(result_message)
            else:
                messages.append(result_message)

            if state.completed:
                await self._publish_task_event(
                    event_bus,
                    "task.completed",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="completed",
                    text="任务执行并验证完成。",
                    tool_name=last_tool_name,
                )
                final_reply = state.success_message()
                await _emit_status("stage:finalizing")
                _record_round_latency()
                return TaskLoopStep(completed=True, payload=last_payload)

            if last_payload is not None and last_payload.get("blocked"):
                await self._publish_task_event(
                    event_bus,
                    "task.blocked",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="blocked",
                    text="命令被拦截，等待后续恢复步骤。",
                    reason=str(last_payload.get("reason", "命令被拦截。")),
                    tool_name=last_tool_name,
                )

            _record_round_latency()
            # 压缩旧的浏览器 PageState（保留最新完整，旧的只留摘要）
            self._compress_browser_history(messages)
            # 重新注入 voice reminder（tool call 循环会让消息越来越长，
            # 导致 voice reminder 离生成位置越来越远）
            _refresh_voice_reminder(messages)
            return TaskLoopStep(payload=last_payload)

        loop_result = await self.run_task_loop(
            max_rounds=self._max_tool_rounds,
            step_runner=_step_runner,
        )

        if final_reply is not None:
            return final_reply

        logger.warning("[runtime] tool call 循环超过上限，返回兜底说明")

        if state.consent_required:
            await self._publish_task_event(
                event_bus,
                "task.blocked",
                task_id=task_id,
                chat_id=chat_id,
                phase="blocked",
                text="任务被阻塞，等待用户确认替代路径。",
                reason=state.failure_reason or "需要用户确认",
            )
            if on_consent_required is not None:
                await _emit_status("stage:finalizing")
                return on_consent_required(state)
            await _emit_status("stage:finalizing")
            return state.consent_message()

        if state.completed:
            await self._publish_task_event(
                event_bus,
                "task.completed",
                task_id=task_id,
                chat_id=chat_id,
                phase="completed",
                text="任务执行并验证完成。",
            )
            await _emit_status("stage:finalizing")
            return state.success_message()

        if state.constraints.is_write_request and state.constraints.objective != "generic":
            await self._publish_task_event(
                event_bus,
                "task.failed",
                task_id=task_id,
                chat_id=chat_id,
                phase="failed",
                text="任务未完成。",
                reason=state.failure_reason or loop_result.reason or "tool 循环超过上限",
            )
            await _emit_status("stage:finalizing")
            return state.failure_message()

        await self._publish_task_event(
            event_bus,
            "task.failed",
            task_id=task_id,
            chat_id=chat_id,
            phase="failed",
            text="任务未在轮次上限内完成，返回兜底结果。",
            reason="tool 循环超过上限",
        )
        await _emit_status("stage:finalizing")
        return self.tool_fallback_reply(loop_result.last_payload)

    async def _finalize_without_tool_calls(
        self,
        *,
        chat_id: str,
        task_id: str,
        state: ExecutionSessionState,
        model_text: str,
        last_payload: dict[str, Any] | None,
        event_bus,
        on_consent_required: Callable[[ExecutionSessionState], str] | None,
    ) -> str:
        if state.consent_required:
            await self._publish_task_event(
                event_bus,
                "task.blocked",
                task_id=task_id,
                chat_id=chat_id,
                phase="blocked",
                text="任务被阻塞，等待用户确认替代路径。",
                reason=state.failure_reason or "需要用户确认",
            )
            if on_consent_required is not None:
                return on_consent_required(state)
            return state.consent_message()

        if state.completed:
            await self._publish_task_event(
                event_bus,
                "task.completed",
                task_id=task_id,
                chat_id=chat_id,
                phase="completed",
                text="任务执行并验证完成。",
            )
            return state.success_message()

        if state.constraints.is_write_request and state.constraints.objective != "generic":
            await self._publish_task_event(
                event_bus,
                "task.failed",
                task_id=task_id,
                chat_id=chat_id,
                phase="failed",
                text="任务未完成。",
                reason=state.failure_reason or "未达到验证目标",
            )
            return state.failure_message()

        reply = model_text or self.tool_fallback_reply(last_payload)
        if last_payload and last_payload.get("blocked"):
            await self._publish_task_event(
                event_bus,
                "task.blocked",
                task_id=task_id,
                chat_id=chat_id,
                phase="blocked",
                text="命令被拦截。",
                reason=str(last_payload.get("reason", "命令被拦截。")),
            )
        elif last_payload and (last_payload.get("timed_out") or int(last_payload.get("return_code", 0)) != 0):
            await self._publish_task_event(
                event_bus,
                "task.failed",
                task_id=task_id,
                chat_id=chat_id,
                phase="failed",
                text="任务执行失败。",
                reason=str(last_payload.get("reason", "命令执行失败。")),
            )
        else:
            await self._publish_task_event(
                event_bus,
                "task.completed",
                task_id=task_id,
                chat_id=chat_id,
                phase="completed",
                text="任务执行完成。",
            )
        return reply

    def _with_shell_state_context(
        self,
        messages: list[dict[str, Any]],
        state: ExecutionSessionState,
    ) -> list[dict[str, Any]]:
        needs_context = (
            state.constraints.is_write_request
            or state.constraints.has_hard_path_constraints
            or bool(state.failure_reason)
            or state.consent_required
        )
        if not needs_context:
            return messages

        state_content = state.as_system_context()
        if messages and messages[0].get("role") == "system":
            merged_system = dict(messages[0])
            base_content = str(merged_system.get("content", "")).strip()
            merged_system["content"] = (
                f"{base_content}\n\n{state_content}" if base_content else state_content
            )
            return [merged_system, *messages[1:]]
        state_message = {"role": "system", "content": state_content}
        return [state_message, *messages]

    def _shell_failure_reason(self, result: ShellResult) -> str:
        if result.reason and (result.blocked or result.timed_out):
            return result.reason

        stderr = result.stderr.strip()
        if stderr:
            return stderr
        if result.reason:
            return result.reason
        if result.timed_out:
            return "命令执行超时了。"
        if result.return_code != 0:
            return f"命令执行失败，退出码 {result.return_code}。"
        return "命令执行失败了。"

    async def execute_tool(
        self,
        *,
        request: ToolExecutionRequest,
        profile: str | RuntimeProfile,
        state: ExecutionSessionState | None = None,
        deps: RuntimeDeps | None = None,
        task_id: str | None = None,
        chat_id: str | None = None,
        event_bus=None,
        workspace_root: str | None = None,
        services: dict[str, Any] | None = None,
        adapter: str = "",
        user_id: str = "",
    ) -> ToolExecutionResult:
        profile_obj = self._resolve_profile(profile)
        tool = self._tool_registry.get(request.name)
        if tool is None:
            reason = f"未知工具：{request.name}"
            if state is not None:
                state.record_failure(reason, "blocked")
            payload = self._blocked_payload(
                reason=reason,
                cwd=(deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD),
                command="",
            )
            return ToolExecutionResult(success=False, payload=payload, reason=reason)

        allowed_names = self._tool_names_for_profile(
            profile_obj,
            include_internal=profile_obj.include_internal,
        )
        if request.name not in allowed_names:
            reason = f"当前 profile `{profile_obj.name}` 不允许工具 `{request.name}`。"
            if state is not None:
                state.record_failure(reason, "blocked")
            payload = self._blocked_payload(
                reason=reason,
                cwd=(deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD),
                command=str(request.arguments.get("command", "")).strip(),
            )
            return ToolExecutionResult(success=False, payload=payload, reason=reason)

        # ── AuthorityGate：权限检查 ─────────────────────────────────────────────
        # adapter 为空 = 内部调用（heartbeat/agents），默认 OWNER 不受限
        auth_level = identify_auth(adapter, user_id) if adapter else AuthLevel.OWNER
        allowed, deny_reason = authorize(request.name, auth_level)
        if not allowed:
            if state is not None:
                state.record_failure(deny_reason, "blocked")
            payload = self._blocked_payload(
                reason=deny_reason,
                cwd=(deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD),
                command=str(request.arguments.get("command", "")).strip(),
            )
            return ToolExecutionResult(success=False, payload=payload, reason=deny_reason)

        shell_executor = deps.execute_shell if deps is not None else default_execute_shell
        shell_default_cwd = deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD

        # ── VitalGuard：存活保护检查 ────────────────────────────────────────────
        vg_command = str(request.arguments.get("command", "")).strip()

        if request.name in _SHELL_TOOLS and vg_command:
            from config.settings import SHELL_BACKEND
            guard = check_compound(vg_command, relaxed=(SHELL_BACKEND == "docker"))
            if guard.verdict == Verdict.BLOCK:
                reason = f"[VitalGuard] {guard.reason}"
                if state is not None:
                    state.record_failure(reason, "blocked")
                payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command=vg_command)
                return ToolExecutionResult(success=False, payload=payload, reason=reason)
            if guard.verdict == Verdict.VERIFY_FIRST:
                vital_targets = extract_vital_shell_targets(vg_command)
                if vital_targets:
                    await auto_backup(vital_targets)

        elif request.name in _FILE_WRITE_TOOLS:
            path_str = str(request.arguments.get("path", "")).strip()
            if path_str:
                target = Path(path_str).expanduser().resolve()
                file_guard = check_file_target(target)
                if file_guard.verdict == Verdict.BLOCK:
                    reason = f"[VitalGuard] {file_guard.reason}"
                    if state is not None:
                        state.record_failure(reason, "blocked")
                    payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command="")
                    return ToolExecutionResult(success=False, payload=payload, reason=reason)
                if file_guard.verdict == Verdict.VERIFY_FIRST:
                    await auto_backup([target])

        # ── BrowserGuard：浏览器 URL 安全检查（工具层做操作级检查） ──────────
        elif tool.capability == "browser" and request.name == "browser_open":
            bg = getattr(self, "_browser_guard", None)
            if bg is not None:
                url = str(request.arguments.get("url", "")).strip()
                if url:
                    bg_result = bg.check_url(url)
                    if bg_result.action == "block":
                        reason = f"[BrowserGuard] {bg_result.reason}"
                        if state is not None:
                            state.record_failure(reason, "blocked")
                        payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command="")
                        return ToolExecutionResult(success=False, payload=payload, reason=reason)

        context = ToolExecutionContext(
            execute_shell=shell_executor,
            shell_default_cwd=shell_default_cwd,
            workspace_root=workspace_root or str(ROOT_DIR),
            services=services or {},
            adapter=adapter,
            user_id=user_id,
            auth_level=auth_level,
            chat_id=chat_id or "",
            memory=self._memory,
            memory_index=self._memory_index,
        )

        policy_hook = str(tool.metadata.get("policy_hook", "")).strip()
        use_shell_policy = (
            profile_obj.shell_policy_enabled
            and policy_hook == "shell_command"
            and state is not None
            and deps is not None
        )
        command = str(request.arguments.get("command", "")).strip()
        intent = None

        if use_shell_policy:
            if not command:
                reason = "工具参数缺少 command。"
                state.record_failure(reason, "blocked")
                payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command="")
                return ToolExecutionResult(success=False, payload=payload, reason=reason)

            intent = deps.policy.analyze_command(command)
            state.record_intent(intent)
            pre_decision = deps.policy.before_execute(
                constraints=state.constraints,
                intent=intent,
                state=state,
            )
            if pre_decision.action == "require_consent":
                if pre_decision.alternative is not None:
                    state.require_consent(pre_decision.alternative)
                reason = pre_decision.reason or "需要用户确认。"
                if not state.failure_reason:
                    state.record_failure(reason, pre_decision.failure_type)
                payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command=command)
                return ToolExecutionResult(success=False, payload=payload, reason=reason)
            if pre_decision.action == "block":
                reason = pre_decision.reason or "命令被策略拦截。"
                state.record_failure(reason, pre_decision.failure_type)
                payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command=command)
                return ToolExecutionResult(success=False, payload=payload, reason=reason)

        execution = await self._tool_registry.execute(request, context=context)
        if not use_shell_policy:
            return execution

        assert state is not None
        assert deps is not None
        shell_result = execution.shell_result
        if shell_result is None or intent is None:
            reason = execution.reason or "工具执行失败。"
            state.record_failure(reason, "blocked")
            if "blocked" not in execution.payload:
                payload = self._blocked_payload(
                    reason=reason,
                    cwd=shell_default_cwd,
                    command=command,
                )
                return ToolExecutionResult(success=False, payload=payload, reason=reason)
            return execution

        post_decision = deps.policy.after_execute(
            constraints=state.constraints,
            intent=intent,
            state=state,
            result=shell_result,
            shell_allow_sudo=deps.shell_allow_sudo,
        )
        if post_decision.action == "block":
            reason = post_decision.reason or self._shell_failure_reason(shell_result)
            state.record_failure(reason, post_decision.failure_type)
            if post_decision.alternative is not None:
                state.require_consent(post_decision.alternative)
            return execution

        if post_decision.should_verify:
            if event_bus is not None and task_id is not None and chat_id is not None:
                await self._publish_task_event(
                    event_bus,
                    "task.verifying",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="verifying",
                    text="正在验证任务结果。",
                    command=command,
                    tool_name=request.name,
                )
            verification = deps.policy.verify(state.constraints)
            if verification.completed:
                state.mark_completed(verification)
            else:
                state.record_failure(verification.reason, "verification_failed")
        return execution

    async def _execute_tool_call(
        self,
        *,
        tool_call: ToolCallRequest,
        state: ExecutionSessionState,
        deps: RuntimeDeps,
        task_id: str,
        chat_id: str,
        event_bus,
        services: dict[str, Any] | None = None,
        profile: str | RuntimeProfile = "chat_shell",
        adapter: str = "",
        user_id: str = "",
    ) -> tuple[str, dict[str, Any], bool]:
        execution = await self.execute_tool(
            request=ToolExecutionRequest(name=tool_call.name, arguments=tool_call.arguments),
            profile=profile,
            state=state,
            deps=deps,
            task_id=task_id,
            chat_id=chat_id,
            event_bus=event_bus,
            services=services,
            adapter=adapter,
            user_id=user_id,
        )
        payload = execution.payload
        tool_result_text = self._format_tool_result_for_llm(
            tool_name=tool_call.name,
            payload=payload,
        )
        return tool_result_text, payload, execution.success

    def _format_tool_result_for_llm(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
    ) -> str:
        """将工具结果转换为传回模型的文本，并在必要时裁剪。"""
        normalized_payload: dict[str, Any] = payload
        if tool_name == "web_search":
            normalized_payload = self._compact_web_search_payload(payload)

        rendered = json.dumps(normalized_payload, ensure_ascii=False)
        if len(rendered) <= _TOOL_RESULT_MAX_CHARS:
            return rendered

        # 统一兜底：超长时保留前缀并附带提示，避免向上游发送过大 payload。
        preview_budget = max(0, _TOOL_RESULT_MAX_CHARS - 260)
        preview = rendered[:preview_budget]
        fallback = {
            "_truncated": True,
            "_tool": tool_name,
            "_original_chars": len(rendered),
            "_preview": preview,
            "_note": "工具输出过长，已截断。若需要完整内容，请让模型缩小范围或再次调用工具获取局部结果。",
        }
        return json.dumps(fallback, ensure_ascii=False)

    def _compact_web_search_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """压缩 web_search 输出，优先保留标题/URL/摘要。"""
        results = payload.get("results")
        if not isinstance(results, list):
            return payload

        compact_results: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            snippet = str(item.get("snippet", ""))
            if len(snippet) > _WEB_SEARCH_SNIPPET_MAX_CHARS:
                snippet = snippet[:_WEB_SEARCH_SNIPPET_MAX_CHARS] + "..."
            entry: dict[str, Any] = {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": snippet,
            }
            if item.get("published_date"):
                entry["published_date"] = item["published_date"]
            if item.get("relevance_score") is not None:
                entry["relevance_score"] = item["relevance_score"]
            compact_results.append(entry)

        compact_payload: dict[str, Any] = {
            "query": str(payload.get("query", "")),
            "count": len(compact_results),
            "results": compact_results,
        }
        if "_system_hint" in payload:
            compact_payload["_system_hint"] = payload["_system_hint"]
        return compact_payload

    def _new_loop_detection_state(self) -> LoopDetectionState:
        return LoopDetectionState(
            history=deque(maxlen=self._loop_detection_config.history_size),
        )

    def _generic_repeat_count(
        self,
        *,
        loop_detection_state: LoopDetectionState,
        current_signature: tuple[str, str],
    ) -> int:
        count = 1
        for previous_signature in reversed(loop_detection_state.history):
            if previous_signature != current_signature:
                break
            count += 1
        return count

    def _record_tool_signature(
        self,
        *,
        loop_detection_state: LoopDetectionState,
        signature: tuple[str, str],
    ) -> None:
        loop_detection_state.history.append(signature)

    def _should_emit_generic_repeat_warning(self, repeat_count: int) -> bool:
        if not self._loop_detection_config.enabled:
            return False
        if not self._loop_detection_config.detector_generic_repeat:
            return False
        return repeat_count >= self._loop_detection_config.warning_threshold

    def _should_block_by_global_circuit_breaker(self, repeat_count: int) -> bool:
        if not self._loop_detection_config.enabled:
            return False
        if not self._loop_detection_config.detector_generic_repeat:
            return False
        return repeat_count >= self._loop_detection_config.global_circuit_breaker_threshold

    def _ping_pong_count(
        self,
        *,
        loop_detection_state: LoopDetectionState,
        current_signature: tuple[str, str],
    ) -> int:
        """检测 A→B→A→B 交替重复模式，返回交替轮次数。"""
        history = loop_detection_state.history
        if len(history) < 3:
            return 0
        prev = history[-1]
        if prev == current_signature:
            return 0  # 连续重复，由 genericRepeat 覆盖
        # 从末尾往前检查 ...B→A→B→A 模式（当前即将追加 current）
        # history 末尾应为 ...A, B, A, B，当前是 A
        count = 1  # 当前这对 (prev, current) 算一轮
        idx = len(history) - 1
        while idx >= 1:
            if history[idx] != prev or history[idx - 1] != current_signature:
                break
            count += 1
            idx -= 2
        return count

    def _should_emit_ping_pong_warning(self, ping_pong_count: int) -> bool:
        if not self._loop_detection_config.enabled:
            return False
        if not self._loop_detection_config.detector_ping_pong:
            return False
        return ping_pong_count >= self._loop_detection_config.warning_threshold

    def _should_block_by_ping_pong(self, ping_pong_count: int) -> bool:
        if not self._loop_detection_config.enabled:
            return False
        if not self._loop_detection_config.detector_ping_pong:
            return False
        return ping_pong_count >= self._loop_detection_config.global_circuit_breaker_threshold

    def _record_tool_loop_latency(
        self,
        *,
        round_started_at: float,
        tool_names: list[str],
    ) -> None:
        if self._latency_monitor is None:
            return

        duration_ms = max(int((time.perf_counter() - round_started_at) * 1000), 0)
        bucket = self._tool_loop_bucket(tool_names)
        try:
            self._latency_monitor.record_tool_loop_round(
                bucket=bucket,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.warning("[runtime] 记录 tool loop 延迟失败: %s", exc)

    def _tool_loop_bucket(self, tool_names: list[str]) -> str:
        for name in tool_names:
            normalized = str(name).strip().lower()
            if any(keyword in normalized for keyword in ("search", "web", "browser", "crawl", "fetch")):
                return "web_search"
        return "shell_local"

    def _tool_args_hash(self, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _text_utf8_bytes(self, value: Any) -> int:
        if not isinstance(value, str):
            return 0
        return len(value.encode("utf-8"))

    def _tool_execution_is_error(
        self,
        *,
        payload: dict[str, Any],
        execution_success: bool,
    ) -> bool:
        if not execution_success:
            return True
        if payload.get("blocked") or payload.get("timed_out"):
            return True
        try:
            return_code = int(payload.get("return_code", 0))
        except (TypeError, ValueError):
            return False
        return return_code != 0

    def _compress_browser_history(self, messages: list[dict[str, Any]]) -> None:
        """压缩旧的浏览器 PageState，保留最新完整，旧的替换为摘要。

        浏览器操作是多轮 tool call，每轮 PageState ~2000-3000 token。
        压缩策略：找到所有包含 "[页面]" 标记的 tool result，
        保留最后一个完整，之前的替换为 "[已浏览] title" 摘要。
        仅在浏览器 tool result 累计超过 3 条时触发。
        """
        # 收集含 PageState 的 tool result 消息的索引
        page_state_indices: list[int] = []
        for i, msg in enumerate(messages):
            content = ""
            if isinstance(msg, dict):
                content = str(msg.get("content", ""))
            if "[页面]" in content and "URL:" in content:
                page_state_indices.append(i)

        if len(page_state_indices) <= 3:
            return

        # 保留最后一个完整，压缩之前的
        for idx in page_state_indices[:-1]:
            msg = messages[idx]
            if not isinstance(msg, dict):
                continue
            old_content = str(msg.get("content", ""))
            # 提取标题行
            title = ""
            for line in old_content.split("\n"):
                if line.startswith("[页面]"):
                    title = line.replace("[页面]", "").strip()
                    break
            msg["content"] = f"[已浏览] {title}" if title else "[已浏览]"

    def _detect_simulated_tool_call(self, text: str | None, available_tools: list[str]) -> bool:
        """检测 LLM 是否在文本中描述了工具调用而没有真正调用。"""
        if not text or not available_tools:
            return False

        text_lower = text.lower()
        for tool_name in available_tools:
            if tool_name not in text_lower:
                continue
            for pattern in (
                f"用 {tool_name}", f"使用 {tool_name}", f"调用 {tool_name}",
                f"call {tool_name}", f"use {tool_name}",
            ):
                if pattern in text_lower:
                    return True

        if '"tool"' in text or '"function"' in text or '"name"' in text:
            import re
            if re.search(r'\{\s*"(tool|function|name)"\s*:', text):
                return True

        return False

    def _blocked_payload(
        self,
        *,
        reason: str,
        cwd: str,
        command: str,
    ) -> dict[str, Any]:
        return {
            "command": command,
            "stdout": "",
            "stderr": "",
            "return_code": -1,
            "timed_out": False,
            "blocked": True,
            "reason": reason,
            "cwd": cwd,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    def tool_fallback_reply(self, payload: dict[str, Any] | None) -> str:
        if not payload:
            return "我这次没有整理出可回复的结果。"

        command = str(payload.get("command", "")).strip()
        if payload.get("blocked"):
            return f"本地命令没有执行。{payload.get('reason', '命令被拦截了。')}"

        if payload.get("timed_out"):
            if command:
                return f"本地命令执行超时了：`{command}`。"
            return "本地命令执行超时了。"

        return_code = int(payload.get("return_code", -1))
        if return_code != 0:
            stderr = str(payload.get("stderr", "")).strip()
            if stderr:
                return f"命令执行失败，退出码 {return_code}。\n\n```\n{stderr}\n```"
            return f"命令执行失败，退出码 {return_code}。"

        stdout = str(payload.get("stdout", "")).strip()
        if stdout:
            return f"命令已经执行完了，输出是：\n\n```\n{stdout}\n```"

        return "命令已经执行完了，但没有输出。"

    async def _publish_task_event(
        self,
        event_bus,
        event_type: str,
        *,
        task_id: str,
        chat_id: str,
        phase: str,
        text: str,
        **extra: Any,
    ) -> None:
        if event_bus is None:
            return

        payload: dict[str, Any] = {
            "task_id": task_id,
            "chat_id": chat_id,
            "phase": phase,
            "text": text,
        }
        for key, value in extra.items():
            if value is not None:
                payload[key] = value

        try:
            await event_bus.publish(event_type, payload)
        except Exception as exc:
            logger.warning("[runtime] 发布任务事件失败: %s", exc)
