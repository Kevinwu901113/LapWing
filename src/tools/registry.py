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
    read_file_tool,
    run_python_code_tool,
    verify_code_result_tool,
    verify_workspace_tool,
    weather_tool,
    write_file_tool,
)
from src.tools.skill_tools import skill_list_tool, skill_view_tool
from src.tools.trace_mark import trace_mark_tool
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

    # ── 后台进程管理 ──
    from config.settings import SHELL_ENABLED
    if SHELL_ENABLED:
        from src.tools.process_tools import PROCESS_EXECUTORS
        registry.register(ToolSpec(
            name="process_spawn",
            description=(
                "在后台启动一个命令，不阻塞对话。"
                "适用于长时间运行的任务（测试、编译、服务器）。"
                "可以设置 watch_patterns 在输出匹配时通知。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                    "watch_patterns": {
                        "type": "array", "items": {"type": "string"},
                        "description": "监控模式列表。输出包含这些文本时会通知。如 ['FAILED', 'ERROR']",
                    },
                    "notify_on_complete": {
                        "type": "boolean",
                        "description": "进程完成时是否通知（默认 true）",
                    },
                },
                "required": ["command"],
            },
            executor=PROCESS_EXECUTORS["process_spawn"],
            capability="shell",
            risk_level="medium",
        ))
        registry.register(ToolSpec(
            name="process_status",
            description="查询后台进程状态。不传 process_id 则列出所有运行中的进程。",
            json_schema={
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "进程 ID（从 process_spawn 返回值获取）",
                    },
                },
            },
            executor=PROCESS_EXECUTORS["process_status"],
            capability="shell",
            risk_level="low",
        ))
        registry.register(ToolSpec(
            name="process_kill",
            description="终止一个后台进程。",
            json_schema={
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "要终止的进程 ID",
                    },
                },
                "required": ["process_id"],
            },
            executor=PROCESS_EXECUTORS["process_kill"],
            capability="shell",
            risk_level="medium",
        ))
        registry.register(ToolSpec(
            name="process_logs",
            description="查看后台进程的输出日志。",
            json_schema={
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "进程 ID",
                    },
                    "tail": {
                        "type": "integer",
                        "description": "显示最后多少行（默认 50）",
                    },
                },
                "required": ["process_id"],
            },
            executor=PROCESS_EXECUTORS["process_logs"],
            capability="shell",
            risk_level="low",
        ))

    # web_search 和 web_fetch 已由 personal_tools.py 注册（Phase 4）

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

    # ── Skill 三级渐进加载：Level 1 工具（Pattern 1）──────────────────
    registry.register(
        ToolSpec(
            name="skill_list",
            description=(
                "列出所有可用技能的完整名称和描述。"
                "当 system prompt 中的精简索引不够用、需要看完整描述时调用。"
            ),
            json_schema={
                "type": "object",
                "properties": {},
            },
            executor=skill_list_tool,
            capability="skill",
            risk_level="low",
        )
    )

    registry.register(
        ToolSpec(
            name="skill_view",
            description=(
                "按名称加载技能的完整内容（SKILL.md 正文 + 资源清单）。"
                "传入 path 参数可进一步加载技能目录下的具体资源文件（references/、scripts/ 等）。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "技能名称（来自 system prompt 或 skill_list 的结果）",
                    },
                    "path": {
                        "type": "string",
                        "description": "可选，技能目录下资源文件的相对路径，如 references/guide.md",
                    },
                },
                "required": ["name"],
            },
            executor=skill_view_tool,
            capability="skill",
            risk_level="low",
        )
    )

    # ── 轨迹标记：供执行后反思使用（Pattern 4）──────────────────────
    registry.register(
        ToolSpec(
            name="trace_mark",
            description=(
                "标记本次任务值得在自省时回顾，用于经验积累。"
                "完成 3+ 次工具调用的任务、走过弯路、或 Kevin 纠正了做法时使用。"
                "不会立即创建经验笔记——晚上自省时会优先处理这些标记。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "简短说明为什么值得回顾，一句话即可",
                    },
                    "category": {
                        "type": "string",
                        "description": "经验分类：coding / research / daily / content / system（默认 general）",
                    },
                },
                "required": ["reason"],
            },
            executor=trace_mark_tool,
            capability="general",
            risk_level="low",
        )
    )

    # ── 经验技能管理 ──
    from config.settings import EXPERIENCE_SKILLS_ENABLED
    if EXPERIENCE_SKILLS_ENABLED:
        from src.tools.experience_skill_tools import EXPERIENCE_SKILL_EXECUTORS
        registry.register(ToolSpec(
            name="experience_skill_list",
            description="列出我积累的所有经验技能——名字和简介。",
            json_schema={"type": "object", "properties": {}},
            executor=EXPERIENCE_SKILL_EXECUTORS["experience_skill_list"],
            capability="skill",
            risk_level="low",
        ))
        registry.register(ToolSpec(
            name="experience_skill_view",
            description="查看某个经验技能的完整内容，或其引用文件。",
            json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名称或 ID"},
                    "reference": {
                        "type": "string",
                        "description": "可选，引用文件的相对路径（如 references/tips.md）",
                    },
                },
                "required": ["name"],
            },
            executor=EXPERIENCE_SKILL_EXECUTORS["experience_skill_view"],
            capability="skill",
            risk_level="low",
        ))
        registry.register(ToolSpec(
            name="experience_skill_manage",
            description=(
                "管理经验技能——创建新技能、修补过时技能、或删除不需要的技能。"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "patch", "delete"],
                        "description": "操作类型",
                    },
                    "name": {"type": "string", "description": "技能名称"},
                    "content": {
                        "type": "string",
                        "description": "create 时必需：完整 SKILL.md 内容（含 frontmatter）",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "patch 时必需：要替换的旧文本",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "patch 时必需：替换后的新文本",
                    },
                },
                "required": ["action", "name"],
            },
            executor=EXPERIENCE_SKILL_EXECUTORS["experience_skill_manage"],
            capability="skill",
            risk_level="medium",
        ))

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

    # memory_note 已被 Phase 3 的 write_note 替代（register_memory_tools_v2）

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

    # memory_crud (memory_list/read/edit/delete/search) 已被 Phase 3 工具替代

    # ── 对话历史全文搜索（FTS5）──
    from src.tools.session_search import session_search_executor
    registry.register(ToolSpec(
        name="session_search",
        description="搜索历史对话记录。当需要回忆之前讨论过的具体内容时使用。比 memory_search 更适合找对话细节。",
        json_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（可以是多个词）",
                },
                "days_back": {
                    "type": "integer",
                    "description": "限定搜索最近 N 天内的对话（可选）",
                },
            },
            "required": ["query"],
        },
        executor=session_search_executor,
        capability="memory",
        risk_level="low",
    ))

    # delegate_task 已由 personal_tools.py 的 delegate 替代（Phase 4）

    # schedule_task / list_scheduled_tasks / cancel_scheduled_task 已由 DurableScheduler 工具替代（Phase 4）
    # 新工具名: set_reminder / view_reminders / cancel_reminder，在 container.py 中注册

    # ── 图片搜索（保留，personal_tools 未替代） ──
    from src.tools.image_search import IMAGE_SEARCH_EXECUTORS
    registry.register(ToolSpec(
        name="image_search",
        description=(
            "搜索图片，返回可直接用于 send_image 的图片 URL 列表。"
            "当你想发图片给用户但没有现成的图片 URL 时，先用这个工具搜索，再用 send_image 发送。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，建议用英文以获得更多结果",
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回的最大结果数，默认 5",
                },
            },
            "required": ["query"],
        },
        executor=IMAGE_SEARCH_EXECUTORS["image_search"],
        capability="web",
        risk_level="low",
    ))

    # send_image, send_proactive_message 已由 personal_tools.py 注册（Phase 4）

    # ── 自我状态 ──
    from src.tools.self_status import SELF_STATUS_EXECUTORS
    registry.register(ToolSpec(
        name="self_status",
        description="查看自己的运行状态：启动时间、运行时长、CPU/内存/磁盘、通道连接、记忆统计。",
        json_schema={
            "type": "object",
            "properties": {},
        },
        executor=SELF_STATUS_EXECUTORS["self_status"],
        capability="general",
        risk_level="low",
    ))

    # ── Incident 自报告 ──
    from src.tools.incident_tool import execute_report_incident
    registry.register(ToolSpec(
        name="report_incident",
        description=(
            "报告一个你发现的问题或失败。当你注意到自己犯了错、某个工具反复失败、"
            "或者某个能力有缺陷时使用。不是用来记录普通记忆的——普通记忆用 write_note。"
            "这个工具专门用于你觉得需要调查和修复的问题。"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "问题描述：发生了什么，你觉得原因是什么",
                },
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "严重程度。low=小问题，medium=影响回复质量，high=功能不可用",
                },
                "related_tool": {
                    "type": "string",
                    "description": "相关的工具名（如果有），如 web_search、execute_shell",
                },
            },
            "required": ["description"],
        },
        executor=execute_report_incident,
        capability="general",
        risk_level="low",
    ))

    return registry
