"""FileAgent — 读写项目文件，带安全白名单/黑名单。"""

import logging
from pathlib import Path

from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.task_runtime import TaskRuntime
from src.core.prompt_loader import load_prompt
from src.tools import file_editor
from src.tools.types import ToolExecutionRequest
from config.settings import ROOT_DIR

logger = logging.getLogger("lapwing.agents.file")

# 白名单：只允许这些目录下的文件
_ALLOWED_DIRS = {"prompts", "data", "logs", "config"}

# 绝对禁止的路径（相对于 ROOT_DIR）
_BLOCKED_PATHS = {"main.py", "config/.env"}

# 禁止操作的文件模式（glob 形式）
_BLOCKED_GLOBS = ["src/**/*.py", "tests/**/*.py", "*.py"]


def _is_blocked(rel_path: str) -> bool:
    """检查相对路径是否命中黑名单。"""
    if rel_path in _BLOCKED_PATHS:
        return True
    p = Path(rel_path)
    # 检查 glob 模式
    for pattern in _BLOCKED_GLOBS:
        if p.match(pattern):
            return True
    return False


def _is_allowed(rel_path: str) -> bool:
    """检查相对路径是否在白名单目录内。"""
    parts = Path(rel_path).parts
    if not parts:
        return False
    return parts[0] in _ALLOWED_DIRS


