"""Agent Team 工具：delegate_to_researcher + delegate_to_coder + 5 个新 agent 工具。

两层架构：主脑 LLM 通过 tool_call 名称（delegate_to_researcher/coder/agent）
选择目标 agent；AgentRegistry 按名取实例并执行。Blueprint §7 引入了
delegate_to_agent + list/create/destroy/save_agent 五个工具，覆盖动态
agent 的全生命周期；旧的 delegate_to_researcher/coder 暂时保留（Task 12
将其转为 shim）。

Module-level side-tables:
  - ``_ephemeral_run_counts``: 跟踪 ephemeral agent 已运行次数，用于
    达到 max_runs 后自动 destroy。
  - ``_completed_delegations``: 跟踪每个 agent 的成功完成次数，供
    save_agent 构造 run_history 给 policy 校验。
两个表都按 agent name 索引，destroy 时清理。
"""

from __future__ import annotations

import inspect
import logging
import traceback
import uuid

from src.agents.budget import BudgetExhausted
from src.agents.policy import AgentPolicyViolation, CreateAgentInput
from src.agents.types import AgentMessage, AgentResult
from src.logging.state_mutation_log import MutationType
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec

logger = logging.getLogger("lapwing.tools.agent_tools")


# Side-tables for ephemeral lifecycle and save_agent run-history.
# Keyed by agent name. Cleared on agent destroy.
_ephemeral_run_counts: dict[str, int] = {}
_completed_delegations: dict[str, int] = {}


def _generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


def _extract_context_digest(ctx: ToolExecutionContext) -> str:
    """从当前上下文中提取摘要供子 agent 参考。"""
    parts: list[str] = []

    trajectory_store = ctx.services.get("trajectory_store")
    if trajectory_store is not None:
        try:
            recent = trajectory_store.recent(ctx.chat_id, limit=6)
            if recent:
                lines = []
                for entry in recent[-6:]:
                    role = getattr(entry, "role", "")
                    text = getattr(entry, "text", "") or getattr(entry, "content", "")
                    if text:
                        lines.append(f"{role}: {str(text)[:200]}")
                if lines:
                    parts.append("最近对话：\n" + "\n".join(lines))
        except Exception:
            pass

    return "\n\n".join(parts)


def _serialize_agent_result(result: AgentResult, task_id: str) -> ToolExecutionResult:
    """统一将 AgentResult 序列化为 ToolExecutionResult。"""
    trace_tail = result.execution_trace[-5:] if result.execution_trace else []

    if result.status == "done":
        payload: dict = {
            "task_id": task_id,
            "result": result.result,
            "artifacts": result.artifacts,
            "evidence": result.evidence,
        }
        if trace_tail:
            payload["execution_trace"] = trace_tail
        return ToolExecutionResult(
            success=True,
            payload=payload,
            reason="任务完成",
        )
    else:
        payload = {
            "task_id": task_id,
            "status": result.status,
        }
        if result.error_detail:
            payload["error_detail"] = result.error_detail
        if trace_tail:
            payload["execution_trace"] = trace_tail
        return ToolExecutionResult(
            success=False,
            payload=payload,
            reason=result.reason or "任务失败",
        )


async def _resolve_agent(agent_registry, agent_name: str):
    """Prefer the v2 ``get_or_create_instance`` async method; fall back to
    legacy ``get`` for the pre-v2 registry.

    We use ``inspect.iscoroutinefunction`` to detect actual async methods —
    plain ``MagicMock`` auto-attributes are NOT coroutine functions, so
    legacy tests that wire up ``registry.get`` keep working unchanged.
    """
    method = getattr(agent_registry, "get_or_create_instance", None)
    if method is not None and inspect.iscoroutinefunction(method):
        return await method(agent_name)
    if hasattr(agent_registry, "get"):
        return agent_registry.get(agent_name)
    return None


