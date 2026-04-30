"""任务执行运行时：封装 tool loop、工具执行和任务生命周期事件。"""

from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
import re
import time
import uuid
from typing import Any, Awaitable, Callable

from src.utils.loop_detection import (
    LoopDetector as _SharedLoopDetector,
    LoopDetectorConfig as _SharedLoopDetectorConfig,
    tool_args_hash as _shared_tool_args_hash,
)

from config.settings import (
    ROOT_DIR,
    SHELL_DEFAULT_CWD,
    TASK_ERROR_BURST_THRESHOLD,
    TASK_MAX_TOOL_ROUNDS,
    TASK_NO_ACTION_BUDGET,
)
from src.core.llm_router import ToolCallRequest
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    current_chat_id,
    current_iteration_id,
    current_llm_request_id,
    iteration_context,
    new_iteration_id,
)
# Re-export types for backward compatibility
from src.core.task_types import (  # noqa: F401
    ErrorBurstGuard,
    LoopDetectionConfig,
    LoopDetectionState,
    LoopRecoveryState,
    NoActionBudget,
    RuntimeDeps,
    RuntimeOptions,
    TaskLoopStep,
    TaskLoopResult,
    ToolLoopContext,
)
from src.core.llm_exceptions import (
    classify_as_llm_exception,
    PromptTooLongError,
    EmptyResponseError,
    APIOverloadError,
    APITimeoutError,
    APIConnectionError,
)
from src.core.runtime_profiles import (
    COMPOSE_PROACTIVE_PROFILE,
    RuntimeProfile,
    get_runtime_profile,
)
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
from src.core.tool_dispatcher import ServiceContextView

logger = logging.getLogger("lapwing.core.task_runtime")


_MAX_TOOL_ROUNDS = TASK_MAX_TOOL_ROUNDS
_TOOL_RESULT_MAX_CHARS = 12000

# ── Tool result budgeting ────────────────────────────────────────────────────
TOOL_RESULT_BUDGET_MAX_CHARS = 50_000
TOOL_RESULT_PREVIEW_CHARS = 2_000
TOOL_RESULT_DIR = os.path.join(str(ROOT_DIR), "data", "tool_results")
BUDGET_EXEMPT_TOOLS = frozenset({
    "file_read", "read_file", "file_read_segment", "memory_read",
})

# VitalGuard 对命令类型的分类（模块级常量，避免每次 execute_tool() 重建）
_SHELL_TOOLS: frozenset[str] = frozenset({"execute_shell", "run_python_code"})
_FILE_WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file", "file_write", "file_append", "apply_workspace_patch",
})