class FileAgent(BaseAgent):
    """提供受安全约束的文件读写能力。"""

    name = "file"
    description = "读取、写入、管理 Lapwing 项目中的文本文件（prompts、data、logs、config）"
    capabilities = [
        "读取文件内容",
        "写入或覆盖文件",
        "追加内容到文件",
        "列出目录中的文件",
    ]

    def __init__(self, memory, runtime: TaskRuntime | None = None) -> None:
        self._memory = memory
        self._runtime = runtime

    async def execute(self, task: AgentTask, router) -> AgentResult:
        # 用 LLM 解析操作意图
        prompt = load_prompt("agent_file").format(user_message=task.user_message)
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=512,
                session_key=f"chat:{task.chat_id}",
                origin="agent.file.parse",
            )
        except Exception as exc:
            logger.warning(f"[file] LLM 解析失败: {exc}")
            return AgentResult(content="解析文件操作时出了点问题，请稍后再试。")

        import json, re
        # 提取 JSON
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return AgentResult(content="无法理解你想对哪个文件做什么。")
        try:
            cmd = json.loads(match.group())
        except json.JSONDecodeError:
            return AgentResult(content="无法理解你想对哪个文件做什么。")

        operation = cmd.get("operation", "")
        if operation == "error":
            return AgentResult(content=cmd.get("reason", "无法解析操作。"))

        rel_path: str = cmd.get("path", "").strip().lstrip("/")
        content: str = cmd.get("content", "")

        if not rel_path and operation != "list":
            return AgentResult(content="请告诉我要操作哪个文件。")

        # 安全检查
        ok, err = self._validate_path(rel_path)
        if not ok:
            logger.warning(f"[file] 拒绝操作被禁止的路径: {rel_path!r} — {err}")
            return AgentResult(content=f"这个路径不在我的操作范围内。")

        abs_path = ROOT_DIR / rel_path

        if operation == "read":
            return await self._do_read(abs_path, rel_path)
        elif operation == "write":
            return await self._do_write(abs_path, rel_path, content)
        elif operation == "append":
            return await self._do_append(abs_path, rel_path, content)
        elif operation == "list":
            list_path = ROOT_DIR / rel_path if rel_path else ROOT_DIR
            return await self._do_list(list_path, rel_path)
        else:
            return AgentResult(content=f"不支持的操作：{operation}")

    def _validate_path(self, rel_path: str) -> tuple[bool, str]:
        """白名单 + 黑名单双重检查。"""
        if not rel_path:
            return False, "路径为空"
        if ".." in rel_path:
            return False, "禁止路径穿越"
        if _is_blocked(rel_path):
            return False, "路径在黑名单中"
        if not _is_allowed(rel_path):
            return False, f"路径不在允许目录 {_ALLOWED_DIRS} 内"
        return True, ""

    async def _do_read(self, abs_path: Path, rel_path: str) -> AgentResult:
        if self._runtime is None:
            result = file_editor.read_file_segment(
                str(abs_path),
                start_line=1,
                end_line=10 ** 9,
                root_dir=ROOT_DIR,
            )
        else:
            execution = await self._runtime.execute_tool(
                request=ToolExecutionRequest(
                    name="file_read_segment",
                    arguments={
                        "path": str(abs_path),
                        "start_line": 1,
                        "end_line": 10 ** 9,
                    },
                ),
                profile="file_ops",
                workspace_root=str(ROOT_DIR),
            )
            payload = execution.payload
            result = file_editor.FileEditResult(
                success=bool(payload.get("success", False)),
                operation=str(payload.get("operation", "file_read_segment")),
                path=str(payload.get("path", str(abs_path))),
                changed=bool(payload.get("changed", False)),
                reason=str(payload.get("reason", "")),
                content=str(payload.get("content", "")),
                diff=str(payload.get("diff", "")),
                backup_path=payload.get("backup_path"),
                metadata=dict(payload.get("metadata") or {}),
            )
        if not result.success:
            logger.warning(f"[file] 读取失败: {rel_path} — {result.reason}")
            return AgentResult(content=f"读取文件时出错：{result.reason}")

        text = result.content
        logger.info(f"[file] 读取: {rel_path} ({len(text)} 字符)")
        return AgentResult(
            content=f"**{rel_path}** 的内容：\n\n```\n{text}\n```",
            needs_persona_formatting=False,
        )

    async def _do_write(self, abs_path: Path, rel_path: str, content: str) -> AgentResult:
        if self._runtime is None:
            result = file_editor.write_file(
                str(abs_path),
                content=content,
                root_dir=ROOT_DIR,
            )
        else:
            execution = await self._runtime.execute_tool(
                request=ToolExecutionRequest(
                    name="file_write",
                    arguments={
                        "path": str(abs_path),
                        "content": content,
                    },
                ),
                profile="file_ops",
                workspace_root=str(ROOT_DIR),
            )
            payload = execution.payload
            result = file_editor.FileEditResult(
                success=bool(payload.get("success", False)),
                operation=str(payload.get("operation", "file_write")),
                path=str(payload.get("path", str(abs_path))),
                changed=bool(payload.get("changed", False)),
                reason=str(payload.get("reason", "")),
                content=str(payload.get("content", "")),
                diff=str(payload.get("diff", "")),
                backup_path=payload.get("backup_path"),
                metadata=dict(payload.get("metadata") or {}),
            )
        if not result.success:
            logger.warning(f"[file] 写入失败: {rel_path} — {result.reason}")
            return AgentResult(content=f"写入文件时出错：{result.reason}")

        logger.info(f"[file] 写入: {rel_path} ({len(content)} 字符)")
        return AgentResult(content=f"已写入 `{rel_path}`。")

    async def _do_append(self, abs_path: Path, rel_path: str, content: str) -> AgentResult:
        if self._runtime is None:
            result = file_editor.append_to_file(
                str(abs_path),
                content=content,
                root_dir=ROOT_DIR,
            )
        else:
            execution = await self._runtime.execute_tool(
                request=ToolExecutionRequest(
                    name="file_append",
                    arguments={
                        "path": str(abs_path),
                        "content": content,
                    },
                ),
                profile="file_ops",
                workspace_root=str(ROOT_DIR),
            )
            payload = execution.payload
            result = file_editor.FileEditResult(
                success=bool(payload.get("success", False)),
                operation=str(payload.get("operation", "file_append")),
                path=str(payload.get("path", str(abs_path))),
                changed=bool(payload.get("changed", False)),
                reason=str(payload.get("reason", "")),
                content=str(payload.get("content", "")),
                diff=str(payload.get("diff", "")),
                backup_path=payload.get("backup_path"),
                metadata=dict(payload.get("metadata") or {}),
            )
        if not result.success:
            logger.warning(f"[file] 追加失败: {rel_path} — {result.reason}")
            return AgentResult(content=f"追加文件时出错：{result.reason}")

        logger.info(f"[file] 追加: {rel_path} ({len(content)} 字符)")
        return AgentResult(content=f"已追加内容到 `{rel_path}`。")

    async def _do_list(self, abs_path: Path, rel_path: str) -> AgentResult:
        if self._runtime is None:
            result = file_editor.list_directory(str(abs_path), root_dir=ROOT_DIR)
        else:
            execution = await self._runtime.execute_tool(
                request=ToolExecutionRequest(
                    name="file_list_directory",
                    arguments={"path": str(abs_path)},
                ),
                profile="file_ops",
                workspace_root=str(ROOT_DIR),
            )
            payload = execution.payload
            result = file_editor.FileEditResult(
                success=bool(payload.get("success", False)),
                operation=str(payload.get("operation", "file_list_directory")),
                path=str(payload.get("path", str(abs_path))),
                changed=bool(payload.get("changed", False)),
                reason=str(payload.get("reason", "")),
                content=str(payload.get("content", "")),
                diff=str(payload.get("diff", "")),
                backup_path=payload.get("backup_path"),
                metadata=dict(payload.get("metadata") or {}),
            )
        if not result.success:
            logger.warning(f"[file] 列出失败: {rel_path} — {result.reason}")
            return AgentResult(content=f"列出目录时出错：{result.reason}")

        entries = result.metadata.get("entries", [])
        display = rel_path or "根目录"
        logger.info(f"[file] 列出: {display} ({len(entries)} 项)")
        return AgentResult(
            content=f"**{display}** 目录内容：\n\n{result.content}",
            needs_persona_formatting=False,
        )
