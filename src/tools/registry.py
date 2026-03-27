"""工具注册中心：统一管理工具 schema 与执行。"""

from __future__ import annotations

import json
import logging
import shlex
from dataclasses import dataclass
from typing import Any

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec

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


@dataclass
class ToolRegistry:
    _tools: dict[str, ToolSpec]

    def __init__(self) -> None:
        self._tools = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def function_tools(self, *, capability: str | None = None) -> list[dict[str, Any]]:
        specs = list(self._tools.values())
        if capability is not None:
            specs = [tool for tool in specs if tool.capability == capability]
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

    return registry
