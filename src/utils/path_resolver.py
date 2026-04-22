"""工具路径自动修正 — 把 LLM 常见的路径误写映射回项目目录。"""

from __future__ import annotations

import os
from pathlib import Path

from config.settings import ROOT_DIR

_ROOT_STR = str(ROOT_DIR)

_KNOWN_PROJECT_PREFIXES: list[tuple[str, str]] = [
    ("/data/", "data/"),
    ("/consciousness/", "data/consciousness/"),
    ("/app/", ""),
]


def resolve_tool_path(raw_path: str) -> tuple[str, str | None]:
    """将 LLM 传入的路径修正为可用的绝对路径。

    Returns (resolved_path, correction_note).
    correction_note 为 None 表示无需修正。
    """
    stripped = raw_path.strip()
    if not stripped:
        return stripped, None

    if not os.path.isabs(stripped):
        return str(ROOT_DIR / stripped), None

    if stripped.startswith(_ROOT_STR + "/") or stripped == _ROOT_STR:
        return stripped, None

    for prefix, replacement in _KNOWN_PROJECT_PREFIXES:
        if stripped.startswith(prefix) or stripped == prefix.rstrip("/"):
            suffix = stripped[len(prefix):] if stripped.startswith(prefix) else ""
            resolved = str(ROOT_DIR / replacement / suffix) if replacement else str(ROOT_DIR / suffix)
            note = f"路径已自动修正: {stripped} → {resolved}"
            return resolved, note

    return stripped, None
