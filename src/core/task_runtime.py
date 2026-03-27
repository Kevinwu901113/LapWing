"""任务执行运行时：封装 tool loop、工具执行和任务生命周期事件。"""

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.core.llm_router import ToolCallRequest
from src.core.shell_policy import (
    ExecutionConstraints,
    ExecutionSessionState,
)
from src.policy.shell_runtime_policy import ShellRuntimePolicy
from src.tools.registry import ToolRegistry, build_default_tool_registry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.shell_executor import ShellResult

logger = logging.getLogger("lapwing.task_runtime")

_MAX_TOOL_ROUNDS = 8


@dataclass(frozen=True)
class RuntimeDeps:
    """tool loop 运行所需的策略与执行依赖。"""

    execute_shell: Callable[[str], Awaitable[ShellResult]]
    policy: ShellRuntimePolicy
    shell_default_cwd: str
    shell_allow_sudo: bool


class TaskRuntime:
    """负责执行工具轮次与任务级事件发布。"""

    def __init__(
        self,
        router,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._router = router
        self._max_tool_rounds = max_tool_rounds
        self._tool_registry = tool_registry or build_default_tool_registry()

    def chat_tools(self, shell_enabled: bool) -> list[dict[str, Any]]:
        if not shell_enabled:
            return []
        return self._tool_registry.function_tools(capability="shell")

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
    ) -> str:
        if not tools:
            return await self._router.complete(messages, purpose="chat")

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

        last_payload: dict[str, Any] | None = None

        for round_index in range(self._max_tool_rounds):
            turn = await self._router.complete_with_tools(
                self._with_shell_state_context(messages, state),
                tools=tools,
                purpose="chat",
            )

            if not turn.tool_calls:
                return await self._finalize_without_tool_calls(
                    chat_id=chat_id,
                    task_id=task_id,
                    state=state,
                    model_text=turn.text,
                    last_payload=last_payload,
                    event_bus=event_bus,
                    on_consent_required=on_consent_required,
                )

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

            tool_result_text, last_payload = await self._execute_tool_call(
                tool_call=tool_call,
                state=state,
                deps=deps,
                task_id=task_id,
                chat_id=chat_id,
                event_bus=event_bus,
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
                    return on_consent_required(state)
                return state.consent_message()

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
                return state.success_message()

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
                reason=state.failure_reason or "tool 循环超过上限",
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
        return self.tool_fallback_reply(last_payload)

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
        # 被主动拦截（blocked/timed_out）时直接使用 reason
        if result.reason and (result.blocked or result.timed_out):
            return result.reason

        # 优先使用 stderr，比 shell_executor 生成的通用 reason 更有用
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

    async def _execute_tool_call(
        self,
        *,
        tool_call: ToolCallRequest,
        state: ExecutionSessionState,
        deps: RuntimeDeps,
        task_id: str,
        chat_id: str,
        event_bus,
    ) -> tuple[str, dict[str, Any]]:
        tool = self._tool_registry.get(tool_call.name)
        if tool is None:
            reason = f"未知工具：{tool_call.name}"
            state.record_failure(reason, "blocked")
            payload = self._blocked_payload(
                reason=reason,
                cwd=deps.shell_default_cwd,
                command="",
            )
            return json.dumps(payload, ensure_ascii=False), payload

        command = str(tool_call.arguments.get("command", "")).strip()
        intent = None
        if tool_call.name == "execute_shell":
            if not command:
                reason = "工具参数缺少 command。"
                state.record_failure(reason, "blocked")
                payload = self._blocked_payload(
                    reason=reason,
                    cwd=deps.shell_default_cwd,
                    command="",
                )
                return json.dumps(payload, ensure_ascii=False), payload

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
                payload = self._blocked_payload(
                    reason=reason,
                    cwd=deps.shell_default_cwd,
                    command=command,
                )
                return json.dumps(payload, ensure_ascii=False), payload
            if pre_decision.action == "block":
                reason = pre_decision.reason or "命令被策略拦截。"
                state.record_failure(reason, pre_decision.failure_type)
                payload = self._blocked_payload(
                    reason=reason,
                    cwd=deps.shell_default_cwd,
                    command=command,
                )
                return json.dumps(payload, ensure_ascii=False), payload

        execution = await self._tool_registry.execute(
            ToolExecutionRequest(name=tool_call.name, arguments=tool_call.arguments),
            context=ToolExecutionContext(
                execute_shell=deps.execute_shell,
                shell_default_cwd=deps.shell_default_cwd,
            ),
        )

        payload = execution.payload
        if tool_call.name != "execute_shell":
            return json.dumps(payload, ensure_ascii=False), payload

        shell_result = execution.shell_result
        if shell_result is None or intent is None:
            reason = execution.reason or "工具执行失败。"
            state.record_failure(reason, "blocked")
            if "blocked" not in payload:
                payload = self._blocked_payload(
                    reason=reason,
                    cwd=deps.shell_default_cwd,
                    command=command,
                )
            return json.dumps(payload, ensure_ascii=False), payload

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
        elif post_decision.should_verify:
            await self._publish_task_event(
                event_bus,
                "task.verifying",
                task_id=task_id,
                chat_id=chat_id,
                phase="verifying",
                text="正在验证任务结果。",
                command=command,
                tool_name=tool_call.name,
            )
            verification = deps.policy.verify(state.constraints)
            if verification.completed:
                state.mark_completed(verification)
            else:
                state.record_failure(verification.reason, "verification_failed")

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
