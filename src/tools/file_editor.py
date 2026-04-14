"""项目内文件编辑工具：提供可复用的细粒度编辑与事务回滚能力。"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import DATA_DIR, ROOT_DIR

_BACKUP_DIR = DATA_DIR / "backups" / "file_editor"
_MUTATING_OPS = {
    "replace_in_file",
    "replace_lines",
    "insert_before",
    "insert_after",
    "append_to_file",
    "write_file",
}


@dataclass
class FileEditResult:
    success: bool
    operation: str
    path: str
    changed: bool = False
    reason: str = ""
    content: str = ""
    diff: str = ""
    backup_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransactionResult:
    success: bool
    results: list[FileEditResult]
    changed_files: list[str] = field(default_factory=list)
    rolled_back: bool = False
    reason: str = ""


def _resolve_path(path: str, root_dir: Path | str = ROOT_DIR) -> Path:
    root = Path(root_dir).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    try:
        common = os.path.commonpath([str(resolved), str(root)])
    except ValueError as exc:
        raise ValueError(f"路径校验失败: {exc}") from exc

    if common != str(root):
        raise ValueError(f"路径超出项目根目录: {path}")
    return resolved


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".lapwing_edit_", dir=str(path.parent))
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _backup_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = str(path).lstrip("/").replace("/", "__")
    backup_path = _BACKUP_DIR / f"{safe_name}.{timestamp}.bak"
    shutil.copy2(path, backup_path)
    return str(backup_path)


def _build_diff(path: Path, old_text: str, new_text: str) -> str:
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"{path} (before)",
        tofile=f"{path} (after)",
        lineterm="",
    )
    return "\n".join(diff)


def read_file_segment(
    path: str,
    start_line: int,
    end_line: int,
    *,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "read_file_segment"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        if start_line < 1 or end_line < start_line:
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="start_line/end_line 参数不合法。",
            )
        if not abs_path.exists() or not abs_path.is_file():
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="文件不存在或不是普通文件。",
            )

        lines = _read_text(abs_path).splitlines()
        segment = lines[start_line - 1: end_line]
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            content="\n".join(segment),
            metadata={
                "start_line": start_line,
                "end_line": end_line,
                "actual_end_line": min(end_line, len(lines)),
                "total_lines": len(lines),
            },
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def preview_patch(
    path: str,
    new_content: str,
    *,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "preview_patch"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        old_content = _read_text(abs_path) if abs_path.exists() else ""
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            changed=(old_content != new_content),
            diff=_build_diff(abs_path, old_content, new_content),
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


# ── 模糊匹配策略 ────────────────────────────────────────────────────────────


def _match_line_trimmed(
    old_content: str, old_text: str, new_text: str, count: int,
) -> tuple[str, int] | None:
    """每行 strip() 后比较，找到匹配块后替换原始内容。"""
    content_lines = old_content.splitlines(keepends=True)
    search_lines = old_text.splitlines()
    search_stripped = [line.strip() for line in search_lines]
    if not search_stripped:
        return None

    matched = 0
    result_lines = list(content_lines)
    # 从后往前查找，避免替换后偏移影响
    positions = []
    for i in range(len(content_lines) - len(search_stripped) + 1):
        block = [content_lines[i + j].rstrip("\n\r").strip() for j in range(len(search_stripped))]
        if block == search_stripped:
            positions.append(i)

    for pos in reversed(positions[:count]):
        # 推断缩进：用原始内容第一行的缩进
        original_first = content_lines[pos]
        indent = original_first[: len(original_first) - len(original_first.lstrip())]
        # 检测 new_text 自身的基线缩进（第一行的缩进）
        new_lines = new_text.splitlines(keepends=True)
        new_base = new_lines[0][: len(new_lines[0]) - len(new_lines[0].lstrip())] if new_lines else ""
        indented_new = []
        for nl in new_lines:
            # 去掉 new_text 基线缩进，加上原始缩进，保留相对缩进
            if new_base and nl.startswith(new_base):
                relative = nl[len(new_base):]
                indented_new.append(indent + relative)
            else:
                # new_base 为空时，保留 new_text 行的原始相对缩进
                indented_new.append(indent + nl)
        # 确保最后一行有换行（如果原始块最后一行有的话）
        if indented_new and not indented_new[-1].endswith("\n"):
            if pos + len(search_stripped) < len(content_lines):
                indented_new[-1] += "\n"
        result_lines[pos: pos + len(search_stripped)] = indented_new
        matched += 1

    if matched == 0:
        return None
    return "".join(result_lines), matched


def _match_whitespace_normalized(
    old_content: str, old_text: str, new_text: str, count: int,
) -> tuple[str, int] | None:
    """将连续空白折叠为单空格后匹配，找到原始 span 后替换。"""
    def _normalize_ws(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    norm_content = _normalize_ws(old_content)
    norm_search = _normalize_ws(old_text)
    if not norm_search or norm_search not in norm_content:
        return None

    # 建立 normalized→original 的位置映射
    # 找到 normalized 文本中的匹配位置，映射回原始位置
    matched = 0
    result = old_content
    for _ in range(count):
        norm_result = _normalize_ws(result)
        idx = norm_result.find(norm_search)
        if idx < 0:
            break
        # 映射 normalized 索引回原始文本
        orig_start = _map_normalized_pos(result, idx)
        orig_end = _map_normalized_pos(result, idx + len(norm_search))
        if orig_start is None or orig_end is None:
            break
        result = result[:orig_start] + new_text + result[orig_end:]
        matched += 1

    return (result, matched) if matched > 0 else None


def _map_normalized_pos(text: str, norm_pos: int) -> int | None:
    """将 whitespace-normalized 位置映射回原始文本位置。"""
    orig_idx = 0
    norm_idx = 0
    in_space = False
    # 跳过前导空白（normalized 会 strip）
    while orig_idx < len(text) and text[orig_idx] in " \t\n\r":
        orig_idx += 1
    while orig_idx <= len(text) and norm_idx < norm_pos:
        if orig_idx >= len(text):
            return None
        ch = text[orig_idx]
        if ch in " \t\n\r":
            if not in_space:
                norm_idx += 1  # 折叠空白计为一个空格
                in_space = True
            orig_idx += 1
        else:
            norm_idx += 1
            orig_idx += 1
            in_space = False
    return orig_idx


def _match_indentation_flexible(
    old_content: str, old_text: str, new_text: str, count: int,
) -> tuple[str, int] | None:
    """完全忽略行首缩进匹配，用原始内容的缩进级别应用 new_text。"""
    content_lines = old_content.splitlines(keepends=True)
    search_lines = old_text.splitlines()
    search_content = [line.lstrip() for line in search_lines]
    if not search_content or not any(search_content):
        return None

    matched = 0
    result_lines = list(content_lines)
    positions = []
    for i in range(len(content_lines) - len(search_content) + 1):
        block = [content_lines[i + j].rstrip("\n\r").lstrip() for j in range(len(search_content))]
        if block == search_content:
            positions.append(i)

    for pos in reversed(positions[:count]):
        # 检测原始块的缩进模式
        original_indent = content_lines[pos][: len(content_lines[pos]) - len(content_lines[pos].lstrip())]
        new_lines = new_text.splitlines(keepends=True)
        # 检测 new_text 自身的缩进基线（第一行的缩进）
        new_base_indent = new_lines[0][: len(new_lines[0]) - len(new_lines[0].lstrip())] if new_lines else ""
        indented_new = []
        for nl in new_lines:
            # 去掉 new_text 的基线缩进，替换为原始缩进
            stripped = nl
            if new_base_indent and stripped.startswith(new_base_indent):
                stripped = stripped[len(new_base_indent):]
            indented_new.append(original_indent + stripped.lstrip() if not stripped.startswith(" ") else original_indent + stripped)
        if indented_new and not indented_new[-1].endswith("\n"):
            if pos + len(search_content) < len(content_lines):
                indented_new[-1] += "\n"
        result_lines[pos: pos + len(search_content)] = indented_new
        matched += 1

    return ("".join(result_lines), matched) if matched > 0 else None


_FUZZY_STRATEGIES = [
    ("line_trimmed", _match_line_trimmed),
    ("whitespace_normalized", _match_whitespace_normalized),
    ("indentation_flexible", _match_indentation_flexible),
]


def _fuzzy_find_and_replace(
    old_content: str, old_text: str, new_text: str, count: int,
) -> tuple[str, int, str]:
    """逐级尝试模糊匹配策略。返回 (new_content, matched_count, strategy_name)。"""
    for name, strategy_fn in _FUZZY_STRATEGIES:
        result = strategy_fn(old_content, old_text, new_text, count)
        if result is not None:
            return (*result, name)
    return (old_content, 0, "none")


def replace_in_file(
    path: str,
    old_text: str,
    new_text: str,
    *,
    use_regex: bool = False,
    fuzzy: bool = True,
    count: int = 1,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "replace_in_file"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        if not abs_path.exists() or not abs_path.is_file():
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="文件不存在或不是普通文件。",
            )

        old_content = _read_text(abs_path)
        fuzzy_strategy = None
        if use_regex:
            new_content, matched = re.subn(old_text, new_text, old_content, count=count)
        else:
            matched = old_content.count(old_text) if old_text else 0
            if matched > 0:
                new_content = old_content.replace(old_text, new_text, count)
                matched = min(matched, count)
            else:
                new_content = old_content

            # 精确匹配失败时尝试模糊匹配
            if matched == 0 and fuzzy:
                new_content, matched, fuzzy_strategy = _fuzzy_find_and_replace(
                    old_content, old_text, new_text, count,
                )

        if matched == 0:
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="未找到可替换的内容。",
            )

        if old_content == new_content:
            return FileEditResult(
                success=True,
                operation=operation,
                path=str(abs_path),
                changed=False,
                reason="替换后内容无变化。",
            )

        backup_path = _backup_file(abs_path)
        _write_text_atomic(abs_path, new_content)
        metadata: dict[str, Any] = {"matched": matched, "use_regex": use_regex}
        if fuzzy_strategy:
            metadata["fuzzy_strategy"] = fuzzy_strategy
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            changed=True,
            backup_path=backup_path,
            diff=_build_diff(abs_path, old_content, new_content),
            metadata=metadata,
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def replace_lines(
    path: str,
    start_line: int,
    end_line: int,
    new_text: str,
    *,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "replace_lines"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        if start_line < 1 or end_line < start_line:
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="start_line/end_line 参数不合法。",
            )
        if not abs_path.exists() or not abs_path.is_file():
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="文件不存在或不是普通文件。",
            )

        old_content = _read_text(abs_path)
        lines = old_content.splitlines()
        if end_line > len(lines):
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="行号超出文件范围。",
            )

        replacement = new_text.splitlines()
        merged = lines[: start_line - 1] + replacement + lines[end_line:]
        new_content = "\n".join(merged)
        if old_content.endswith("\n"):
            new_content = f"{new_content}\n"

        if old_content == new_content:
            return FileEditResult(
                success=True,
                operation=operation,
                path=str(abs_path),
                changed=False,
                reason="替换后内容无变化。",
            )

        backup_path = _backup_file(abs_path)
        _write_text_atomic(abs_path, new_content)
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            changed=True,
            backup_path=backup_path,
            diff=_build_diff(abs_path, old_content, new_content),
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def _find_anchor_span(
    content: str,
    anchor: str,
    *,
    use_regex: bool,
    occurrence: int,
) -> tuple[int, int] | None:
    if occurrence < 1:
        return None

    if use_regex:
        matches = list(re.finditer(anchor, content, flags=re.MULTILINE))
        if len(matches) < occurrence:
            return None
        target = matches[occurrence - 1]
        return target.start(), target.end()

    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        index = content.find(anchor, cursor)
        if index < 0:
            break
        spans.append((index, index + len(anchor)))
        cursor = index + max(len(anchor), 1)
    if len(spans) < occurrence:
        return None
    return spans[occurrence - 1]


def insert_before(
    path: str,
    anchor: str,
    new_text: str,
    *,
    use_regex: bool = False,
    occurrence: int = 1,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "insert_before"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        if not abs_path.exists() or not abs_path.is_file():
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="文件不存在或不是普通文件。",
            )

        old_content = _read_text(abs_path)
        span = _find_anchor_span(old_content, anchor, use_regex=use_regex, occurrence=occurrence)
        if span is None:
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="未找到锚点内容。",
            )

        start, _ = span
        new_content = f"{old_content[:start]}{new_text}{old_content[start:]}"
        backup_path = _backup_file(abs_path)
        _write_text_atomic(abs_path, new_content)
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            changed=True,
            backup_path=backup_path,
            diff=_build_diff(abs_path, old_content, new_content),
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def insert_after(
    path: str,
    anchor: str,
    new_text: str,
    *,
    use_regex: bool = False,
    occurrence: int = 1,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "insert_after"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        if not abs_path.exists() or not abs_path.is_file():
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="文件不存在或不是普通文件。",
            )

        old_content = _read_text(abs_path)
        span = _find_anchor_span(old_content, anchor, use_regex=use_regex, occurrence=occurrence)
        if span is None:
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="未找到锚点内容。",
            )

        _, end = span
        new_content = f"{old_content[:end]}{new_text}{old_content[end:]}"
        backup_path = _backup_file(abs_path)
        _write_text_atomic(abs_path, new_content)
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            changed=True,
            backup_path=backup_path,
            diff=_build_diff(abs_path, old_content, new_content),
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def append_to_file(
    path: str,
    content: str,
    *,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "append_to_file"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        old_content = _read_text(abs_path) if abs_path.exists() else ""
        new_content = f"{old_content}{content}"
        backup_path = _backup_file(abs_path)
        _write_text_atomic(abs_path, new_content)
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            changed=True,
            backup_path=backup_path,
            diff=_build_diff(abs_path, old_content, new_content),
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def write_file(
    path: str,
    content: str,
    *,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "write_file"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        old_content = _read_text(abs_path) if abs_path.exists() else ""
        backup_path = _backup_file(abs_path)
        _write_text_atomic(abs_path, content)
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            changed=(old_content != content),
            backup_path=backup_path,
            diff=_build_diff(abs_path, old_content, content),
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def list_directory(
    path: str,
    *,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    operation = "list_directory"
    try:
        abs_path = _resolve_path(path, root_dir=root_dir)
        if not abs_path.exists():
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="目录不存在。",
            )
        if not abs_path.is_dir():
            return FileEditResult(
                success=False,
                operation=operation,
                path=str(abs_path),
                reason="路径不是目录。",
            )

        entries = sorted(abs_path.iterdir(), key=lambda item: (item.is_file(), item.name))
        lines: list[str] = []
        structured: list[dict[str, Any]] = []
        for entry in entries:
            prefix = "[DIR]" if entry.is_dir() else "[FILE]"
            lines.append(f"{prefix} {entry.name}")
            structured.append(
                {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                }
            )
        return FileEditResult(
            success=True,
            operation=operation,
            path=str(abs_path),
            content="\n".join(lines) if lines else "（目录为空）",
            metadata={"entries": structured},
        )
    except Exception as exc:
        return FileEditResult(
            success=False,
            operation=operation,
            path=path,
            reason=str(exc),
        )


def apply_operation(
    operation: dict[str, Any],
    *,
    root_dir: Path | str = ROOT_DIR,
) -> FileEditResult:
    op = str(operation.get("op", "")).strip()
    path = str(operation.get("path", "")).strip()

    if op == "replace_in_file":
        return replace_in_file(
            path=path,
            old_text=str(operation.get("old_text", "")),
            new_text=str(operation.get("new_text", "")),
            use_regex=bool(operation.get("use_regex", False)),
            count=int(operation.get("count", 1) or 1),
            root_dir=root_dir,
        )
    if op == "replace_lines":
        return replace_lines(
            path=path,
            start_line=int(operation.get("start_line", 0)),
            end_line=int(operation.get("end_line", 0)),
            new_text=str(operation.get("new_text", "")),
            root_dir=root_dir,
        )
    if op == "insert_before":
        return insert_before(
            path=path,
            anchor=str(operation.get("anchor", "")),
            new_text=str(operation.get("new_text", "")),
            use_regex=bool(operation.get("use_regex", False)),
            occurrence=int(operation.get("occurrence", 1) or 1),
            root_dir=root_dir,
        )
    if op == "insert_after":
        return insert_after(
            path=path,
            anchor=str(operation.get("anchor", "")),
            new_text=str(operation.get("new_text", "")),
            use_regex=bool(operation.get("use_regex", False)),
            occurrence=int(operation.get("occurrence", 1) or 1),
            root_dir=root_dir,
        )
    if op == "append_to_file":
        return append_to_file(
            path=path,
            content=str(operation.get("content", "")),
            root_dir=root_dir,
        )
    if op == "write_file":
        return write_file(
            path=path,
            content=str(operation.get("content", "")),
            root_dir=root_dir,
        )
    if op == "preview_patch":
        return preview_patch(
            path=path,
            new_content=str(operation.get("new_content", "")),
            root_dir=root_dir,
        )
    if op == "read_file_segment":
        return read_file_segment(
            path=path,
            start_line=int(operation.get("start_line", 0)),
            end_line=int(operation.get("end_line", 0)),
            root_dir=root_dir,
        )

    return FileEditResult(
        success=False,
        operation=op or "unknown",
        path=path,
        reason=f"不支持的操作: {op}",
    )


def batch_apply(
    operations: list[dict[str, Any]],
    *,
    root_dir: Path | str = ROOT_DIR,
) -> list[FileEditResult]:
    return [apply_operation(operation, root_dir=root_dir) for operation in operations]


def transactional_apply(
    operations: list[dict[str, Any]],
    *,
    root_dir: Path | str = ROOT_DIR,
) -> TransactionResult:
    snapshots: dict[str, tuple[bool, str]] = {}
    results: list[FileEditResult] = []
    changed_files: list[str] = []

    def rollback() -> bool:
        try:
            for path_str, (existed, content) in snapshots.items():
                path = Path(path_str)
                if existed:
                    _write_text_atomic(path, content)
                else:
                    path.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    for operation in operations:
        op = str(operation.get("op", "")).strip()
        raw_path = str(operation.get("path", "")).strip()
        if op in _MUTATING_OPS:
            try:
                abs_path = _resolve_path(raw_path, root_dir=root_dir)
            except Exception as exc:
                result = FileEditResult(
                    success=False,
                    operation=op or "unknown",
                    path=raw_path,
                    reason=str(exc),
                )
                results.append(result)
                rolled_back = rollback()
                return TransactionResult(
                    success=False,
                    results=results,
                    changed_files=changed_files,
                    rolled_back=rolled_back,
                    reason=result.reason,
                )
            key = str(abs_path)
            if key not in snapshots:
                snapshots[key] = (
                    abs_path.exists(),
                    _read_text(abs_path) if abs_path.exists() and abs_path.is_file() else "",
                )

        result = apply_operation(operation, root_dir=root_dir)
        results.append(result)

        if not result.success:
            rolled_back = rollback()
            return TransactionResult(
                success=False,
                results=results,
                changed_files=changed_files,
                rolled_back=rolled_back,
                reason=result.reason or "事务执行失败。",
            )

        if result.changed and result.path not in changed_files:
            changed_files.append(result.path)

    return TransactionResult(
        success=True,
        results=results,
        changed_files=changed_files,
        rolled_back=False,
        reason="",
    )
