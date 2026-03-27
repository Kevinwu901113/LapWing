"""任务执行运行时：封装 tool loop、工具执行和任务生命周期事件。"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from config.settings import ROOT_DIR, SHELL_DEFAULT_CWD
from src.core.llm_router import ToolCallRequest
from src.core.runtime_profiles import RuntimeProfile, get_runtime_profile
from src.core.shell_policy import (
    ExecutionConstraints,
    ExecutionSessionState,
    PendingShellConfirmation,
    build_followup_message,
    is_confirmation_message,
    is_rejection_message,
)
from src.policy.shell_runtime_policy import ShellRuntimePolicy
from src.tools.registry import ToolRegistry, build_default_tool_registry
from src.tools.shell_executor import ShellResult, execute as default_execute_shell
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.task_runtime")

_MAX_TOOL_ROUNDS = 8


@dataclass(frozen=True)
class RuntimeDeps:
    """tool loop 运行所需的策略与执行依赖。"""

    execute_shell: Callable[[str], Awaitable[ShellResult]]
    policy: ShellRuntimePolicy
    shell_default_cwd: str
    shell_allow_sudo: bool


@dataclass(frozen=True)
class TaskLoopStep:
    completed: bool = False
    stop: bool = False
    reason: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskLoopResult:
    completed: bool
    stopped: bool
    attempts: int
    reason: str = ""
    last_payload: dict[str, Any] | None = None


class TaskRuntime:
    """负责执行工具轮次、统一工具执行和任务级事件发布。"""

    def __init__(
        self,
        router,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._router = router
        self._max_tool_rounds = max_tool_rounds
        self._tool_registry = tool_registry or build_default_tool_registry()
        self._pending_shell_confirmations: dict[str, PendingShellConfirmation] = {}

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

    def chat_tools(self, shell_enabled: bool) -> list[dict[str, Any]]:
        """兼容旧接口：chat 默认使用 shell profile。"""
        if not shell_enabled:
            return []
        return self.tools_for_profile("chat_shell")

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
        profile: str | RuntimeProfile = "chat_shell",
    ) -> str:
        if not tools:
            return await self._router.complete(messages, purpose="chat")

        profile_obj = self._resolve_profile(profile)
        state = ExecutionSessionState(constraints=constraints)
        task_id = f"task_{uuid.uuid4().hex[:12]}"
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

        async def _step_runner(round_index: int) -> TaskLoopStep:
            nonlocal last_payload, final_reply
            turn = await self._router.complete_with_tools(
                self._with_shell_state_context(messages, state),
                tools=tools,
                purpose="chat",
            )

            if not turn.tool_calls:
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

            if len(turn.tool_calls) > 1:
                logger.warning(
                    "[runtime] 模型返回了 %s 个 tool calls，当前将按顺序只处理第一个。",
                    len(turn.tool_calls),
                )

            tool_call = turn.tool_calls[0]
            if turn.continuation_message is not None:
                messages.append(turn.continuation_message)

            if status_callback and round_index >= 1:
                try:
                    await status_callback(chat_id, f"第 {round_index} 步完成，继续处理中...")
                except Exception:
                    pass

            await self._publish_task_event(
                event_bus,
                "task.executing",
                task_id=task_id,
                chat_id=chat_id,
                phase="executing",
                text=f"正在执行工具：{tool_call.name}",
                tool_name=tool_call.name,
                round=round_index + 1,
            )

            tool_result_text, payload = await self._execute_tool_call(
                tool_call=tool_call,
                state=state,
                deps=deps,
                task_id=task_id,
                chat_id=chat_id,
                event_bus=event_bus,
                profile=profile_obj,
            )
            last_payload = payload

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
                return TaskLoopStep(completed=True, payload=last_payload)

            messages.append(
                self._router.build_tool_result_message(
                    purpose="chat",
                    tool_results=[(tool_call, tool_result_text)],
                )
            )
            logger.info("[runtime] 完成第 %s 轮 tool call: %s", round_index + 1, tool_call.name)

            if state.completed:
                await self._publish_task_event(
                    event_bus,
                    "task.completed",
                    task_id=task_id,
                    chat_id=chat_id,
                    phase="completed",
                    text="任务执行并验证完成。",
                    tool_name=tool_call.name,
                )
                final_reply = state.success_message()
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
                    tool_name=tool_call.name,
                )

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
                reason=state.failure_reason or loop_result.reason or "tool 循环超过上限",
            )
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

        shell_executor = deps.execute_shell if deps is not None else default_execute_shell
        shell_default_cwd = deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD
        context = ToolExecutionContext(
            execute_shell=shell_executor,
            shell_default_cwd=shell_default_cwd,
            workspace_root=workspace_root or str(ROOT_DIR),
            services=services or {},
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
        profile: str | RuntimeProfile = "chat_shell",
    ) -> tuple[str, dict[str, Any]]:
        execution = await self.execute_tool(
            request=ToolExecutionRequest(name=tool_call.name, arguments=tool_call.arguments),
            profile=profile,
            state=state,
            deps=deps,
            task_id=task_id,
            chat_id=chat_id,
            event_bus=event_bus,
        )
        payload = execution.payload
        return json.dumps(payload, ensure_ascii=False), payload

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
