"""工具注册中心：统一管理工具 schema、执行和可见性。"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.tools.handlers import (
    _blocked_payload,
    activate_skill_tool,
    apply_workspace_patch_tool,
    execute_shell_tool,
    file_append_tool,
    file_list_directory_tool,
    file_read_segment_tool,
    file_write_tool,
    memory_note_tool,
    read_file_tool,
    run_python_code_tool,
    verify_code_result_tool,
    verify_workspace_tool,
    weather_tool,
    web_fetch_tool,
    web_search_tool,
    write_file_tool,
)
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)

logger = logging.getLogger("lapwing.tools.registry")


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
                "properties": {"path": {"type": "string", "description": "文件的绝对路径"}},
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
                    "path": {"type": "string", "description": "文件的绝对路径"},
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
            name="web_search",
            description="联网搜索网页信息。输入 query，返回标题、链接和摘要结果列表。",
            json_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或问题"},
                    "max_results": {
                        "type": "integer",
                        "description": "可选，返回结果数量（1-10，默认使用 SEARCH_MAX_RESULTS）",
                    },
                },
                "required": ["query"],
            },
            executor=web_search_tool,
            capability="web",
            risk_level="medium",
        )
    )

    registry.register(
        ToolSpec(
            name="web_fetch",
            description="抓取指定 URL 的标题与正文文本，用于进一步阅读与总结。",
            json_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "需要抓取的网页 URL（http/https）"},
                    "max_chars": {"type": "integer", "description": "可选，正文最大字符数（默认 4000）"},
                },
                "required": ["url"],
            },
            executor=web_fetch_tool,
            capability="web",
            risk_level="medium",
        )
    )

    registry.register(
        ToolSpec(
            name="activate_skill",
            description=(
                "按名称激活一个已发现的 Skill，返回去 frontmatter 的正文与资源清单。"
                "当任务需要某个技能的详细步骤时先调用此工具。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名称（必须来自 system prompt 的可用技能目录）"},
                    "user_input": {"type": "string", "description": "用户对该技能的附加输入（可选）"},
                },
                "required": ["name"],
            },
            executor=activate_skill_tool,
            capability="skill",
            risk_level="low",
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

    registry.register(
        ToolSpec(
            name="memory_note",
            description=(
                "记下重要的事情。当你觉得对话中有值得记住的信息时使用。"
                "target='kevin' 记录关于他的事（偏好、经历、重要信息）。"
                "target='self' 记录你自己的想法和感受。"
                "不需要每句话都记，只记真正重要的。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["kevin", "self"],
                        "description": "写入目标：kevin=关于他的事，self=你自己的想法",
                    },
                    "content": {
                        "type": "string",
                        "description": "要记下的内容，用自然的语言写",
                    },
                },
                "required": ["target", "content"],
            },
            executor=memory_note_tool,
            capability="memory",
            risk_level="low",
        )
    )

    registry.register(
        ToolSpec(
            name="get_weather",
            description="查询指定城市或地点的当前天气（温度、天气状况、风速）。",
            json_schema={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "要查询天气的城市或地点名称，如「东京」「Los Angeles」「台北」",
                    },
                },
                "required": ["location"],
            },
            executor=weather_tool,
            capability="web",
            risk_level="low",
        )
    )

    # ── Memory CRUD（Wave 1）──
    from config.settings import MEMORY_CRUD_ENABLED, SELF_SCHEDULE_ENABLED
    if MEMORY_CRUD_ENABLED:
        from src.tools.memory_crud import MEMORY_CRUD_EXECUTORS
        registry.register(ToolSpec(
            name="memory_list",
            description="列出记忆目录中的所有文件。用于了解自己记住了什么、记忆是怎么组织的。",
            json_schema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "要列出的子目录，默认整个记忆目录。例如: 'kevin_fact'",
                        "default": "",
                    },
                },
            },
            executor=MEMORY_CRUD_EXECUTORS["memory_list"],
            capability="memory",
            risk_level="low",
        ))
        registry.register(ToolSpec(
            name="memory_read",
            description="读取一个记忆文件的内容（带行号）。用于回顾之前记录的信息、检查记忆是否准确。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对于 data/memory/）。例如: 'KEVIN.md' 或 'kevin_fact/preferences.md'",
                    },
                },
                "required": ["path"],
            },
            executor=MEMORY_CRUD_EXECUTORS["memory_read"],
            capability="memory",
            risk_level="low",
        ))
        registry.register(ToolSpec(
            name="memory_edit",
            description="编辑记忆文件中的内容（精确查找替换）。用于更新过时的信息、纠正错误。old_text 必须精确匹配。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对于 data/memory/）"},
                    "old_text": {"type": "string", "description": "要替换的原文（必须精确匹配）"},
                    "new_text": {"type": "string", "description": "替换后的新文本"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            executor=MEMORY_CRUD_EXECUTORS["memory_edit"],
            capability="memory",
            risk_level="medium",
        ))
        registry.register(ToolSpec(
            name="memory_delete",
            description="删除记忆文件或文件中的特定内容。用于清理过时的、错误的记忆。不提供 text_to_remove 则删除整个文件。",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对于 data/memory/）"},
                    "text_to_remove": {"type": "string", "description": "要从文件中删除的特定文本。不提供则删除整个文件。"},
                },
                "required": ["path"],
            },
            executor=MEMORY_CRUD_EXECUTORS["memory_delete"],
            capability="memory",
            risk_level="medium",
        ))
        registry.register(ToolSpec(
            name="memory_search",
            description="在所有记忆文件中搜索包含关键词的内容。用于查找之前记录的特定信息。",
            json_schema={
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "搜索关键词"}},
                "required": ["keyword"],
            },
            executor=MEMORY_CRUD_EXECUTORS["memory_search"],
            capability="memory",
            risk_level="low",
        ))

    # ── 自主调度（Wave 1）──
    if SELF_SCHEDULE_ENABLED:
        from src.tools.schedule_task import SCHEDULE_EXECUTORS
        registry.register(ToolSpec(
            name="schedule_task",
            description=(
                "安排一个在未来执行的定时任务。"
                "用于：定期自省、定时提醒 Kevin、自主浏览新闻、安排兴趣探索等。"
                "schedule 用自然语言描述（如'每天晚上11点'、'每隔2小时'、'明天早上8点'）。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "schedule": {
                        "type": "string",
                        "description": "时间安排。支持: '每天HH:MM'、'每隔N小时'、'每隔N分钟'、'YYYY-MM-DD HH:MM'（单次）、'明天/后天 HH:MM'",
                    },
                    "task_description": {
                        "type": "string",
                        "description": "要执行的任务描述，这会成为你收到的 prompt。",
                    },
                    "repeat": {"type": "boolean", "description": "是否重复执行，默认 true。"},
                },
                "required": ["schedule", "task_description"],
            },
            executor=SCHEDULE_EXECUTORS["schedule_task"],
            capability="schedule",
            risk_level="medium",
        ))
        registry.register(ToolSpec(
            name="list_scheduled_tasks",
            description="查看所有已安排的定时任务。",
            json_schema={"type": "object", "properties": {}},
            executor=SCHEDULE_EXECUTORS["list_scheduled_tasks"],
            capability="schedule",
            risk_level="low",
        ))
        registry.register(ToolSpec(
            name="cancel_scheduled_task",
            description="取消一个定时任务。",
            json_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string", "description": "要取消的任务 ID"}},
                "required": ["task_id"],
            },
            executor=SCHEDULE_EXECUTORS["cancel_scheduled_task"],
            capability="schedule",
            risk_level="medium",
        ))

    return registry
