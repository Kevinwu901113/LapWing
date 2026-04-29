from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from src.logging.state_mutation_log import MutationType, current_iteration_id, current_chat_id
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolExecutionContext
from src.core.runtime_profiles import RuntimeProfile
from src.core.task_types import RuntimeDeps
from src.core.shell_policy import ShellRuntimePolicy as ShellPolicy, ExecutionSessionState
from src.tools.shell_executor import execute as default_execute_shell
from src.core.authority_gate import identify as identify_auth, authorize, AuthLevel
from config.settings import SHELL_DEFAULT_CWD, ROOT_DIR
from src.core.vital_guard import check_compound, Verdict, check_file_target, auto_backup, extract_vital_shell_targets
from pathlib import Path
from src.core.shell_policy import VerificationStatus

if TYPE_CHECKING:
    from src.core.task_runtime import TaskRuntime

logger = logging.getLogger("lapwing.core.tool_dispatcher")

_SHELL_TOOLS = frozenset({"execute_shell"})
_FILE_WRITE_TOOLS = frozenset(
    {"write_file", "apply_workspace_patch", "file_write", "file_append"}
)

class ServiceContextView:
    """Thin wrapper around the legacy services dict to provide typed accessors."""
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    @property
    def agent_registry(self):
        return self.raw.get("agent_registry")

    @property
    def mutation_log(self):
        return self.raw.get("mutation_log")
        
    @property
    def agent_policy(self):
        return self.raw.get("agent_policy")
        
    @property
    def budget_ledger(self):
        return self.raw.get("budget_ledger")
        
    @property
    def proactive_message_gate(self):
        return self.raw.get("proactive_message_gate")

    @property
    def ambient_store(self):
        return self.raw.get("ambient_store")


