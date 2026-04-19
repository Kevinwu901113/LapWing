"""Coder Agent 工作区工具 — 限制在 data/agent_workspace/ 下。"""

from __future__ import annotations

import logging
from pathlib import Path

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.workspace_tools")

AGENT_WORKSPACE = Path("data/agent_workspace")


def _resolve_safe(path_str: str) -> Path | None:
    """解析路径并确保在 workspace 内。返回 None 表示越界。"""
    resolved = (AGENT_WORKSPACE / path_str).resolve()
    try:
        resolved.relative_to(AGENT_WORKSPACE.resolve())
        return resolved
    except ValueError:
        return None


async def ws_file_write_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """写入文件到 agent_workspace。"""
    path_str = req.arguments.get("path", "")
    content = req.arguments.get("content", "")

    resolved = _resolve_safe(path_str)
    if resolved is None:
        return ToolExecutionResult(
            success=False, payload={},
            reason="只能写入 data/agent_workspace/ 下的文件。",
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")

    rel = resolved.relative_to(Path.cwd()) if resolved.is_relative_to(Path.cwd()) else resolved
    return ToolExecutionResult(
        success=True, payload={"path": str(rel)}, reason="已写入",
    )


async def ws_file_read_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """读取 agent_workspace 中的文件。"""
    path_str = req.arguments.get("path", "")

    resolved = _resolve_safe(path_str)
    if resolved is None:
        return ToolExecutionResult(
            success=False, payload={},
            reason="只能读取 data/agent_workspace/ 下的文件。",
        )

    if not resolved.exists():
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"文件不存在: {path_str}",
        )

    content = resolved.read_text(encoding="utf-8")
    return ToolExecutionResult(
        success=True, payload={"content": content, "path": path_str},
    )


async def ws_file_list_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """列出 agent_workspace 中的文件。"""
    path_str = req.arguments.get("path", ".")

    resolved = _resolve_safe(path_str)
    if resolved is None:
        return ToolExecutionResult(
            success=False, payload={},
            reason="只能列出 data/agent_workspace/ 下的内容。",
        )

    if not resolved.exists():
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"目录不存在: {path_str}",
        )

    files = sorted(p.name for p in resolved.iterdir())
    return ToolExecutionResult(
        success=True, payload={"files": files, "path": path_str},
    )
