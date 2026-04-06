"""Memory CRUD 工具集 — 让 Lapwing 能查看、编辑、删除、搜索自己的记忆文件。

操作范围限于 data/memory/ 和 data/evolution/，data/identity/ 受宪法保护禁止操作。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from config.settings import DATA_DIR, IDENTITY_DIR, MEMORY_DIR, EVOLUTION_DIR
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.memory_crud")

_ALLOWED_DIRS: tuple[Path, ...] = (
    MEMORY_DIR.resolve(),
    EVOLUTION_DIR.resolve(),
)
_FORBIDDEN_DIRS: tuple[Path, ...] = (
    IDENTITY_DIR.resolve(),
)


def _validate_path(path_str: str) -> tuple[bool, Path | str]:
    """路径安全验证。防止目录遍历，禁止操作 identity 目录。

    Returns:
        (True, resolved_path) or (False, error_message)
    """
    try:
        resolved = Path(path_str).resolve()
    except Exception:
        return False, f"无效路径: {path_str}"

    for forbidden in _FORBIDDEN_DIRS:
        try:
            resolved.relative_to(forbidden)
            return False, "不能操作身份文件，这受宪法保护。"
        except ValueError:
            pass

    for allowed in _ALLOWED_DIRS:
        try:
            resolved.relative_to(allowed)
            return True, resolved
        except ValueError:
            pass

    return False, f"只能操作 data/memory/ 或 data/evolution/ 中的文件。"


async def _execute_memory_list(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    directory = str(request.arguments.get("directory", "")).strip()
    target = MEMORY_DIR / directory if directory else MEMORY_DIR

    ok, result = _validate_path(str(target))
    if not ok:
        return ToolExecutionResult(success=False, payload={"error": result}, reason=str(result))

    def _list() -> str:
        if not target.exists():
            return f"目录不存在: {directory or 'memory/'}"
        entries = []
        for item in sorted(target.rglob("*")):
            if item.is_file():
                rel = item.relative_to(MEMORY_DIR)
                size = item.stat().st_size
                size_str = f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB"
                entries.append(f"  {size_str}\t{rel}")
        if not entries:
            return "(记忆目录为空)"
        return f"记忆文件 ({len(entries)} 个):\n" + "\n".join(entries)

    output = await asyncio.to_thread(_list)
    return ToolExecutionResult(success=True, payload={"output": output})


async def _execute_memory_read(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    path_str = str(request.arguments.get("path", "")).strip()
    if not path_str:
        return ToolExecutionResult(success=False, payload={"error": "缺少 path 参数"}, reason="缺少 path 参数")

    full_path = MEMORY_DIR / path_str
    ok, result = _validate_path(str(full_path))
    if not ok:
        return ToolExecutionResult(success=False, payload={"error": result}, reason=str(result))

    def _read() -> tuple[bool, str]:
        if not full_path.exists():
            return False, f"文件不存在: {path_str}"
        content = full_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        numbered = "\n".join(f"{i + 1:>4}\t{line}" for i, line in enumerate(lines))
        return True, f"=== {path_str} ===\n{numbered}"

    success, output = await asyncio.to_thread(_read)
    if not success:
        return ToolExecutionResult(success=False, payload={"error": output}, reason=output)
    return ToolExecutionResult(success=True, payload={"output": output})


async def _execute_memory_edit(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    path_str = str(request.arguments.get("path", "")).strip()
    old_text = str(request.arguments.get("old_text", ""))
    new_text = str(request.arguments.get("new_text", ""))

    if not path_str:
        return ToolExecutionResult(success=False, payload={"error": "缺少 path 参数"}, reason="缺少 path 参数")

    full_path = MEMORY_DIR / path_str
    ok, result = _validate_path(str(full_path))
    if not ok:
        return ToolExecutionResult(success=False, payload={"error": result}, reason=str(result))

    def _edit() -> tuple[bool, str]:
        if not full_path.exists():
            return False, f"文件不存在: {path_str}"
        content = full_path.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count == 0:
            return False, "未找到要替换的文本，请确认 old_text 精确匹配文件内容。"
        if count > 1:
            return False, f"old_text 在文件中出现了 {count} 次，请提供更精确的匹配文本。"
        full_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return True, f"已更新 {path_str}"

    success, output = await asyncio.to_thread(_edit)
    if not success:
        return ToolExecutionResult(success=False, payload={"error": output}, reason=output)
    return ToolExecutionResult(success=True, payload={"output": output})


async def _execute_memory_delete(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path_str = str(request.arguments.get("path", "")).strip()
    text_to_remove = request.arguments.get("text_to_remove")

    if not path_str:
        return ToolExecutionResult(success=False, payload={"error": "缺少 path 参数"}, reason="缺少 path 参数")

    full_path = MEMORY_DIR / path_str
    ok, result = _validate_path(str(full_path))
    if not ok:
        return ToolExecutionResult(success=False, payload={"error": result}, reason=str(result))

    def _delete() -> tuple[bool, str]:
        if not full_path.exists():
            return False, f"文件不存在: {path_str}"
        if text_to_remove:
            content = full_path.read_text(encoding="utf-8")
            if text_to_remove not in content:
                return False, "未找到要删除的文本。"
            new_content = content.replace(text_to_remove, "", 1).strip()
            if new_content:
                full_path.write_text(new_content + "\n", encoding="utf-8")
            else:
                full_path.unlink()
            return True, f"已从 {path_str} 中删除指定内容。"
        full_path.unlink()
        return True, f"已删除文件 {path_str}"

    success, output = await asyncio.to_thread(_delete)
    if not success:
        return ToolExecutionResult(success=False, payload={"error": output}, reason=output)

    # 同步索引：删除文件时移除对应的索引条目
    if context.memory_index is not None:
        rel_path = path_str
        try:
            context.memory_index.remove_by_source_file(rel_path)
        except Exception as e:
            logger.warning("同步删除索引条目失败 %s: %s", rel_path, e)

    return ToolExecutionResult(success=True, payload={"output": output})


async def _execute_memory_search(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    keyword = str(request.arguments.get("keyword", "")).strip()
    if not keyword:
        return ToolExecutionResult(success=False, payload={"error": "缺少 keyword 参数"}, reason="缺少 keyword 参数")

    def _search() -> str:
        if not MEMORY_DIR.exists():
            return "(记忆目录为空)"
        matches = []
        for file_path in sorted(MEMORY_DIR.rglob("*.md")):
            try:
                content = file_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if keyword.lower() in content.lower():
                rel = file_path.relative_to(MEMORY_DIR)
                for i, line in enumerate(content.split("\n"), 1):
                    if keyword.lower() in line.lower():
                        matches.append(f"  {rel}:{i}  {line.strip()}")
        if not matches:
            return f"未找到包含 '{keyword}' 的记忆。"
        shown = matches[:20]
        suffix = f"\n（共 {len(matches)} 条，仅显示前 20 条）" if len(matches) > 20 else ""
        return f"找到 {len(matches)} 条匹配:\n" + "\n".join(shown) + suffix

    output = await asyncio.to_thread(_search)
    return ToolExecutionResult(success=True, payload={"output": output})


# 导出供 registry.py 使用
MEMORY_CRUD_EXECUTORS = {
    "memory_list": _execute_memory_list,
    "memory_read": _execute_memory_read,
    "memory_edit": _execute_memory_edit,
    "memory_delete": _execute_memory_delete,
    "memory_search": _execute_memory_search,
}
