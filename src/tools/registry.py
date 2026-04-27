"""工具注册中心：统一管理工具 schema、执行和可见性。"""

from __future__ import annotations

import json
import logging
from typing import Any

from config.settings import ROOT_DIR

from src.tools.handlers import (
    _blocked_payload,
    apply_workspace_patch_tool,
    execute_shell_tool,
    file_append_tool,
    file_list_directory_tool,
    file_read_segment_tool,
    file_write_tool,
    read_file_tool,
    run_python_code_tool,
    verify_code_result_tool,
    verify_workspace_tool,
    write_file_tool,
)
from src.tools.commitments import (
    ABANDON_PROMISE_DESCRIPTION,
    ABANDON_PROMISE_SCHEMA,
    COMMIT_PROMISE_DESCRIPTION,
    COMMIT_PROMISE_SCHEMA,
    FULFILL_PROMISE_DESCRIPTION,
    FULFILL_PROMISE_SCHEMA,
    abandon_promise_executor,
    commit_promise_executor,
    fulfill_promise_executor,
)
from src.tools.plan_tools import (
    PLAN_TASK_DESCRIPTION,
    PLAN_TASK_SCHEMA,
    UPDATE_PLAN_DESCRIPTION,
    UPDATE_PLAN_SCHEMA,
    plan_task_executor,
    update_plan_executor,
)
from src.tools.correction_tools import ADD_CORRECTION_SPEC
from src.tools.focus_tools import CLOSE_FOCUS_SPEC, RECALL_FOCUS_SPEC
from src.tools.sports_tool import SPORTS_TOOL_SPEC
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.registry")