async def _run_agent(
    agent_name: str,
    request: str,
    context_digest: str,
    ctx: ToolExecutionContext,
    parent_task_id: str | None = None,
    expected_output: str = "",
) -> ToolExecutionResult:
    """直接调度指定 agent 执行任务。Budget-aware via ctx.services['budget_ledger']。"""
    agent_registry = ctx.services.get("agent_registry")
    if not agent_registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    agent = await _resolve_agent(agent_registry, agent_name)
    if not agent:
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"Agent '{agent_name}' 不可用",
        )

    # Budget: enter delegation depth (raises BudgetExhausted on overflow).
    ledger = ctx.services.get("budget_ledger")
    if ledger is not None:
        try:
            ledger.enter_delegation()
        except BudgetExhausted as exc:
            return ToolExecutionResult(
                success=False,
                payload={"dimension": exc.dimension},
                reason=f"delegation_depth_exceeded: {exc}",
            )

    task_id = _generate_task_id()

    digest = context_digest.strip()
    if not digest:
        digest = _extract_context_digest(ctx)

    full_content = request
    if expected_output:
        full_content = f"{request}\n\n期望输出格式: {expected_output}"

    message = AgentMessage(
        from_agent="lapwing",
        to_agent=agent_name,
        task_id=task_id,
        content=full_content,
        context_digest=digest,
        message_type="request",
        parent_task_id=parent_task_id,
    )

    result: AgentResult | None = None
    try:
        try:
            result = await agent.execute(message)
        except Exception as exc:
            tb = traceback.format_exc()
            tb_tail = "\n".join(tb.strip().splitlines()[-5:])
            return ToolExecutionResult(
                success=False,
                payload={"task_id": task_id, "error_detail": tb_tail},
                reason=f"Agent 执行异常: {exc}",
            )
    finally:
        if ledger is not None:
            try:
                ledger.exit_delegation()
            except Exception:
                pass

    # Track completion for save_agent run-history (only on successful done).
    if result is not None and result.status == "done":
        _completed_delegations[agent_name] = _completed_delegations.get(agent_name, 0) + 1

    # Ephemeral max_runs cleanup.
    eph_specs = getattr(agent_registry, "_ephemeral_agents", None)
    if eph_specs and agent_name in eph_specs:
        spec = eph_specs[agent_name]
        max_runs = getattr(getattr(spec, "lifecycle", None), "max_runs", None)
        if max_runs is not None:
            count = _ephemeral_run_counts.get(agent_name, 0) + 1
            _ephemeral_run_counts[agent_name] = count
            if count >= max_runs:
                try:
                    await agent_registry.destroy_agent(agent_name)
                except Exception:
                    logger.exception("[agent_tools] auto-destroy failed: %s", agent_name)
                _ephemeral_run_counts.pop(agent_name, None)
                ml = ctx.services.get("mutation_log")
                if ml is not None:
                    try:
                        await ml.record(
                            MutationType.AGENT_DESTROYED,
                            payload={
                                "agent_id": getattr(spec, "id", agent_name),
                                "agent_name": agent_name,
                                "reason": "ephemeral_completed",
                                "total_runs": count,
                            },
                        )
                    except Exception:
                        pass

    return _serialize_agent_result(result, task_id)


async def delegate_to_researcher_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """Compatibility shim — forwards to delegate_to_agent_executor.

    Per blueprint §7.4: legacy delegate tools remain registered for any
    persisted plans / older clients, but the implementation is the new
    delegate_to_agent path. They're removed from RuntimeProfile.tool_names
    in Task 13 so the Brain LLM no longer sees them.
    """
    return await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={
                "agent_name": "researcher",
                "task": req.arguments.get("request", ""),
                "context": req.arguments.get("context_digest", ""),
            },
        ),
        ctx,
    )


async def delegate_to_coder_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """Compatibility shim — forwards to delegate_to_agent_executor."""
    return await delegate_to_agent_executor(
        ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={
                "agent_name": "coder",
                "task": req.arguments.get("request", ""),
                "context": req.arguments.get("context_digest", ""),
            },
        ),
        ctx,
    )


# ── Blueprint §7.2: 5 个新 agent 工具 ──


async def delegate_to_agent_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    args = req.arguments
    agent_name = (args.get("agent_name") or "").strip()
    task = (args.get("task") or "").strip()
    if not agent_name or not task:
        return ToolExecutionResult(
            success=False, payload={},
            reason="agent_name 和 task 不能为空",
        )
    return await _run_agent(
        agent_name,
        task,
        context_digest=args.get("context", ""),
        ctx=ctx,
        expected_output=args.get("expected_output", ""),
    )


async def list_agents_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    registry = ctx.services.get("agent_registry")
    if not registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")
    items = await registry.list_agents(full=req.arguments.get("full", False))
    return ToolExecutionResult(success=True, payload={"agents": items}, reason="")


