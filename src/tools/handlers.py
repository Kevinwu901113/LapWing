"""Tool execution handlers — extracted from registry.py for clarity."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

from config.settings import ROOT_DIR
from src.core import verifier
from src.tools import code_runner, file_editor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.handlers")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _blocked_payload(*, reason: str, cwd: str, command: str = "") -> dict[str, Any]:
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


def _workspace_root(context: ToolExecutionContext) -> Path:
    raw = context.workspace_root.strip() if context.workspace_root else ""
    if not raw:
        return ROOT_DIR.resolve()
    return Path(raw).resolve()


def _file_payload(result: file_editor.FileEditResult) -> dict[str, Any]:
    return {
        "operation": result.operation,
        "path": result.path,
        "success": result.success,
        "changed": result.changed,
        "reason": result.reason,
        "content": result.content,
        "diff": result.diff,
        "backup_path": result.backup_path,
        "metadata": result.metadata,
    }


# ---------------------------------------------------------------------------
# Execution handlers
# ---------------------------------------------------------------------------


async def execute_shell_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    command = str(request.arguments.get("command", "")).strip()
    if not command:
        reason = "工具参数缺少 command。"
        return ToolExecutionResult(
            success=False,
            reason=reason,
            payload=_blocked_payload(reason=reason, cwd=context.shell_default_cwd, command=""),
        )

    result = await context.execute_shell(command)
    payload = {
        "command": command,
        **result.to_dict(),
    }
    return ToolExecutionResult(
        success=(result.return_code == 0 and not result.blocked and not result.timed_out),
        payload=payload,
        reason=result.reason or "",
        shell_result=result,
    )


async def read_file_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    if not path:
        payload = {"error": "缺少 path 参数", "stdout": "", "return_code": -1}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 path 参数")

    result = await context.execute_shell(f"cat {shlex.quote(path)}")
    payload = {"path": path, **result.to_dict()}
    return ToolExecutionResult(
        success=(result.return_code == 0 and not result.blocked and not result.timed_out),
        payload=payload,
        reason=result.reason or "",
        shell_result=result,
    )


async def write_file_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    if not path:
        payload = {"error": "缺少 path 参数", "stdout": "", "return_code": -1}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 path 参数")

    from pathlib import Path as _Path
    target = _Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    payload = {
        "path": path,
        "action": "written",
        "bytes_written": len(content.encode("utf-8")),
        "stdout": "",
        "return_code": 0,
    }
    return ToolExecutionResult(
        success=True,
        payload=payload,
        reason="",
    )


async def activate_skill_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    skill_manager = context.services.get("skill_manager")
    if skill_manager is None:
        payload = {"success": False, "reason": "skill_manager 不可用", "skill_name": "", "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason="skill_manager 不可用")

    name = str(request.arguments.get("name", "")).strip().lower()
    user_input = str(request.arguments.get("user_input", "")).strip()
    if not name:
        payload = {"success": False, "reason": "缺少 name 参数", "skill_name": "", "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 name 参数")

    try:
        activated = skill_manager.activate(name, user_input=user_input)
    except KeyError:
        payload = {"success": False, "reason": f"技能不存在: {name}", "skill_name": name, "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason=f"技能不存在: {name}")
    except Exception as exc:
        payload = {"success": False, "reason": f"激活技能失败: {exc}", "skill_name": name, "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason=f"激活技能失败: {exc}")

    payload = {
        "success": True,
        "reason": "",
        "skill_name": activated.get("skill_name", name),
        "skill_dir": activated.get("skill_dir", ""),
        "content": activated.get("content", ""),
        "resources": activated.get("resources", []),
        "metadata": activated.get("metadata", {}),
        "wrapped_content": activated.get("wrapped_content", ""),
    }
    return ToolExecutionResult(success=True, payload=payload, reason="")


async def file_read_segment_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    start_line = int(request.arguments.get("start_line", 1) or 1)
    end_line = int(request.arguments.get("end_line", 10**9) or 10**9)
    result = file_editor.read_file_segment(
        path,
        start_line=start_line,
        end_line=end_line,
        root_dir=_workspace_root(context),
    )
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def file_write_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    result = file_editor.write_file(path, content=content, root_dir=_workspace_root(context))
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def file_append_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    result = file_editor.append_to_file(path, content=content, root_dir=_workspace_root(context))
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def file_list_directory_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip() or "."
    result = file_editor.list_directory(path, root_dir=_workspace_root(context))
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def apply_workspace_patch_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    operations = request.arguments.get("operations")
    if not isinstance(operations, list) or not operations:
        payload = {"success": False, "reason": "缺少 operations 参数", "changed_files": []}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 operations 参数")

    tx = file_editor.transactional_apply(operations, root_dir=_workspace_root(context))
    payload = {
        "success": tx.success,
        "reason": tx.reason,
        "changed_files": tx.changed_files,
        "rolled_back": tx.rolled_back,
        "results": [
            {
                "operation": item.operation,
                "path": item.path,
                "success": item.success,
                "changed": item.changed,
                "reason": item.reason,
                "metadata": item.metadata,
            }
            for item in tx.results
        ],
    }
    return ToolExecutionResult(success=tx.success, payload=payload, reason=tx.reason)


async def run_python_code_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    code = str(request.arguments.get("code", ""))
    timeout = int(request.arguments.get("timeout", 10) or 10)
    result = await code_runner.run_python(code, timeout=timeout)
    payload = {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
    }
    success = result.exit_code == 0 and not result.timed_out
    return ToolExecutionResult(success=success, payload=payload, reason=result.stderr.strip())


async def verify_code_result_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    exit_code_raw = request.arguments.get("exit_code", -1)
    try:
        exit_code = int(exit_code_raw)
    except (TypeError, ValueError):
        exit_code = -1
    result = code_runner.CodeResult(
        stdout=str(request.arguments.get("stdout", "")),
        stderr=str(request.arguments.get("stderr", "")),
        exit_code=exit_code,
        timed_out=bool(request.arguments.get("timed_out", False)),
    )
    require_stdout = bool(request.arguments.get("require_stdout", False))
    verified = verifier.verify_code_result(result, require_stdout=require_stdout)
    payload = {
        "passed": verified.passed,
        "status": verified.status,
        "reason": verified.reason,
        "checks": verified.checks,
        "artifacts": verified.artifacts,
    }
    return ToolExecutionResult(success=verified.passed, payload=payload, reason=verified.reason)


async def verify_workspace_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    changed_files = request.arguments.get("changed_files")
    if not isinstance(changed_files, list):
        payload = {"passed": False, "status": "failed", "reason": "缺少 changed_files 参数"}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 changed_files 参数")

    pytest_targets_raw = request.arguments.get("pytest_targets")
    pytest_targets = (
        [str(item) for item in pytest_targets_raw if str(item).strip()]
        if isinstance(pytest_targets_raw, list)
        else None
    )
    verified = await verifier.verify_workspace(
        changed_files=[str(item) for item in changed_files],
        root_dir=_workspace_root(context),
        pytest_targets=pytest_targets,
    )
    payload = {
        "passed": verified.passed,
        "status": verified.status,
        "reason": verified.reason,
        "checks": verified.checks,
        "artifacts": verified.artifacts,
    }
    return ToolExecutionResult(success=verified.passed, payload=payload, reason=verified.reason)


