"""后台进程管理工具。"""

from __future__ import annotations

import logging

from src.core.process_registry import process_registry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.process_tools")


async def _execute_process_spawn(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    command = str(request.arguments.get("command", "")).strip()
    if not command:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 command 参数"},
            reason="missing_command",
        )

    watch_patterns = request.arguments.get("watch_patterns") or []
    notify_on_complete = bool(request.arguments.get("notify_on_complete", True))

    try:
        session = process_registry.spawn(
            command=command,
            chat_id=context.chat_id or "",
            watch_patterns=watch_patterns,
            notify_on_complete=notify_on_complete,
        )
        return ToolExecutionResult(
            success=True,
            payload={
                "output": f"后台进程已启动: {session.id} (PID {session.pid})",
                "process_id": session.id,
                "pid": session.pid,
            },
        )
    except RuntimeError as e:
        return ToolExecutionResult(
            success=False,
            payload={"error": str(e)},
            reason="spawn_failed",
        )


async def _execute_process_status(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    process_id = str(request.arguments.get("process_id", "")).strip()
    if not process_id:
        # 没指定 id，列出所有活跃进程
        active = process_registry.list_active()
        if not active:
            return ToolExecutionResult(
                success=True,
                payload={"output": "没有运行中的后台进程。"},
            )
        lines = [f"运行中的后台进程 ({len(active)} 个):"]
        for p in active:
            lines.append(f"  [{p['id']}] {p['command']} — PID {p['pid']} — {p['runtime']}")
        return ToolExecutionResult(
            success=True,
            payload={"output": "\n".join(lines)},
        )

    result = process_registry.poll(process_id)
    if "error" in result:
        return ToolExecutionResult(
            success=False,
            payload={"error": result["error"]},
            reason="not_found",
        )

    lines = [
        f"进程 {result['id']}:",
        f"  命令: {result['command'][:80]}",
        f"  状态: {result['status']}",
        f"  PID: {result['pid']}",
        f"  运行时间: {result['runtime_seconds']}s",
    ]
    if result["exit_code"] is not None:
        lines.append(f"  退出码: {result['exit_code']}")
    if result["output_tail"]:
        lines.append(f"\n最近输出:\n{result['output_tail']}")

    return ToolExecutionResult(
        success=True,
        payload={"output": "\n".join(lines)},
    )


async def _execute_process_kill(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    process_id = str(request.arguments.get("process_id", "")).strip()
    if not process_id:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 process_id 参数"},
            reason="missing_id",
        )

    result = process_registry.kill(process_id)
    if "error" in result:
        return ToolExecutionResult(
            success=False,
            payload={"error": result["error"]},
            reason="kill_failed",
        )
    return ToolExecutionResult(
        success=True,
        payload={"output": f"已终止进程 {process_id}（退出码 {result['exit_code']}）"},
    )


async def _execute_process_logs(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    process_id = str(request.arguments.get("process_id", "")).strip()
    if not process_id:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 process_id 参数"},
            reason="missing_id",
        )

    tail = int(request.arguments.get("tail", 50))
    logs = process_registry.logs(process_id, tail=tail)
    return ToolExecutionResult(
        success=True,
        payload={"output": logs},
    )


PROCESS_EXECUTORS = {
    "process_spawn": _execute_process_spawn,
    "process_status": _execute_process_status,
    "process_kill": _execute_process_kill,
    "process_logs": _execute_process_logs,
}