async def create_agent_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    registry = ctx.services.get("agent_registry")
    if not registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    args = req.arguments
    try:
        request = CreateAgentInput(
            name_hint=args.get("name_hint", ""),
            purpose=args.get("purpose", ""),
            instructions=args.get("instructions", ""),
            profile=args.get("profile", ""),
            model_slot=args.get("model_slot", "agent_researcher"),
            lifecycle=args.get("lifecycle", "ephemeral"),
            max_runs=args.get("max_runs", 1),
            ttl_seconds=args.get("ttl_seconds", 3600),
        )
    except TypeError as exc:
        return ToolExecutionResult(
            success=False, payload={}, reason=f"invalid arguments: {exc}",
        )

    try:
        spec = await registry.create_agent(request, ctx)
    except AgentPolicyViolation as exc:
        return ToolExecutionResult(
            success=False,
            payload={"violation_reason": exc.reason, "details": exc.details},
            reason=f"policy_violation: {exc.reason}",
        )

    ml = ctx.services.get("mutation_log")
    if ml is not None:
        try:
            await ml.record(
                MutationType.AGENT_CREATED,
                payload={
                    "agent_id": spec.id,
                    "agent_name": spec.name,
                    "kind": "dynamic",
                    "profile": spec.runtime_profile,
                    "model_slot": spec.model_slot,
                    "lifecycle_mode": spec.lifecycle.mode,
                    "created_by": spec.created_by,
                    "created_reason": spec.created_reason,
                    "spec_hash": spec.spec_hash(),
                },
            )
        except Exception:
            pass

    return ToolExecutionResult(
        success=True,
        payload={
            "name": spec.name,
            "id": spec.id,
            "profile": spec.runtime_profile,
            "lifecycle": spec.lifecycle.mode,
            "model_slot": spec.model_slot,
        },
    )


async def destroy_agent_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    registry = ctx.services.get("agent_registry")
    if not registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")
    name = (req.arguments.get("agent_name") or "").strip()
    if not name:
        return ToolExecutionResult(success=False, payload={}, reason="agent_name 不能为空")
    # Capture run count BEFORE destroy clears it.
    total_runs = _ephemeral_run_counts.get(name, 0)
    ok = await registry.destroy_agent(name)
    if not ok:
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"cannot_destroy_builtin: '{name}' is builtin or unknown",
        )
    # Cleanup our side-tables.
    _ephemeral_run_counts.pop(name, None)
    _completed_delegations.pop(name, None)
    ml = ctx.services.get("mutation_log")
    if ml is not None:
        try:
            await ml.record(
                MutationType.AGENT_DESTROYED,
                payload={
                    "agent_id": name,
                    "agent_name": name,
                    "reason": "manual",
                    "total_runs": total_runs,
                },
            )
        except Exception:
            pass
    return ToolExecutionResult(success=True, payload={"name": name})


async def save_agent_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    registry = ctx.services.get("agent_registry")
    if not registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")
    name = (req.arguments.get("agent_name") or "").strip()
    reason = (req.arguments.get("reason") or "").strip()
    if not name or not reason:
        return ToolExecutionResult(
            success=False, payload={},
            reason="agent_name 和 reason 不能为空",
        )

    # Build run_history from completion side-table; policy only checks truthiness.
    run_count = _completed_delegations.get(name, 0)
    run_history = [f"task_{i}" for i in range(run_count)]

    try:
        await registry.save_agent(name, reason, run_history)
    except AgentPolicyViolation as exc:
        return ToolExecutionResult(
            success=False,
            payload={"violation_reason": exc.reason, "details": exc.details},
            reason=f"policy_violation: {exc.reason}",
        )

    # Fetch the post-save spec for the audit hash. Best-effort.
    saved_hash = ""
    if hasattr(registry, "_lookup_spec"):
        try:
            saved_spec = await registry._lookup_spec(name)
            if saved_spec is not None:
                saved_hash = saved_spec.spec_hash()
        except Exception:
            pass

    ml = ctx.services.get("mutation_log")
    if ml is not None:
        try:
            await ml.record(
                MutationType.AGENT_SAVED,
                payload={
                    "agent_id": name,
                    "agent_name": name,
                    "save_reason": reason,
                    "spec_hash": saved_hash,
                    "run_count": run_count,
                },
            )
        except Exception:
            pass

    return ToolExecutionResult(success=True, payload={"name": name, "reason": reason})