class ToolDispatcher:
    """
    Central dispatcher for tool execution.
    Responsible for executing profile, auth, policy, browser, budget, and audit checks
    before delegating to the underlying ToolRegistry.
    """
    def __init__(self, runtime: "TaskRuntime"):
        self._runtime = runtime
        

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

    async def _record_tool_denied(
        self,
        *,
        tool_name: str,
        guard: str,
        reason: str,
        auth_level: int,
        services: dict[str, Any] | None,
        chat_id: str | None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        ctx = ServiceContextView(services or {})
        mutation_log = ctx.mutation_log
        if mutation_log is None:
            return
        payload: dict[str, Any] = {
            "tool": tool_name,
            "guard": guard,
            "reason": reason,
            "auth_level": int(auth_level),
        }
        if extras:
            payload.update(extras)
        try:
            await mutation_log.record(
                MutationType.TOOL_DENIED,
                payload,
                iteration_id=current_iteration_id(),
                chat_id=current_chat_id() or chat_id,
            )
        except Exception:
            logger.warning("[mutation_log] TOOL_DENIED record failed", exc_info=True)
    async def dispatch(
        self,
        *,
        request: ToolExecutionRequest,
        profile: str | RuntimeProfile,
        agent_spec: Any = None,
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
        ctx = ServiceContextView(services or {})
        profile_obj = self._runtime._resolve_profile(profile)
        
        # ── Agent Policy Check ────────────────────────────────────────────────
        if agent_spec is not None and getattr(agent_spec, "kind", None) == "dynamic":
            agent_policy = ctx.agent_policy
            if agent_policy is None:
                reason = "missing AgentPolicy in services (fail-closed)"
                if state is not None:
                    state.record_failure(reason, "blocked")
                payload = self._blocked_payload(
                    reason=reason,
                    cwd=(deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD),
                    command=str(request.arguments.get("command", "")).strip(),
                )
                await self._record_tool_denied(
                    tool_name=request.name,
                    guard="agent_policy",
                    reason=reason,
                    auth_level=AuthLevel.OWNER,
                    services=services,
                    chat_id=chat_id,
                    extras={"agent_name": getattr(agent_spec, "name", "unknown")},
                )
                return ToolExecutionResult(success=False, payload=payload, reason=reason)
            
            if not agent_policy.validate_tool_access(agent_spec, request.name):
                reason = "blocked by AgentPolicy"
                if state is not None:
                    state.record_failure(reason, "blocked")
                payload = self._blocked_payload(
                    reason=reason,
                    cwd=(deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD),
                    command=str(request.arguments.get("command", "")).strip(),
                )
                await self._record_tool_denied(
                    tool_name=request.name,
                    guard="agent_policy",
                    reason=reason,
                    auth_level=AuthLevel.OWNER,
                    services=services,
                    chat_id=chat_id,
                    extras={"agent_name": getattr(agent_spec, "name", "unknown")},
                )
                return ToolExecutionResult(success=False, payload=payload, reason=reason)

        tool = self._runtime._tool_registry.get(request.name)
        if tool is None:
            reason = f"未知工具：{request.name}"
            if state is not None:
                state.record_failure(reason, "blocked")
            payload = self._blocked_payload(
                reason=reason,
                cwd=(deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD),
                command="",
            )
            await self._record_tool_denied(
                tool_name=request.name,
                guard="unknown_tool",
                reason=reason,
                auth_level=AuthLevel.OWNER,
                services=services,
                chat_id=chat_id,
                extras={"profile": profile_obj.name},
            )
            return ToolExecutionResult(success=False, payload=payload, reason=reason)

        allowed_names = self._runtime._tool_names_for_profile(
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
            await self._record_tool_denied(
                tool_name=request.name,
                guard="profile_not_allowed",
                reason=reason,
                auth_level=AuthLevel.OWNER,
                services=services,
                chat_id=chat_id,
                extras={"profile": profile_obj.name},
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
            await self._record_tool_denied(
                tool_name=request.name,
                guard="authority_gate",
                reason=deny_reason,
                auth_level=auth_level,
                services=services,
                chat_id=chat_id,
                extras={"adapter": adapter, "user_id": user_id},
            )
            return ToolExecutionResult(success=False, payload=payload, reason=deny_reason)

        shell_executor = deps.execute_shell if deps is not None else default_execute_shell
        shell_default_cwd = deps.shell_default_cwd if deps is not None else SHELL_DEFAULT_CWD

        # ── CheckpointManager：文件修改前自动快照 ──────────────────────────────
        if request.name in (_SHELL_TOOLS | _FILE_WRITE_TOOLS):
            checkpoint_mgr = getattr(self._runtime, "_checkpoint_manager", None)
            if checkpoint_mgr is not None:
                try:
                    checkpoint_mgr.snapshot(workspace_root or str(ROOT_DIR))
                except Exception as cp_exc:
                    logger.debug("Checkpoint 快照跳过: %s", cp_exc)

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
                await self._record_tool_denied(
                    tool_name=request.name,
                    guard="vital_guard",
                    reason=reason,
                    auth_level=auth_level,
                    services=services,
                    chat_id=chat_id,
                    extras={"command_head": vg_command[:120]},
                )
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
                    await self._record_tool_denied(
                        tool_name=request.name,
                        guard="vital_guard",
                        reason=reason,
                        auth_level=auth_level,
                        services=services,
                        chat_id=chat_id,
                        extras={"path": str(target)},
                    )
                    return ToolExecutionResult(success=False, payload=payload, reason=reason)
                if file_guard.verdict == Verdict.VERIFY_FIRST:
                    await auto_backup([target])

        # ── BrowserGuard: 浏览器自动化的 URL/动作/JS 安全检查 ──────────────
        # 当 guard 未挂载时，统一拒绝执行——浏览器工具不可在裸状态下运行。
        elif tool.capability == "browser":
            bg = getattr(self._runtime, "_browser_guard", None)
            if bg is None:
                reason = (
                    f"[BrowserGuard] 未挂载，浏览器工具 '{request.name}' 被拒绝。"
                    "browser 子系统启用时必须装载 BrowserGuard；详见 "
                    "src/core/browser_guard.py。"
                )
                if state is not None:
                    state.record_failure(reason, "blocked")
                payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command="")
                await self._record_tool_denied(
                    tool_name=request.name,
                    guard="browser_guard_missing",
                    reason=reason,
                    auth_level=auth_level,
                    services=services,
                    chat_id=chat_id,
                )
                return ToolExecutionResult(success=False, payload=payload, reason=reason)
            if request.name == "browser_open":
                url = str(request.arguments.get("url", "")).strip()
                if url:
                    bg_result = bg.check_url(url)
                    if bg_result.action == "block":
                        reason = f"[BrowserGuard] {bg_result.reason}"
                        if state is not None:
                            state.record_failure(reason, "blocked")
                        payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command="")
                        await self._record_tool_denied(
                            tool_name=request.name,
                            guard="browser_guard",
                            reason=reason,
                            auth_level=auth_level,
                            services=services,
                            chat_id=chat_id,
                            extras={"url": url[:200]},
                        )
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
            focus_id=focus_id,
            memory=None,
            memory_index=self._runtime._memory_index,
            send_fn=send_fn,
            runtime_profile=profile_obj.name,
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
                await self._record_tool_denied(
                    tool_name=request.name,
                    guard="shell_policy",
                    reason=reason,
                    auth_level=auth_level,
                    services=services,
                    chat_id=chat_id,
                    extras={
                        "decision": "require_consent",
                        "command_head": command[:120],
                    },
                )
                return ToolExecutionResult(success=False, payload=payload, reason=reason)
            if pre_decision.action == "block":
                reason = pre_decision.reason or "命令被策略拦截。"
                state.record_failure(reason, pre_decision.failure_type)
                payload = self._blocked_payload(reason=reason, cwd=shell_default_cwd, command=command)
                await self._record_tool_denied(
                    tool_name=request.name,
                    guard="shell_policy",
                    reason=reason,
                    auth_level=auth_level,
                    services=services,
                    chat_id=chat_id,
                    extras={
                        "decision": "block",
                        "command_head": command[:120],
                    },
                )
                return ToolExecutionResult(success=False, payload=payload, reason=reason)

        # ── AmbientKnowledge 缓存拦截（仅限 research 工具）──────────────
        if request.name == "research":
            _ambient = ctx.ambient_store
            if _ambient is not None:
                _cache_hit = await self._runtime._try_ambient_cache(request, _ambient)
                if _cache_hit is not None:
                    return _cache_hit

        execution = await self._runtime._tool_registry.execute(request, context=context)

        # ── research 成功后写回 ambient cache ──────────────────────────
        if request.name == "research" and execution.success:
            _ambient_wb = ctx.ambient_store
            if _ambient_wb is not None:
                try:
                    await self._runtime._writeback_to_ambient(request, execution, _ambient_wb)
                except Exception:
                    logger.debug("ambient writeback failed", exc_info=True)

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
            reason = post_decision.reason or self._runtime._shell_failure_reason(shell_result)
            state.record_failure(reason, post_decision.failure_type)
            if post_decision.alternative is not None:
                state.require_consent(post_decision.alternative)
            return execution

        if post_decision.should_verify:
            if event_bus is not None and task_id is not None and chat_id is not None:
                await self._runtime._publish_task_event(
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
