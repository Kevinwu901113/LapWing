"""FileAgent — 读写项目文件，带安全白名单/黑名单。"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.prompt_loader import load_prompt
from config.settings import ROOT_DIR, DATA_DIR

logger = logging.getLogger("lapwing.agents.file")

_BACKUP_DIR = DATA_DIR / "backups" / "prompts"

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

    def __init__(self, memory) -> None:
        self._memory = memory

    async def execute(self, task: AgentTask, router) -> AgentResult:
        # 用 LLM 解析操作意图
        prompt = load_prompt("agent_file").format(user_message=task.user_message)
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=512,
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
        if not abs_path.exists():
            return AgentResult(content=f"文件不存在：{rel_path}")
        if not abs_path.is_file():
            return AgentResult(content=f"{rel_path} 不是一个文件。")
        try:
            text = abs_path.read_text(encoding="utf-8")
            logger.info(f"[file] 读取: {rel_path} ({len(text)} 字符)")
            return AgentResult(
                content=f"**{rel_path}** 的内容：\n\n```\n{text}\n```",
                needs_persona_formatting=False,
            )
        except Exception as exc:
            logger.warning(f"[file] 读取失败: {rel_path} — {exc}")
            return AgentResult(content=f"读取文件时出错：{exc}")

    async def _do_write(self, abs_path: Path, rel_path: str, content: str) -> AgentResult:
        try:
            # prompts/ 下的文件先备份
            if abs_path.exists() and abs_path.parts[len(ROOT_DIR.parts)] == "prompts":
                self._backup(abs_path)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
            logger.info(f"[file] 写入: {rel_path} ({len(content)} 字符)")
            return AgentResult(content=f"已写入 `{rel_path}`。")
        except Exception as exc:
            logger.warning(f"[file] 写入失败: {rel_path} — {exc}")
            return AgentResult(content=f"写入文件时出错：{exc}")

    async def _do_append(self, abs_path: Path, rel_path: str, content: str) -> AgentResult:
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            with abs_path.open("a", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[file] 追加: {rel_path} ({len(content)} 字符)")
            return AgentResult(content=f"已追加内容到 `{rel_path}`。")
        except Exception as exc:
            logger.warning(f"[file] 追加失败: {rel_path} — {exc}")
            return AgentResult(content=f"追加文件时出错：{exc}")

    async def _do_list(self, abs_path: Path, rel_path: str) -> AgentResult:
        if not abs_path.exists():
            return AgentResult(content=f"目录不存在：{rel_path or '.'}")
        if abs_path.is_file():
            return AgentResult(content=f"{rel_path} 是一个文件，不是目录。")
        try:
            entries = sorted(abs_path.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines = []
            for e in entries:
                icon = "📁" if e.is_dir() else "📄"
                lines.append(f"{icon} {e.name}")
            result = "\n".join(lines) if lines else "（目录为空）"
            display = rel_path or "根目录"
            logger.info(f"[file] 列出: {display} ({len(entries)} 项)")
            return AgentResult(
                content=f"**{display}** 目录内容：\n\n{result}",
                needs_persona_formatting=False,
            )
        except Exception as exc:
            logger.warning(f"[file] 列出失败: {rel_path} — {exc}")
            return AgentResult(content=f"列出目录时出错：{exc}")

    def _backup(self, abs_path: Path) -> None:
        """将文件备份到 data/backups/prompts/ 下。"""
        try:
            _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{abs_path.stem}_{ts}{abs_path.suffix}"
            shutil.copy2(abs_path, _BACKUP_DIR / backup_name)
            logger.info(f"[file] 备份: {abs_path.name} → {backup_name}")
        except Exception as exc:
            logger.warning(f"[file] 备份失败: {exc}")