def register_agent_tools(registry, agent_registry=None) -> None:
    """注册 7 个 agent 工具：legacy delegate_to_researcher/coder + 新 5 个。"""

    _DELEGATE_SCHEMA = {
        "type": "object",
        "properties": {
            "request": {"type": "string", "description": "你的需求——清晰具体地描述任务"},
            "context_digest": {
                "type": "string",
                "description": "当前对话的背景摘要，帮助 agent 理解上下文",
            },
        },
        "required": ["request"],
    }

    registry.register(ToolSpec(
        name="delegate_to_researcher",
        description=(
            "把调研任务交给 Researcher。"
            "擅长：网络搜索、信息整理、多源综合、写摘要。"
            "不擅长：写代码、执行脚本、文件操作。"
        ),
        json_schema=_DELEGATE_SCHEMA,
        executor=delegate_to_researcher_executor,
        capability="agent",
        risk_level="low",
        max_result_tokens=3000,
    ))

    registry.register(ToolSpec(
        name="delegate_to_coder",
        description=(
            "把代码任务交给 Coder。"
            "擅长：写代码、调试、跑脚本、文件读写。"
            "不擅长：网络搜索、信息调研。"
        ),
        json_schema=_DELEGATE_SCHEMA,
        executor=delegate_to_coder_executor,
        capability="agent",
        risk_level="low",
        max_result_tokens=3000,
    ))

    # ── Blueprint §7.2 schemas ──

    DELEGATE_TO_AGENT_SCHEMA = {
        "type": "object",
        "properties": {
            "agent_name": {"type": "string", "description": "目标 agent 的内部名称"},
            "task": {"type": "string", "description": "交给 agent 的具体任务描述"},
            "context": {"type": "string", "description": "可选的额外上下文信息", "default": ""},
            "expected_output": {"type": "string", "description": "可选的期望输出格式描述", "default": ""},
        },
        "required": ["agent_name", "task"],
    }
    LIST_AGENTS_SCHEMA = {
        "type": "object",
        "properties": {
            "full": {"type": "boolean", "description": "是否返回完整信息", "default": False},
        },
    }
    CREATE_AGENT_SCHEMA = {
        "type": "object",
        "properties": {
            "name_hint": {"type": "string"},
            "purpose": {"type": "string"},
            "instructions": {"type": "string"},
            "profile": {"type": "string", "enum": ["agent_researcher", "agent_coder"]},
            "model_slot": {
                "type": "string",
                "enum": ["agent_researcher", "agent_coder", "lightweight_judgment"],
            },
            "lifecycle": {"type": "string", "enum": ["ephemeral", "session"], "default": "ephemeral"},
            "max_runs": {"type": "integer", "default": 1},
            "ttl_seconds": {"type": "integer", "default": 3600},
        },
        "required": ["name_hint", "purpose", "instructions", "profile"],
    }
    DESTROY_AGENT_SCHEMA = {
        "type": "object",
        "properties": {"agent_name": {"type": "string"}},
        "required": ["agent_name"],
    }
    SAVE_AGENT_SCHEMA = {
        "type": "object",
        "properties": {
            "agent_name": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["agent_name", "reason"],
    }

    registry.register(ToolSpec(
        name="delegate_to_agent",
        description="委派任务给指定 agent。提供 agent_name、task，可选 context 和 expected_output。",
        json_schema=DELEGATE_TO_AGENT_SCHEMA,
        executor=delegate_to_agent_executor,
        capability="agent",
        risk_level="low",
        max_result_tokens=3000,
    ))
    registry.register(ToolSpec(
        name="list_agents",
        description="列出当前可用的 agent（builtin + 已创建的 dynamic）。",
        json_schema=LIST_AGENTS_SCHEMA,
        executor=list_agents_executor,
        capability="agent",
        risk_level="low",
        max_result_tokens=2000,
    ))
    registry.register(ToolSpec(
        name="create_agent",
        description="创建新的动态 agent 用于特定任务。",
        json_schema=CREATE_AGENT_SCHEMA,
        executor=create_agent_executor,
        capability="agent",
        risk_level="medium",
    ))
    registry.register(ToolSpec(
        name="destroy_agent",
        description="销毁动态 agent（不能销毁 builtin agent）。",
        json_schema=DESTROY_AGENT_SCHEMA,
        executor=destroy_agent_executor,
        capability="agent",
        risk_level="medium",
    ))
    registry.register(ToolSpec(
        name="save_agent",
        description="持久化动态 agent 的 spec 以便复用。",
        json_schema=SAVE_AGENT_SCHEMA,
        executor=save_agent_executor,
        capability="agent",
        risk_level="medium",
    ))

    logger.info("[agent_tools] 已注册 7 个 agent 工具 (legacy delegate_* + 新 5 个)")