class ToolNotRegisteredError(RuntimeError):
    """Raised when a caller requests tools by name that aren't registered.

    v2.0 Step 1 §4.2 — every ``tool_names`` entry passed to
    :meth:`ToolRegistry.list_tools` (and therefore :meth:`function_tools`)
    must resolve to an actually-registered ToolSpec. Silent filtering of
    unknown names is forbidden: it hides configuration drift where a
    whitelist lists a tool that was never implemented.
    """


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
            known = set(self._tools.keys())
            missing = set(tool_names) - known
            if missing:
                raise ToolNotRegisteredError(
                    f"tool_names whitelist references unregistered tools: {sorted(missing)}"
                )
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

    def get_tools_for_profile(self, profile: Any, *, include_internal: bool | None = None) -> list[ToolSpec]:
        """Return registered tools visible for a RuntimeProfile.

        Dynamic chat profiles list optional tools that are only registered when
        their subsystem is enabled, so missing names are skipped here instead
        of treated as configuration drift.
        """
        if include_internal is None:
            include_internal = bool(getattr(profile, "include_internal", False))
        tool_names = getattr(profile, "tool_names", frozenset())
        if tool_names:
            specs = [
                self._tools[name]
                for name in tool_names
                if name in self._tools
                and (include_internal or self._tools[name].is_model_facing)
            ]
        else:
            specs = self.list_tools(
                capabilities=set(getattr(profile, "capabilities", frozenset())),
                include_internal=bool(getattr(profile, "include_internal", False)),
            )
        exclude = set(getattr(profile, "exclude_tool_names", frozenset()))
        if exclude:
            specs = [tool for tool in specs if tool.name not in exclude]
        return specs

    def function_tools_for_profile(self, profile: Any) -> list[dict[str, Any]]:
        return [
            tool.to_function_tool()
            for tool in self.get_tools_for_profile(profile, include_internal=False)
        ]

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

    # 承诺三件套——commit/fulfill/abandon_promise。
    # 与 tell_user 一起构成"说话 + 承诺"语义层。
    registry.register(
        ToolSpec(
            name="commit_promise",
            description=COMMIT_PROMISE_DESCRIPTION,
            json_schema=COMMIT_PROMISE_SCHEMA,
            executor=commit_promise_executor,
            capability="commitment",
            risk_level="low",
        )
    )
    registry.register(
        ToolSpec(
            name="fulfill_promise",
            description=FULFILL_PROMISE_DESCRIPTION,
            json_schema=FULFILL_PROMISE_SCHEMA,
            executor=fulfill_promise_executor,
            capability="commitment",
            risk_level="low",
        )
    )
    registry.register(
        ToolSpec(
            name="abandon_promise",
            description=ABANDON_PROMISE_DESCRIPTION,
            json_schema=ABANDON_PROMISE_SCHEMA,
            executor=abandon_promise_executor,
            capability="commitment",
            risk_level="low",
        )
    )

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
                    "command": {"type": "string", "description": "要执行的 shell 命令"}
                },
                "required": ["command"],
            },
            executor=execute_shell_tool,
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
                "properties": {"path": {"type": "string", "description": f"文件的绝对路径。项目根目录: {ROOT_DIR}，数据目录: {ROOT_DIR / 'data'}/"}},
                "required": ["path"],
            },
            executor=read_file_tool,
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
                    "path": {"type": "string", "description": f"文件的绝对路径。项目根目录: {ROOT_DIR}，数据目录: {ROOT_DIR / 'data'}/"},
                    "content": {"type": "string", "description": "要写入的内容"},
                },
                "required": ["path", "content"],
            },
            executor=write_file_tool,
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
            executor=file_read_segment_tool,
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
            executor=file_write_tool,
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
            executor=file_append_tool,
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
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            executor=file_list_directory_tool,
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
                    "operations": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["operations"],
            },
            executor=apply_workspace_patch_tool,
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
            executor=run_python_code_tool,
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
            executor=verify_code_result_tool,
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
            executor=verify_workspace_tool,
            capability="verify",
            capabilities=("workspace", "code"),
            visibility="internal",
            risk_level="low",
        )
    )

    # 任务规划工具
    registry.register(
        ToolSpec(
            name="plan_task",
            description=PLAN_TASK_DESCRIPTION,
            json_schema=PLAN_TASK_SCHEMA,
            executor=plan_task_executor,
            capability="general",
            risk_level="low",
        )
    )
    registry.register(
        ToolSpec(
            name="update_plan",
            description=UPDATE_PLAN_DESCRIPTION,
            json_schema=UPDATE_PLAN_SCHEMA,
            executor=update_plan_executor,
            capability="general",
            risk_level="low",
        )
    )

    # 纠正记录工具
    registry.register(ADD_CORRECTION_SPEC)
    registry.register(CLOSE_FOCUS_SPEC)
    registry.register(RECALL_FOCUS_SPEC)
    registry.register(SPORTS_TOOL_SPEC)

    # 时区工具
    from src.tools.timezone_tools import (
        CONVERT_TIMEZONE_DESCRIPTION,
        CONVERT_TIMEZONE_SCHEMA,
        GET_CURRENT_DATETIME_DESCRIPTION,
        GET_CURRENT_DATETIME_SCHEMA,
        convert_timezone_executor,
        get_current_datetime_executor,
    )
    registry.register(
        ToolSpec(
            name="convert_timezone",
            description=CONVERT_TIMEZONE_DESCRIPTION,
            json_schema=CONVERT_TIMEZONE_SCHEMA,
            executor=convert_timezone_executor,
            capability="general",
            risk_level="low",
        )
    )
    registry.register(
        ToolSpec(
            name="get_current_datetime",
            description=GET_CURRENT_DATETIME_DESCRIPTION,
            json_schema=GET_CURRENT_DATETIME_SCHEMA,
            executor=get_current_datetime_executor,
            capability="general",
            risk_level="low",
        )
    )

    # memory_tools_v2, soul_tools, personal_tools, agent_tools, browser_tools,
    # durable_scheduler tools — 全部在 container.py 中注册（Phase 3-6）

    return registry
