from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from src.core.concurrent_bg_work.policy import ResourceExhaustedError
from src.core.concurrent_bg_work.supervisor import ToolValidationError
from src.core.concurrent_bg_work.types import (
    CancellationScope,
    NotifyPolicy,
    SalienceLevel,
    TaskStatus,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec


def _supervisor(ctx: ToolExecutionContext):
    services = ctx.services or {}
    return services.get("background_task_supervisor") or services.get("concurrent_bg_work_supervisor")


def _failure(reason: str, payload: dict[str, Any] | None = None) -> ToolExecutionResult:
    return ToolExecutionResult(success=False, payload=payload or {}, reason=reason)


def _snapshot_payload(snapshot) -> dict[str, Any]:
    data = asdict(snapshot)
    data["status"] = snapshot.status.value
    data["salience"] = snapshot.salience.value
    for key in ("started_at",):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    return data


async def start_agent_task_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    sup = _supervisor(ctx)
    if sup is None:
        return _failure("background_task_supervisor unavailable")
    args = req.arguments
    try:
        handle = await sup.start_agent_task(
            spec_id=str(args.get("spec_id") or "").strip(),
            objective=str(args.get("objective") or "").strip(),
            chat_id=ctx.chat_id or "unknown",
            owner_user_id=ctx.user_id or "owner",
            parent_event_id=str(args.get("parent_event_id") or "") or (
                f"tool_{ctx.turn_id}" if ctx.turn_id else f"tool_unscoped_{uuid.uuid4().hex}"
            ),
            parent_turn_id=str(args.get("parent_turn_id") or "") or ctx.turn_id or None,
            context=args.get("context") or {},
            expected_output=args.get("expected_output"),
            notify_policy=NotifyPolicy(args.get("notify_policy", "auto")),
            salience=SalienceLevel(args.get("salience", "normal")),
            priority=int(args.get("priority") or 0),
            replaces_task_id=args.get("replaces_task_id"),
            services=ctx.services or {},
        )
    except ResourceExhaustedError as exc:
        return _failure(f"resource_exhausted: {exc}", {"status": "resource_exhausted"})
    except (ToolValidationError, ValueError) as exc:
        return _failure(f"validation_error: {exc}", {"status": "validation_error"})
    return ToolExecutionResult(
        success=True,
        payload={
            "task_id": handle.task_id,
            "status": handle.status.value,
            "estimated_first_progress_at": (
                handle.estimated_first_progress_at.isoformat()
                if handle.estimated_first_progress_at else None
            ),
            "workspace_path": handle.workspace_path,
        },
        reason="task accepted",
    )


async def list_agent_tasks_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    sup = _supervisor(ctx)
    if sup is None:
        return _failure("background_task_supervisor unavailable")
    args = req.arguments
    statuses = None
    if args.get("status_filter"):
        statuses = [TaskStatus(value) for value in args["status_filter"]]
    snapshots = await sup.list_agent_tasks(
        chat_id=args.get("chat_id") or None,
        status_filter=statuses,
        spec_filter=args.get("spec_filter") or None,
        include_recently_completed=bool(args.get("include_recently_completed", False)),
        max_results=int(args.get("max_results") or 20),
    )
    return ToolExecutionResult(
        success=True,
        payload={"tasks": [_snapshot_payload(item) for item in snapshots]},
    )


async def read_agent_task_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    sup = _supervisor(ctx)
    if sup is None:
        return _failure("background_task_supervisor unavailable")
    snapshot = await sup.read_agent_task(str(req.arguments.get("task_id") or ""))
    if snapshot is None:
        return _failure("task_not_found")
    return ToolExecutionResult(success=True, payload=_snapshot_payload(snapshot))


async def cancel_agent_task_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    sup = _supervisor(ctx)
    if sup is None:
        return _failure("background_task_supervisor unavailable")
    args = req.arguments
    try:
        result = await sup.cancel_agent_task(
            scope=CancellationScope(args.get("scope", "task_id")),
            chat_id=ctx.chat_id or "unknown",
            owner_user_id=ctx.user_id or "owner",
            task_id=args.get("task_id"),
            semantic_query=args.get("semantic_query"),
            reason=args.get("reason") or "lapwing_decision",
        )
    except (ToolValidationError, ValueError) as exc:
        return _failure(f"validation_error: {exc}", {"status": "validation_error"})
    payload = asdict(result)
    payload["cancellation_initiated_at"] = result.cancellation_initiated_at.isoformat()
    if result.ambiguous_match is not None:
        payload["ambiguous_match"] = [_snapshot_payload(item) for item in result.ambiguous_match]
    return ToolExecutionResult(success=True, payload=payload)


async def respond_to_agent_input_executor(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult:
    sup = _supervisor(ctx)
    if sup is None:
        return _failure("background_task_supervisor unavailable")
    result = await sup.respond_to_agent_input(
        task_id=str(req.arguments.get("task_id") or ""),
        answer=req.arguments.get("answer"),
    )
    return ToolExecutionResult(
        success=result.accepted,
        payload={"accepted": result.accepted, "new_status": result.new_status.value},
        reason="accepted" if result.accepted else "not_accepted",
    )


START_AGENT_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "spec_id": {"type": "string"},
        "objective": {"type": "string"},
        "context": {"type": "object"},
        "expected_output": {"type": ["string", "null"]},
        "notify_policy": {"type": "string", "enum": ["auto", "silent"], "default": "auto"},
        "salience": {"type": "string", "enum": ["low", "normal", "high", "critical"], "default": "normal"},
        "priority": {"type": "integer", "default": 0},
        "replaces_task_id": {"type": ["string", "null"]},
    },
    "required": ["spec_id", "objective"],
}

