"""工具注册中心：统一管理工具 schema、执行和可见性。"""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path
from typing import Any

from config.settings import ROOT_DIR
from src.core import verifier
from src.tools import code_runner, file_editor
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.registry")


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
    payload: dict[str, Any] = {
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
    return payload


async def _execute_shell_tool(
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


async def _read_file_tool(
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


async def _write_file_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    if not path:
        payload = {"error": "缺少 path 参数", "stdout": "", "return_code": -1}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 path 参数")

    await context.execute_shell(f"mkdir -p $(dirname {shlex.quote(path)})")
    write_cmd = f"cat > {shlex.quote(path)} << 'LAPWING_EOF'\n{content}\nLAPWING_EOF"
    result = await context.execute_shell(write_cmd)
    payload = {"path": path, "action": "written", **result.to_dict()}
    return ToolExecutionResult(
        success=(result.return_code == 0 and not result.blocked and not result.timed_out),
        payload=payload,
        reason=result.reason or "",
        shell_result=result,
    )


async def _file_read_segment_tool(
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


async def _file_write_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    result = file_editor.write_file(
        path,
        content=content,
        root_dir=_workspace_root(context),
    )
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def _file_append_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    result = file_editor.append_to_file(
        path,
        content=content,
        root_dir=_workspace_root(context),
    )
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def _file_list_directory_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    if not path:
        path = "."
    result = file_editor.list_directory(path, root_dir=_workspace_root(context))
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def _apply_workspace_patch_tool(
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


async def _run_python_code_tool(
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
    success = (result.exit_code == 0 and not result.timed_out)
    return ToolExecutionResult(success=success, payload=payload, reason=result.stderr.strip())


async def _verify_code_result_tool(
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
    return ToolExecutionResult(
        success=verified.passed,
        payload=payload,
        reason=verified.reason,
    )


async def _verify_workspace_tool(
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
    return ToolExecutionResult(
        success=verified.passed,
        payload=payload,
        reason=verified.reason,
    )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_tools(
        self,
        *,
        capability: str | None = None,
        capabilities: set[str] | None = None,
        include_internal: bool = False,
        tool_names: set[str] | None = None,
    ) -> list[ToolSpec]:
        specs = list(self._tools.values())
        if capability is not None:
            specs = [tool for tool in specs if tool.supports_capability(capability)]
        if capabilities:
            specs = [
                tool
                for tool in specs
                if any(tool.supports_capability(item) for item in capabilities)
            ]
        if tool_names is not None:
            specs = [tool for tool in specs if tool.name in tool_names]
        if not include_internal:
            specs = [tool for tool in specs if tool.is_model_facing]
        return specs

    def function_tools(
        self,
        *,
        capability: str | None = None,
        capabilities: set[str] | None = None,
        include_internal: bool = False,
        tool_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        specs = self.list_tools(
            capability=capability,
            capabilities=capabilities,
            include_internal=include_internal,
            tool_names=tool_names,
        )
        return [tool.to_function_tool() for tool in specs]

    async def execute(
        self,
        request: ToolExecutionRequest,
        *,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        tool = self.get(request.name)
        if tool is None:
            reason = f"未知工具：{request.name}"
            return ToolExecutionResult(
                success=False,
                reason=reason,
                payload=_blocked_payload(reason=reason, cwd=context.shell_default_cwd, command=""),
            )

        try:
            return await tool.executor(request, context)
        except Exception as exc:
            logger.warning("[tools] 工具 `%s` 执行异常: %s", request.name, exc)
            reason = f"工具执行失败：{request.name}"
            return ToolExecutionResult(
                success=False,
                reason=reason,
                payload=_blocked_payload(reason=reason, cwd=context.shell_default_cwd, command=""),
            )

    def as_descriptions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risk_level": tool.risk_level,
                "capability": tool.capability,
                "capabilities": [tool.capability, *tool.capabilities],
                "visibility": tool.visibility,
                "schema": json.dumps(tool.json_schema, ensure_ascii=False),
            }
            for tool in self._tools.values()
        ]


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="execute_shell",
            description=(
                "在服务器上执行 shell 命令。"
                "用于创建文件/目录、查看文件内容、安装软件、运行脚本等任何命令行操作。"
                "遇到权限问题时自动尝试替代路径，不要询问用户。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    }
                },
                "required": ["command"],
            },
            executor=_execute_shell_tool,
            capability="shell",
            risk_level="high",
            metadata={"policy_hook": "shell_command"},
        )
    )

    registry.register(
        ToolSpec(
            name="read_file",
            description="读取服务器上的文件内容。用于查看配置文件、日志、代码等。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的绝对路径",
                    }
                },
                "required": ["path"],
            },
            executor=_read_file_tool,
            capability="shell",
            risk_level="medium",
        )
    )

    registry.register(
        ToolSpec(
            name="write_file",
            description="将内容写入文件。如果文件不存在会自动创建，包括必要的父目录。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的绝对路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容",
                    },
                },
                "required": ["path", "content"],
            },
            executor=_write_file_tool,
            capability="shell",
            risk_level="high",
        )
    )

    registry.register(
        ToolSpec(
            name="file_read_segment",
            description="读取工作区内文件的指定行范围。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path", "start_line", "end_line"],
            },
            executor=_file_read_segment_tool,
            capability="file",
            capabilities=("workspace",),
            risk_level="low",
        )
    )

    registry.register(
        ToolSpec(
            name="file_write",
            description="在工作区内覆盖写入文件。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            executor=_file_write_tool,
            capability="file",
            capabilities=("workspace",),
            risk_level="high",
        )
    )

    registry.register(
        ToolSpec(
            name="file_append",
            description="在工作区内向文件追加内容。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            executor=_file_append_tool,
            capability="file",
            capabilities=("workspace",),
            risk_level="medium",
        )
    )

    registry.register(
        ToolSpec(
            name="file_list_directory",
            description="列出工作区目录内容。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
            executor=_file_list_directory_tool,
            capability="file",
            capabilities=("workspace",),
            risk_level="low",
        )
    )

    registry.register(
        ToolSpec(
            name="apply_workspace_patch",
            description="在工作区按事务应用多文件编辑操作，失败自动回滚。",
            json_schema={
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "required": ["operations"],
            },
            executor=_apply_workspace_patch_tool,
            capability="code",
            capabilities=("file", "workspace"),
            risk_level="high",
        )
    )

    registry.register(
        ToolSpec(
            name="run_python_code",
            description="在隔离目录中执行 Python 代码并返回 stdout/stderr。",
            json_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["code"],
            },
            executor=_run_python_code_tool,
            capability="code",
            capabilities=("execution",),
            risk_level="medium",
        )
    )

    registry.register(
        ToolSpec(
            name="verify_code_result",
            description="内部工具：验证代码执行结果。",
            json_schema={
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "timed_out": {"type": "boolean"},
                    "require_stdout": {"type": "boolean"},
                },
                "required": ["stdout", "stderr", "exit_code", "timed_out"],
            },
            executor=_verify_code_result_tool,
            capability="verify",
            capabilities=("code",),
            visibility="internal",
            risk_level="low",
        )
    )

    registry.register(
        ToolSpec(
            name="verify_workspace",
            description="内部工具：验证工作区改动。",
            json_schema={
                "type": "object",
                "properties": {
                    "changed_files": {"type": "array", "items": {"type": "string"}},
                    "pytest_targets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["changed_files"],
            },
            executor=_verify_workspace_tool,
            capability="verify",
            capabilities=("workspace", "code"),
            visibility="internal",
            risk_level="low",
        )
    )

    return registry