def _sanitize_visible_text(text: str) -> str:
    """从可见文本中移除调试日志残留，防止工具调用描述泄露给用户。"""
    from src.core.output_sanitizer import sanitize_outgoing
    text = re.sub(r"\[调用\s+\w+\s*(?:工具)?[^\]]*\]", "", text)
    text = re.sub(r"\[/函数调用结果\]", "", text)
    text = re.sub(r"\[函数调用结果\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = sanitize_outgoing(text)
    return text.strip()


# ── 模拟工具调用检测（模块级辅助函数）──

_SIMULATED_TOOL_PATTERNS = [
    re.compile(r"\[调用\s+\w+[:\s(].*?[\])]"),
    re.compile(r"\[tool_call:\s*.*?\]", re.IGNORECASE),
    re.compile(r"\[calling\s+\w+[:\s].*?\]", re.IGNORECASE),
]


def _contains_simulated_tool_call(text: str) -> bool:
    """检测文本中是否包含模拟的工具调用。"""
    return any(p.search(text) for p in _SIMULATED_TOOL_PATTERNS)


def _strip_simulated_tool_calls(text: str) -> str:
    """移除文本中的模拟工具调用。"""
    for p in _SIMULATED_TOOL_PATTERNS:
        text = p.sub("", text)
    return text.strip()


def _truncate_result(payload: Any, max_chars: int = 800) -> str:
    """将工具结果 payload 序列化并截断，供 dispatcher SSE 广播使用（预览，非持久化）。"""
    if payload is None:
        return ""
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            text = str(payload)
    if len(text) > max_chars:
        return text[:max_chars] + "...（截断）"
    return text


class TaskRuntime:
    """负责执行工具轮次、统一工具执行和任务级事件发布。"""

    def __init__(
        self,
        router,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
        tool_registry: ToolRegistry | None = None,
        loop_detection_config: LoopDetectionConfig | None = None,
        latency_monitor: Any | None = None,
        no_action_budget: int | None = None,
        error_burst_threshold: int | None = None,
        on_circuit_breaker_open: Callable[[str, int], None] | None = None,
    ) -> None:
        self._router = router
        self._max_tool_rounds = max_tool_rounds
        self._tool_registry = tool_registry or build_default_tool_registry()
        self._pending_shell_confirmations: dict[str, PendingShellConfirmation] = {}
        self._loop_detection_config = loop_detection_config or LoopDetectionConfig()
        self._latency_monitor = latency_monitor
        # 从 config.settings 直接读取（支持测试时动态修改）
        import config.settings as _cfg
        self._no_action_budget = no_action_budget if no_action_budget is not None else _cfg.TASK_NO_ACTION_BUDGET
        self._error_burst_threshold = error_burst_threshold if error_burst_threshold is not None else _cfg.TASK_ERROR_BURST_THRESHOLD
        self._memory_index: Any | None = None
        # 断路器触发时的回调，签名：(tool_name, repeat_count) → None
        self.on_circuit_breaker_open: Callable[[str, int], None] | None = on_circuit_breaker_open
        
        from src.core.tool_dispatcher import ToolDispatcher
        self.tool_dispatcher = ToolDispatcher(self)

    def set_browser_guard(self, browser_guard: Any | None) -> None:
        self._browser_guard = browser_guard

    def set_latency_monitor(self, latency_monitor: Any | None) -> None:
        self._latency_monitor = latency_monitor

    def set_memory_index(self, memory_index: Any | None) -> None:
        self._memory_index = memory_index

    def set_checkpoint_manager(self, manager: Any | None) -> None:
        self._checkpoint_manager = manager

    # -- 公开属性，供 Agent 使用 --

    @property
    def llm_router(self):
        """LLM 路由器实例。"""
        return self._router

    @property
    def tool_registry(self) -> ToolRegistry:
        """工具注册表实例。"""
        return self._tool_registry

    def create_agent_context(self, agent_name: str) -> ToolExecutionContext:
        """为子 Agent 创建工具执行上下文（TRUSTED 权限）。"""

        async def _noop_shell(cmd: str):
            return ShellResult(stdout="", stderr="Shell disabled for agents", return_code=1)

        return ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd=SHELL_DEFAULT_CWD,
            workspace_root=SHELL_DEFAULT_CWD,
            services={},
            adapter="agent",
            user_id=f"agent:{agent_name}",
            auth_level=1,  # TRUSTED
            chat_id=f"agent-{agent_name}",
            memory=None,
            memory_index=self._memory_index,
        )

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
        if profile.tool_names:
            specs = self._tool_registry.get_tools_for_profile(
                profile,
                include_internal=include_internal,
            )
        elif profile.capabilities:
            specs = self._tool_registry.list_tools(
                capabilities=set(profile.capabilities),
                include_internal=include_internal,
            )
        else:
            # Explicit zero-tool surface (e.g. ZERO_TOOLS_PROFILE) —
            # empty tool_names + empty capabilities means "no tools",
            # not "no filter".
            specs = []
        names = {spec.name for spec in specs}
        exclude = set(getattr(profile, "exclude_tool_names", frozenset()))
        return names - exclude

    def tools_for_profile(self, profile: str | RuntimeProfile) -> list[dict[str, Any]]:
        profile_obj = self._resolve_profile(profile)
        return self._tool_registry.function_tools_for_profile(profile_obj)

    _BROWSER_TOOL_NAMES: frozenset[str] = frozenset({
        "browser_open", "browser_click", "browser_type", "browser_select",
        "browser_scroll", "browser_screenshot", "browser_get_text",
        "browser_back", "browser_tabs", "browser_switch_tab",
        "browser_close_tab", "browser_wait", "browser_login",
    })

    def chat_tools(
        self,
        shell_enabled: bool,
        *,
        browser_enabled: bool = False,
    ) -> list[dict[str, Any]]:
        """Resolve the proactive-composition tool surface.

        Source of truth for the always-on tool list is
        ``COMPOSE_PROACTIVE_PROFILE`` (defined in runtime_profiles.py) —
        this method layers shell / browser / ambient-knowledge
        capabilities on top based on caller flags, but the base names
        are not duplicated here.

        Web retrieval is *not* a layered capability anymore: every
        external-info query goes through delegate_to_researcher, which
        is part of the base profile. Pre-refactor callers passing
        ``web_enabled=True`` are silently ignored.

        send_message is included so proactive paths (inner ticks,
        compose_proactive) can talk to the user; direct chat replies
        use bare model text and never go through this surface.
        """
        # Start from the centralized profile.
        base_specs = self._tool_registry.get_tools_for_profile(
            COMPOSE_PROACTIVE_PROFILE,
            include_internal=False,
        )
        tool_names: set[str] = {spec.name for spec in base_specs}

        if shell_enabled:
            tool_names.update({"execute_shell", "read_file", "write_file"})
        # Ambient knowledge tools — only when registered (subsystem optional).
        for ambient_tool in (
            "prepare_ambient_knowledge",
            "check_ambient_knowledge",
            "manage_interest_profile",
        ):
            if self._tool_registry.get(ambient_tool) is not None:
                tool_names.add(ambient_tool)
        if browser_enabled:
            for name in self._BROWSER_TOOL_NAMES:
                if self._tool_registry.get(name) is not None:
                    tool_names.add(name)

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
        on_interim_text: Callable[..., "Awaitable[None]"] | None = None,
        on_typing: Callable[[], "Awaitable[None]"] | None = None,
        adapter: str = "",
        user_id: str = "",
        send_fn: Callable[[str], "Awaitable[Any]"] | None = None,
        focus_id: str | None = None,
        runtime_options: RuntimeOptions | None = None,
    ) -> str:
        svc = ServiceContextView(services or {})
        mutation_log: StateMutationLog | None = svc.mutation_log
        iteration_id = new_iteration_id()
        iter_start_mono = time.monotonic()
        end_reason = "completed"

        if mutation_log is not None:
            try:
                await mutation_log.record(
                    MutationType.ITERATION_STARTED,
                    {
                        "iteration_id": iteration_id,
                        "trigger_type": "user_message" if adapter else "internal",
                        "trigger_detail": {
                            "adapter": adapter,
                            "user_id": user_id,
                            "chat_id": chat_id,
                        },
                    },
                    iteration_id=iteration_id,
                    chat_id=chat_id,
                )
            except Exception:
                logger.warning("ITERATION_STARTED mutation record failed", exc_info=True)

        try:
            with iteration_context(iteration_id, chat_id=chat_id):
                reply = await self._complete_chat_body(
                    chat_id=chat_id,
                    messages=messages,
                    constraints=constraints,
                    tools=tools,
                    deps=deps,
                    status_callback=status_callback,
                    event_bus=event_bus,
                    on_consent_required=on_consent_required,
                    services=services,
                    profile=profile,
                    on_interim_text=on_interim_text,
                    on_typing=on_typing,
                    adapter=adapter,
                    user_id=user_id,
                    send_fn=send_fn,
                    focus_id=focus_id,
                    runtime_options=runtime_options,
                )
                # Step 5 cleanup: direct-output chat owns user-visible text;
                # commit_promise tracks intent, and audit lives in
                # CommitmentStore + StateMutationLog.
                return reply
        except Exception:
            end_reason = "error"
            raise
        finally:
            duration_ms = (time.monotonic() - iter_start_mono) * 1000
            if mutation_log is not None:
                try:
                    rows = await mutation_log.query_by_iteration(iteration_id)
                    llm_calls = sum(1 for r in rows if r.event_type == MutationType.LLM_REQUEST.value)
                    tool_calls = sum(1 for r in rows if r.event_type == MutationType.TOOL_CALLED.value)
                    await mutation_log.record(
                        MutationType.ITERATION_ENDED,
                        {
                            "iteration_id": iteration_id,
                            "duration_ms": duration_ms,
                            "end_reason": end_reason,
                            "llm_calls_count": llm_calls,
                            "tool_calls_count": tool_calls,
                        },
                        iteration_id=iteration_id,
                        chat_id=chat_id,
                    )
                except Exception:
                    logger.warning("ITERATION_ENDED mutation record failed", exc_info=True)

    async def _complete_chat_body(
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
        on_interim_text: Callable[..., "Awaitable[None]"] | None = None,
        on_typing: Callable[[], "Awaitable[None]"] | None = None,
        adapter: str = "",
        user_id: str = "",
        send_fn: Callable[[str], "Awaitable[Any]"] | None = None,
        focus_id: str | None = None,
        runtime_options: RuntimeOptions | None = None,
    ) -> str:
        """Original complete_chat body. Wrapped by complete_chat() which binds
        the iteration context and records ITERATION_STARTED / ITERATION_ENDED.
        """
        # Resolve per-call budget overrides (e.g. inner_tick's tighter loop).
        opts = runtime_options or RuntimeOptions()
        max_tool_rounds = opts.max_tool_rounds if opts.max_tool_rounds is not None else self._max_tool_rounds
        no_action_budget_value = (
            opts.no_action_budget if opts.no_action_budget is not None else self._no_action_budget
        )
        error_burst_threshold = (
            opts.error_burst_threshold if opts.error_burst_threshold is not None else self._error_burst_threshold
        )
        if not tools:
            await self._emit_status(status_callback, chat_id, "stage:planning")
            reply = await self._router.complete(
                messages,
                slot="main_conversation",
                session_key=f"chat:{chat_id}",
                origin="task_runtime.chat",
            )
            await self._emit_status(status_callback, chat_id, "stage:finalizing")
            return reply

        profile_obj = self._resolve_profile(profile)
        state = ExecutionSessionState(constraints=constraints)
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        await self._emit_status(status_callback, chat_id, "stage:planning")
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

        ctx = ToolLoopContext(
            messages=messages,
            tools=tools,
            constraints=constraints,
            chat_id=chat_id,
            task_id=task_id,
            deps=deps,
            profile_obj=profile_obj,
            status_callback=status_callback,
            event_bus=event_bus,
            on_consent_required=on_consent_required,
            on_interim_text=on_interim_text,
            on_typing=on_typing,
            services=services,
            adapter=adapter,
            user_id=user_id,
            send_fn=send_fn,
            focus_id=focus_id,
            state=state,
            loop_detection_state=self._new_loop_detection_state(),
            recovery=LoopRecoveryState(),
            no_action_budget=NoActionBudget(
                default=no_action_budget_value,
                remaining=no_action_budget_value,
            ),
            error_guard=ErrorBurstGuard(threshold=error_burst_threshold),
        )

        loop_result = await self.run_task_loop(
            max_rounds=max_tool_rounds,
            step_runner=lambda round_index: self._run_step(ctx, round_index),
        )

        # ── Loop 完成摘要日志 —— 结构化版由 MutationLog ITERATION_ENDED 提供 ──
        recovery = ctx.recovery
        logger.debug(
            "[runtime] Tool loop completed: turns=%d compact=%d output_recovery=%d "
            "api_retries=%d total_result_chars=%d reason=%s",
            recovery.turn_count,
            recovery.reactive_compact_attempts,
            recovery.max_output_recovery_count,
            recovery.consecutive_api_errors,
            recovery.total_result_chars,
            loop_result.reason or "normal",
        )
        if ctx.final_reply is not None:
            # 清理最终回复中可能残留的内部标记
            from src.core.output_sanitizer import sanitize_outgoing
            final_reply = sanitize_outgoing(ctx.final_reply)
            return final_reply

        logger.warning("[runtime] tool call 循环超过上限，返回兜底说明")

        if ctx.state.consent_required:
            await self._publish_task_event(
                event_bus,
                "task.blocked",
                task_id=task_id,
                chat_id=chat_id,
                phase="blocked",
                text="任务被阻塞，等待用户确认替代路径。",
                reason=ctx.state.failure_reason or "需要用户确认",
            )
            if on_consent_required is not None:
                await self._emit_status(status_callback, chat_id, "stage:finalizing")
                return on_consent_required(ctx.state)
            await self._emit_status(status_callback, chat_id, "stage:finalizing")
            return ctx.state.consent_message()

        if ctx.state.completed:
            await self._publish_task_event(
                event_bus,
                "task.completed",
                task_id=task_id,
                chat_id=chat_id,
                phase="completed",
                text="任务执行并验证完成。",
            )
            await self._emit_status(status_callback, chat_id, "stage:finalizing")
            return ctx.state.success_message()

        # ── max_rounds_exceeded 路径：触发完成度检查 ──
        _termination = ctx.state.failure_reason or loop_result.reason or "max_rounds_exceeded"

        if ctx.state.constraints.is_write_request and ctx.state.constraints.objective != "generic":
            await self._publish_task_event(
                event_bus,
                "task.failed",
                task_id=task_id,
                chat_id=chat_id,
                phase="failed",
                text="任务未完成。",
                reason=_termination,
            )
            await self._emit_status(status_callback, chat_id, "stage:finalizing")
            _reply = ctx.state.failure_message()
        else:
            await self._publish_task_event(
                event_bus,
                "task.failed",
                task_id=task_id,
                chat_id=chat_id,
                phase="failed",
                text="任务未在轮次上限内完成，返回兜底结果。",
                reason="tool 循环超过上限",
            )
            await self._emit_status(status_callback, chat_id, "stage:finalizing")
            _reply = self.tool_fallback_reply(loop_result.last_payload)

        return _reply

    @staticmethod
    async def _emit_status(status_callback, chat_id: str, text: str) -> None:
        if status_callback is None:
            return
        try:
            await status_callback(chat_id, text)
        except Exception:
            pass

    async def _run_step(self, ctx: ToolLoopContext, round_index: int) -> TaskLoopStep:
        """单轮工具循环步骤（从 complete_chat._step_runner 提取）。"""
        ctx.recovery.record_transition("tool_turn")
        round_started_at = time.perf_counter()

        try:
            messages_with_state = self._with_shell_state_context(ctx.messages, ctx.state)
            messages_with_state = self._with_plan_context(messages_with_state, ctx.services)
            turn = await self._router.complete_with_tools(
                messages_with_state,
                tools=ctx.tools,
                slot="main_conversation",
                session_key=f"chat:{ctx.chat_id}",
                origin="task_runtime.chat",
            )
            ctx.recovery.reset_api_errors()
        except Exception as raw_exc:
            typed = classify_as_llm_exception(raw_exc)
            if typed is None:
                raise

            if isinstance(typed, PromptTooLongError) and ctx.recovery.can_reactive_compact():
                ctx.recovery.reactive_compact_attempts += 1
                ctx.recovery.record_transition("reactive_compact")
                logger.warning(
                    "[runtime] Prompt too long, reactive compact (attempt %d/%d)",
                    ctx.recovery.reactive_compact_attempts,
                    ctx.recovery.MAX_REACTIVE_COMPACT,
                )
                self._reactive_compact(ctx.messages)
                return TaskLoopStep()

            if isinstance(typed, (APIOverloadError, APITimeoutError, APIConnectionError)):
                if ctx.recovery.can_retry_api():
                    ctx.recovery.consecutive_api_errors += 1
                    ctx.recovery.record_transition("api_retry")
                    wait = 2 ** ctx.recovery.consecutive_api_errors
                    logger.warning(
                        "[runtime] API error (%s), retrying in %ds (attempt %d/%d)",
                        type(typed).__name__,
                        wait,
                        ctx.recovery.consecutive_api_errors,
                        ctx.recovery.MAX_CONSECUTIVE_API_ERRORS,
                    )
                    await asyncio.sleep(wait)
                    return TaskLoopStep()

            raise

        # ── 空响应恢复 ──
        if not turn.tool_calls and not (turn.text or "").strip():
            if ctx.recovery.can_output_recovery():
                ctx.recovery.max_output_recovery_count += 1
                ctx.recovery.record_transition("output_recovery")
                logger.warning(
                    "[runtime] Empty response, injecting continue (attempt %d/%d)",
                    ctx.recovery.max_output_recovery_count,
                    ctx.recovery.MAX_OUTPUT_RECOVERY,
                )
                ctx.messages.append({
                    "role": "user",
                    "content": "请继续你刚才的回答。",
                })
                return TaskLoopStep()

        if not turn.tool_calls:
            # ── 模拟工具调用检测 ──
            model_text = (turn.text or "").strip()
            available_tool_names = [t["function"]["name"] for t in ctx.tools]
            if ctx.simulated_tool_retries < 1 and model_text:
                if self._detect_simulated_tool_call(model_text, available_tool_names):
                    ctx.simulated_tool_retries += 1
                    logger.info("[runtime] 检测到模拟工具调用，注入提醒（retry %d）", ctx.simulated_tool_retries)
                    ctx.messages.append({
                        "role": "user",
                        "content": (
                            "[系统提醒] 你刚才在文字中描述了工具调用，但没有真正调用。"
                            "请直接使用工具，不要用文字描述。"
                        ),
                    })
                    return TaskLoopStep()

            # ── No-Action Budget（仅在 LLM 曾使用工具后激活）──
            if ctx.has_used_tools and ctx.no_action_budget.consume():
                logger.debug(
                    "[runtime] No-action budget consumed (remaining=%d), continuing loop",
                    ctx.no_action_budget.remaining,
                )
                return TaskLoopStep()
            if ctx.has_used_tools and ctx.no_action_budget.exhausted:
                logger.info(
                    "No-action budget exhausted after %d consecutive no-action turns",
                    ctx.no_action_budget.default,
                )
            await self._emit_status(ctx.status_callback, ctx.chat_id, "stage:finalizing")
            ctx.final_reply = await self._finalize_without_tool_calls(
                chat_id=ctx.chat_id,
                task_id=ctx.task_id,
                state=ctx.state,
                model_text=turn.text,
                last_payload=ctx.last_payload,
                event_bus=ctx.event_bus,
                on_consent_required=ctx.on_consent_required,
            )
            return TaskLoopStep(completed=True, payload=ctx.last_payload)

        # LLM 返回了 tool_call — 重置 no-action 预算并标记
        ctx.has_used_tools = True
        ctx.no_action_budget.reset()
        # 工具调用伴随的文字是内部推理，不发给用户
        interim_text = (turn.text or "").strip()
        if interim_text:
            logger.debug(
                "Interim text alongside tool calls: %d chars (not sent to user)",
                len(interim_text),
            )

        tool_names = [tool_call.name for tool_call in turn.tool_calls]
        executed_tool_names: list[str] = []

        def _record_round_latency() -> None:
            names = executed_tool_names if executed_tool_names else tool_names
            self._record_tool_loop_latency(
                round_started_at=round_started_at,
                tool_names=names,
            )

        if len(turn.tool_calls) > 1:
            logger.debug(
                "[runtime] 模型返回了 %s 个 tool calls，当前按顺序串行执行。",
                len(turn.tool_calls),
            )

        if turn.continuation_message is not None:
            ctx.messages.append(turn.continuation_message)

        tool_results: list[tuple[ToolCallRequest, str]] = []
        last_tool_name: str | None = None
        for tool_index, tool_call in enumerate(turn.tool_calls):
            await self._emit_status(ctx.status_callback, ctx.chat_id,
                f"stage:executing:{tool_call.name}:{tool_index + 1}:{len(turn.tool_calls)}"
            )
            tool_args_hash = self._tool_args_hash(tool_call.arguments)
            tool_signature = (tool_call.name, tool_args_hash)
            generic_repeat_count = self._generic_repeat_count(
                loop_detection_state=ctx.loop_detection_state,
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
                    ctx.event_bus,
                    "task.executing",
                    task_id=ctx.task_id,
                    chat_id=ctx.chat_id,
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
                # 通知外部观察者（如 CorrectionManager）断路器已触发
                if self.on_circuit_breaker_open is not None:
                    try:
                        self.on_circuit_breaker_open(tool_call.name, generic_repeat_count)
                    except Exception:
                        logger.debug("[runtime] on_circuit_breaker_open 回调异常", exc_info=True)
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
                ctx.state.record_failure(reason, "blocked")
                await self._publish_task_event(
                    ctx.event_bus,
                    "task.blocked",
                    task_id=ctx.task_id,
                    chat_id=ctx.chat_id,
                    phase="blocked",
                    text="检测到无进展重复循环，已停止自动执行，等待用户介入。",
                    reason=reason,
                    **tool_event_common,
                    **loop_detection_common,
                )
                ctx.final_reply = (
                    "检测到无进展重复循环（同一工具与参数连续重复），"
                    "我已停止当前自动执行，需用户介入。请提供新的策略或更具体的指令后我再继续。"
                )
                await self._emit_status(ctx.status_callback, ctx.chat_id, "stage:finalizing")
                _record_round_latency()
                return TaskLoopStep(completed=True, payload=ctx.last_payload)

            # ── ping-pong 检测（A→B→A→B 交替模式）──────────────────────
            ping_pong_count = self._ping_pong_count(
                loop_detection_state=ctx.loop_detection_state,
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
                ctx.state.record_failure(reason, "blocked")
                await self._publish_task_event(
                    ctx.event_bus,
                    "task.blocked",
                    task_id=ctx.task_id,
                    chat_id=ctx.chat_id,
                    phase="blocked",
                    text="检测到无进展交替循环，已停止自动执行，等待用户介入。",
                    reason=reason,
                    **tool_event_common,
                )
                ctx.final_reply = (
                    "检测到无进展交替循环（两个工具交替重复调用），"
                    "我已停止当前自动执行，需用户介入。请提供新的策略或更具体的指令后我再继续。"
                )
                await self._emit_status(ctx.status_callback, ctx.chat_id, "stage:finalizing")
                _record_round_latency()
                return TaskLoopStep(completed=True, payload=ctx.last_payload)

            # 当前先走串行执行，后续可按 provider 能力升级到并行分发。
            await self._publish_task_event(
                ctx.event_bus,
                "task.executing",
                task_id=ctx.task_id,
                chat_id=ctx.chat_id,
                phase="executing",
                text=f"正在执行工具：{tool_call.name}",
                **tool_event_common,
            )
            await self._publish_task_event(
                ctx.event_bus,
                "task.tool_execution_start",
                task_id=ctx.task_id,
                chat_id=ctx.chat_id,
                phase="executing",
                text=f"工具开始执行：{tool_call.name}",
                stdoutBytes=0,
                stderrBytes=0,
                isError=False,
                durationMs=0,
                **tool_event_common,
            )

            # 工具执行前触发 typing indicator
            if ctx.on_typing is not None:
                try:
                    await ctx.on_typing()
                except Exception:
                    pass

            tool_started_at = time.perf_counter()
            execute_kwargs: dict[str, Any] = {
                "tool_call": tool_call,
                "state": ctx.state,
                "deps": ctx.deps,
                "task_id": ctx.task_id,
                "chat_id": ctx.chat_id,
                "event_bus": ctx.event_bus,
                "profile": ctx.profile_obj,
                "services": ctx.services,
                "adapter": ctx.adapter,
                "user_id": ctx.user_id,
                "send_fn": ctx.send_fn,
            }
            if ctx.focus_id is not None:
                execute_kwargs["focus_id"] = ctx.focus_id
            tool_result_text, payload, execution_success = await self._execute_tool_call(
                **execute_kwargs
            )
            duration_ms = max(int((time.perf_counter() - tool_started_at) * 1000), 0)
            logger.debug(
                "tool_call execute: tool=%s success=%s duration=%.2fs",
                tool_call.name, execution_success, duration_ms / 1000,
            )
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
                ctx.event_bus,
                "task.tool_execution_update",
                task_id=ctx.task_id,
                chat_id=ctx.chat_id,
                phase="executing",
                text=f"工具执行进度：{tool_call.name}",
                **tool_event_common,
                **tool_event_metrics,
            )
            await self._publish_task_event(
                ctx.event_bus,
                "task.tool_execution_end",
                task_id=ctx.task_id,
                chat_id=ctx.chat_id,
                phase="executing",
                text=f"工具执行结束：{tool_call.name}",
                **tool_event_common,
                **tool_event_metrics,
            )
            # ── Error Burst Guard ──
            if execution_success:
                ctx.error_guard.record_success()
            else:
                should_break = ctx.error_guard.record_error(
                    tool_result_text[:200] if tool_result_text else "unknown error"
                )
                if should_break:
                    logger.warning(
                        "[runtime] Error burst guard triggered: %s",
                        ctx.error_guard.summary,
                    )
                    tool_results.append((tool_call, tool_result_text))
                    executed_tool_names.append(tool_call.name)
                    # 先把已收集的 tool_results 写入 history（保证 tool_use → tool_result 配对完整）
                    if tool_results:
                        result_message = self._router.build_tool_result_message(
                            slot="main_conversation",
                            tool_results=tool_results,
                            session_key=f"chat:{ctx.chat_id}",
                        )
                        if isinstance(result_message, list):
                            ctx.messages.extend(result_message)
                        else:
                            ctx.messages.append(result_message)
                    # 注入错误摘要让 LLM 在下一轮有机会调整策略
                    ctx.messages.append({
                        "role": "user",
                        "content": (
                            f"[系统警告] 连续 {ctx.error_guard.threshold} 次工具调用失败。"
                            f"{ctx.error_guard.summary}\n"
                            "请换一种方法或放弃这个子任务。如果继续尝试同样的方法仍然失败，我将终止执行。"
                        ),
                    })
                    _record_round_latency()
                    return TaskLoopStep(payload=ctx.last_payload)

            tool_results.append((tool_call, tool_result_text))
            executed_tool_names.append(tool_call.name)
            self._record_tool_signature(
                loop_detection_state=ctx.loop_detection_state,
                signature=tool_signature,
            )
            ctx.last_payload = payload

            last_tool_name = tool_call.name
            logger.debug(
                "[runtime] 第 %s 轮完成 tool call %s/%s: %s",
                round_index + 1,
                tool_index + 1,
                len(turn.tool_calls),
                tool_call.name,
            )

            if ctx.state.consent_required:
                await self._publish_task_event(
                    ctx.event_bus,
                    "task.blocked",
                    task_id=ctx.task_id,
                    chat_id=ctx.chat_id,
                    phase="blocked",
                    text="任务被阻塞，等待用户确认替代路径。",
                    reason=ctx.state.failure_reason or "需要用户确认",
                    tool_name=tool_call.name,
                )
                if ctx.on_consent_required is not None:
                    ctx.final_reply = ctx.on_consent_required(ctx.state)
                else:
                    ctx.final_reply = ctx.state.consent_message()
                await self._emit_status(ctx.status_callback, ctx.chat_id, "stage:finalizing")
                _record_round_latency()
                return TaskLoopStep(completed=True, payload=ctx.last_payload)

            if ctx.state.completed:
                break

        result_message = self._router.build_tool_result_message(
            slot="main_conversation",
            tool_results=tool_results,
            session_key=f"chat:{ctx.chat_id}",
        )
        if isinstance(result_message, list):
            ctx.messages.extend(result_message)
        else:
            ctx.messages.append(result_message)

        if ctx.state.completed:
            await self._publish_task_event(
                ctx.event_bus,
                "task.completed",
                task_id=ctx.task_id,
                chat_id=ctx.chat_id,
                phase="completed",
                text="任务执行并验证完成。",
                tool_name=last_tool_name,
            )
            ctx.final_reply = ctx.state.success_message()
            await self._emit_status(ctx.status_callback, ctx.chat_id, "stage:finalizing")
            _record_round_latency()
            return TaskLoopStep(completed=True, payload=ctx.last_payload)

        if ctx.last_payload is not None and ctx.last_payload.get("blocked"):
            await self._publish_task_event(
                ctx.event_bus,
                "task.blocked",
                task_id=ctx.task_id,
                chat_id=ctx.chat_id,
                phase="blocked",
                text="命令被拦截，等待后续恢复步骤。",
                reason=str(ctx.last_payload.get("reason", "命令被拦截。")),
                tool_name=last_tool_name,
            )

        _record_round_latency()

        # 压缩旧的浏览器 PageState（保留最新完整，旧的只留摘要）
        self._compress_browser_history(ctx.messages)
        # Step 4 M4.d: voice reminder is now placed by StateSerializer
        # at the message tail on every render, so re-injection inside
        # the tool loop is unnecessary. The pre-Step-3 helper that lived
        # here was a silently-swallowed no-op and has been removed.

        # ── Loop turn 日志 —— 结构化版由 MutationLog TOOL_* 事件提供 ──
        result_chars = sum(len(t[1]) for t in tool_results) if tool_results else 0
        ctx.recovery.total_result_chars += result_chars
        logger.debug(
            "[runtime] Loop turn=%d transition=%s tool_calls=%d result_chars=%d total_chars=%d",
            ctx.recovery.turn_count,
            ctx.recovery.transition_reason,
            len(executed_tool_names),
            result_chars,
            ctx.recovery.total_result_chars,
        )
        return TaskLoopStep(payload=ctx.last_payload)

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

        reply = _sanitize_visible_text(model_text) if model_text else self.tool_fallback_reply(last_payload)
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

    def _with_plan_context(
        self,
        messages: list[dict[str, Any]],
        services: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        svc = ServiceContextView(services or {})
        plan = svc.plan_state
        if plan is None:
            return messages
        rendered = plan.render()
        if not rendered:
            return messages
        if messages and messages[0].get("role") == "system":
            merged_system = dict(messages[0])
            base_content = str(merged_system.get("content", "")).strip()
            merged_system["content"] = (
                f"{base_content}\n\n{rendered}" if base_content else rendered
            )
            return [merged_system, *messages[1:]]
        plan_message = {"role": "system", "content": rendered}
        return [plan_message, *messages]

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
        send_fn: Callable[[str], "Awaitable[Any]"] | None = None,
        focus_id: str | None = None,
    ) -> ToolExecutionResult:
        return await self.tool_dispatcher.dispatch(
            request=request,
            profile=profile,
            state=state,
            deps=deps,
            task_id=task_id,
            chat_id=chat_id,
            event_bus=event_bus,
            workspace_root=workspace_root,
            services=services,
            adapter=adapter,
            user_id=user_id,
            send_fn=send_fn,
            focus_id=focus_id,
        )

    def _reactive_compact(self, messages: list[dict[str, Any]]) -> None:
        """紧急压缩：context 快满时，清理旧的 tool results。"""
        KEEP_RECENT = 6

        tool_result_indices: list[int] = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            if role == "tool":
                tool_result_indices.append(i)
            elif role == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_result_indices.append(i)
                        break

        if len(tool_result_indices) <= KEEP_RECENT:
            return

        to_clear = tool_result_indices[:-KEEP_RECENT]
        for idx in to_clear:
            msg = messages[idx]
            if msg.get("role") == "tool":
                msg["content"] = "(此工具结果已被清理以节省上下文空间)"
            elif isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        block["content"] = "(已清理)"

        logger.info("[runtime] Reactive compact: cleared %d old tool results", len(to_clear))

    def _budget_tool_result(
        self,
        tool_name: str,
        result: ToolExecutionResult,
    ) -> ToolExecutionResult:
        """大工具结果存磁盘，只留预览在 context。"""
        if tool_name in BUDGET_EXEMPT_TOOLS:
            return result

        payload_str = json.dumps(result.payload, ensure_ascii=False, default=str)
        if len(payload_str) <= TOOL_RESULT_BUDGET_MAX_CHARS:
            return result

        os.makedirs(TOOL_RESULT_DIR, exist_ok=True)

        from src.core.time_utils import now as _tz_now
        ts = _tz_now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{ts}_{tool_name}.txt"
        filepath = os.path.join(TOOL_RESULT_DIR, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(payload_str)
        except OSError as exc:
            logger.warning("[runtime] 写入大结果到磁盘失败: %s", exc)
            return result

        preview = payload_str[:TOOL_RESULT_PREVIEW_CHARS]
        original_len = len(payload_str)
        logger.debug(
            "[runtime] Tool result budgeted: %s, %d chars → preview %d chars, saved to %s",
            tool_name, original_len, len(preview), filepath,
        )

        result.payload = {
            "preview": preview,
            "full_result_path": filepath,
            "truncated": True,
            "original_chars": original_len,
            "note": (
                f"完整结果已保存到 {filepath}（{original_len} 字符）。"
                "如需查看完整内容，请使用 read_file 工具读取该文件。"
            ),
        }
        return result

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
        send_fn: Callable[[str], "Awaitable[Any]"] | None = None,
        focus_id: str | None = None,
    ) -> tuple[str, dict[str, Any], bool]:
        svc = ServiceContextView(services or {})
        dispatcher = svc.dispatcher
        mutation_log: StateMutationLog | None = svc.mutation_log
        circuit_breaker = svc.circuit_breaker
        trajectory_store = svc.trajectory_store
        focus_manager = svc.focus_manager
        iteration_id = current_iteration_id()

        cb_key = ""
        if circuit_breaker is not None:
            cb_key = f"{tool_call.name}:{hashlib.md5(json.dumps(tool_call.arguments, sort_keys=True).encode()).hexdigest()[:8]}"
            allowed, cb_reason = circuit_breaker.should_allow(cb_key)
            if not allowed:
                logger.info("Circuit breaker blocked %s: %s", tool_call.name, cb_reason)
                payload = {"blocked": True, "reason": cb_reason, "tool": tool_call.name}
                return json.dumps(payload, ensure_ascii=False), payload, False

        if dispatcher is not None:
            try:
                preview = json.dumps(tool_call.arguments, ensure_ascii=False)[:500]
                await dispatcher.submit(
                    "tool.called",
                    payload={
                        "tool": tool_call.name,
                        "arguments_preview": preview,
                        "chat_id": chat_id,
                    },
                    actor="lapwing",
                    task_id=task_id,
                )
            except Exception:
                logger.debug("tool.called 事件提交失败", exc_info=True)

        # Mutation log records the durable, un-truncated picture of the call.
        # Dispatcher above is separately responsible for the live UI stream.
        if mutation_log is not None:
            try:
                await mutation_log.record(
                    MutationType.TOOL_CALLED,
                    {
                        "tool_name": tool_call.name,
                        "tool_call_id": getattr(tool_call, "id", None),
                        "arguments": tool_call.arguments,
                        "called_from_iteration": iteration_id,
                        "parent_llm_request_id": current_llm_request_id(),
                        "task_id": task_id,
                        "adapter": adapter,
                        "user_id": user_id,
                    },
                    iteration_id=iteration_id,
                    chat_id=current_chat_id() or chat_id,
                )
            except Exception:
                logger.warning("TOOL_CALLED mutation record failed", exc_info=True)

        if trajectory_store is not None:
            try:
                from src.core.trajectory_store import TrajectoryEntryType

                await trajectory_store.append(
                    TrajectoryEntryType.TOOL_CALL,
                    chat_id,
                    "lapwing",
                    {
                        "tool_name": tool_call.name,
                        "tool_call_id": getattr(tool_call, "id", None),
                        "arguments": tool_call.arguments,
                    },
                    related_iteration_id=iteration_id,
                    related_tool_call_id=getattr(tool_call, "id", None),
                    focus_id=focus_id,
                )
                if focus_id and focus_manager is not None:
                    await focus_manager.accumulate(focus_id)
            except Exception:
                logger.debug("TOOL_CALL trajectory append failed", exc_info=True)

        tool_start_mono = time.monotonic()
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
            send_fn=send_fn,
            focus_id=focus_id,
        )
        elapsed_ms = (time.monotonic() - tool_start_mono) * 1000

        # P0: 大结果存磁盘，只留预览
        execution = self._budget_tool_result(tool_call.name, execution)
        payload = execution.payload
        tool_result_text = self._format_tool_result_for_llm(
            tool_name=tool_call.name,
            payload=payload,
        )

        if dispatcher is not None:
            try:
                await dispatcher.submit(
                    "tool.result",
                    payload={
                        "tool": tool_call.name,
                        "success": execution.success,
                        "reason": (execution.reason or "")[:200],
                        "chat_id": chat_id,
                        "result_preview": _truncate_result(payload, max_chars=800),
                    },
                    actor="lapwing",
                    task_id=task_id,
                )
            except Exception:
                logger.debug("tool.result 事件提交失败", exc_info=True)

        if mutation_log is not None:
            try:
                await mutation_log.record(
                    MutationType.TOOL_RESULT,
                    {
                        "tool_call_id": getattr(tool_call, "id", None),
                        "tool_name": tool_call.name,
                        "success": execution.success,
                        "payload": payload,
                        "reason": execution.reason or "",
                        "elapsed_ms": elapsed_ms,
                        "is_error": not execution.success,
                    },
                    iteration_id=iteration_id,
                    chat_id=current_chat_id() or chat_id,
                )
            except Exception:
                logger.warning("TOOL_RESULT mutation record failed", exc_info=True)

        if trajectory_store is not None:
            try:
                from src.core.trajectory_store import TrajectoryEntryType

                await trajectory_store.append(
                    TrajectoryEntryType.TOOL_RESULT,
                    chat_id,
                    "system",
                    {
                        "tool_name": tool_call.name,
                        "tool_call_id": getattr(tool_call, "id", None),
                        "success": execution.success,
                        "reason": execution.reason or "",
                        "result_preview": _truncate_result(payload, max_chars=1200),
                    },
                    related_iteration_id=iteration_id,
                    related_tool_call_id=getattr(tool_call, "id", None),
                    focus_id=focus_id,
                )
                if focus_id and focus_manager is not None:
                    await focus_manager.accumulate(focus_id)
            except Exception:
                logger.debug("TOOL_RESULT trajectory append failed", exc_info=True)

        if circuit_breaker is not None:
            if execution.success:
                circuit_breaker.record_success(cb_key)
            else:
                circuit_breaker.record_failure(cb_key)

        return tool_result_text, payload, execution.success

    def _format_tool_result_for_llm(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
    ) -> str:
        """将工具结果转换为传回模型的文本，并在必要时裁剪。"""
        rendered = json.dumps(payload, ensure_ascii=False)
        if len(rendered) <= _TOOL_RESULT_MAX_CHARS:
            return rendered

        # 统一兜底：超长时保留前缀，自然收尾。
        # 注意：不能包含任何工程化标记（如 _truncated、原长度），
        # 因为 LLM 可能会把这些内容原样转述给用户。
        preview_budget = max(0, _TOOL_RESULT_MAX_CHARS - 60)
        preview = rendered[:preview_budget]
        return preview + "\n\n（结果太长，只显示了一部分。如果需要更多内容可以再查一次。）"

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
        from src.utils.loop_detection import _generic_repeat_count
        return _generic_repeat_count(loop_detection_state.history, current_signature)

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
        if not self._loop_detection_config.blocking:
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
        from src.utils.loop_detection import _ping_pong_count
        return _ping_pong_count(loop_detection_state.history, current_signature)

    def _should_emit_ping_pong_warning(self, ping_pong_count: int) -> bool:
        if not self._loop_detection_config.enabled:
            return False
        if not self._loop_detection_config.detector_ping_pong:
            return False
        return ping_pong_count >= self._loop_detection_config.warning_threshold

    def _should_block_by_ping_pong(self, ping_pong_count: int) -> bool:
        if not self._loop_detection_config.enabled:
            return False
        if not self._loop_detection_config.blocking:
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
        return _shared_tool_args_hash(arguments)

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

        # 先用模块级 bracket pattern 检测（[调用 xxx: ...] 等）
        if _contains_simulated_tool_call(text):
            return True

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
            if re.search(r'\{\s*"(tool|function|name)"\s*:', text):
                return True

        return False

    # ── AmbientKnowledge 缓存辅助方法 ─────────────────────────────────

    async def _try_ambient_cache(
        self,
        request: ToolExecutionRequest,
        ambient_store: Any,
    ) -> ToolExecutionResult | None:
        """保守的 research 缓存拦截：question 包含已缓存条目的 topic 关键词时命中。"""
        question = str(request.arguments.get("question", "")).strip().lower()
        if not question or len(question) < 4:
            return None
        try:
            all_entries = await ambient_store.get_all_fresh()
        except Exception:
            return None
        for entry in all_entries:
            topic_lower = entry.topic.lower()
            keywords = [w for w in topic_lower.split() if len(w) >= 2][:3]
            if not keywords:
                continue
            if all(kw in question for kw in keywords):
                import json as _json
                try:
                    data = _json.loads(entry.data)
                except Exception:
                    data = {}
                return ToolExecutionResult(
                    success=True,
                    payload={
                        "answer": entry.summary,
                        "evidence": data.get("evidence", []),
                        "confidence": entry.confidence,
                        "source": f"ambient_cache:{entry.key}",
                        "cached_at": entry.fetched_at,
                    },
                    reason=f"ambient_cache_hit:{entry.key}",
                )
        return None

    async def _writeback_to_ambient(
        self,
        request: ToolExecutionRequest,
        execution: ToolExecutionResult,
        ambient_store: Any,
    ) -> None:
        """research 成功后将结果写回 ambient cache（仅高置信度结果）。"""
        import json as _json
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from src.ambient.models import AmbientEntry

        payload = execution.payload
        confidence = float(payload.get("confidence", 0))
        if confidence < 0.5:
            return

        question = str(request.arguments.get("question", "")).strip()
        if not question:
            return

        answer = str(payload.get("answer", ""))
        if not answer:
            return

        now = _dt.now(_tz.utc)
        key = f"research:{hash(question) % 100000:05d}"
        entry = AmbientEntry(
            key=key,
            category="research",
            topic=question[:80],
            data=_json.dumps(payload, ensure_ascii=False, default=str),
            summary=answer[:300],
            fetched_at=now.isoformat(),
            expires_at=(now + _td(hours=4)).isoformat(),
            source="research_writeback",
            confidence=confidence,
        )
        await ambient_store.put(key, entry)

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