LIST_AGENT_TASKS_SCHEMA = {
    "type": "object",
    "properties": {
        "chat_id": {"type": ["string", "null"]},
        "status_filter": {"type": "array", "items": {"type": "string"}},
        "spec_filter": {"type": "array", "items": {"type": "string"}},
        "include_recently_completed": {"type": "boolean", "default": False},
        "max_results": {"type": "integer", "default": 20},
    },
    "required": [],
}

READ_AGENT_TASK_SCHEMA = {
    "type": "object",
    "properties": {"task_id": {"type": "string"}},
    "required": ["task_id"],
}

CANCEL_AGENT_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["task_id", "semantic_match", "chat_background", "all_owner_tasks"],
        },
        "task_id": {"type": ["string", "null"]},
        "semantic_query": {"type": ["string", "null"]},
        "reason": {"type": "string", "default": "lapwing_decision"},
    },
    "required": ["scope"],
}

RESPOND_TO_AGENT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "answer": {},
    },
    "required": ["task_id", "answer"],
}


def register_concurrent_bg_work_tools(registry) -> None:
    for spec in (
        ToolSpec(
            name="start_agent_task",
            description="Start a background agent task and return immediately with a task handle.",
            json_schema=START_AGENT_TASK_SCHEMA,
            executor=start_agent_task_executor,
            capability="background_agent",
            risk_level="low",
        ),
        ToolSpec(
            name="list_agent_tasks",
            description="List background agent tasks visible in the current conversation context.",
            json_schema=LIST_AGENT_TASKS_SCHEMA,
            executor=list_agent_tasks_executor,
            capability="background_agent",
            risk_level="low",
        ),
        ToolSpec(
            name="read_agent_task",
            description="Read a compact cognitive snapshot of one background agent task.",
            json_schema=READ_AGENT_TASK_SCHEMA,
            executor=read_agent_task_executor,
            capability="background_agent",
            risk_level="low",
        ),
        ToolSpec(
            name="cancel_agent_task",
            description="Cancel a background agent task by id, scope, or conservative semantic match.",
            json_schema=CANCEL_AGENT_TASK_SCHEMA,
            executor=cancel_agent_task_executor,
            capability="background_agent",
            risk_level="medium",
        ),
        ToolSpec(
            name="respond_to_agent_input",
            description="Resume a waiting background task by answering its pending checkpoint question.",
            json_schema=RESPOND_TO_AGENT_INPUT_SCHEMA,
            executor=respond_to_agent_input_executor,
            capability="background_agent",
            risk_level="low",
        ),
    ):
        registry.register(spec)
