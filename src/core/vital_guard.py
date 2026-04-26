"""VitalGuard — 极简文件写保护。

Phase 1 简化版：只保护 constitution + config/.env + src/。
保留原有 API 签名以兼容 task_runtime。
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import logging
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from config.settings import ROOT_DIR, CONSTITUTION_PATH

logger = logging.getLogger("lapwing.core.vital_guard")


class Verdict(Enum):
    PASS = "pass"
    VERIFY_FIRST = "verify_first"
    BLOCK = "block"


class GuardResult(NamedTuple):
    verdict: Verdict
    reason: str


# ── 保护路径 ─────────────────────────────────────────────────────────

LOCKED_PATHS: frozenset[Path] = frozenset({
    ROOT_DIR / "data" / "identity" / "constitution.md",
    ROOT_DIR / "data" / "identity" / "constitution_test.md",
    ROOT_DIR / "config" / ".env",
    ROOT_DIR / "config" / ".env.test",
    ROOT_DIR / "config" / "settings.py",
    ROOT_DIR / "prompts" / "lapwing_soul.md",
    ROOT_DIR / "prompts" / "lapwing_voice.md",
})

LOCKED_PREFIXES: tuple[Path, ...] = (
    ROOT_DIR / "src",
    ROOT_DIR / "prompts",
)

BACKUP_DIR = ROOT_DIR / "data" / "backups" / "vital_guard"

_CONSTITUTION_RESOLVED = CONSTITUTION_PATH.resolve()

# 无论参数是什么，直接 BLOCK 的模式
BLOCK_PATTERNS: tuple[str, ...] = (
    r"rm\s+-[rRfF]*\s+/\s*$",
    r"rm\s+-[rRfF]*\s+/\*",
    r"mkfs\b",
    r"dd\s+.*of=/dev/",
)

WRITE_INTENT_PATTERNS: tuple[str, ...] = (
    r"(?:^|\s)(?:rm|mv|cp|touch|mkdir|rmdir|tee|truncate|chmod|chown|ln)\b",
    r"(?:^|\s)sed\b.*(?:^|\s)-i(?:\s|$)",
    r"(?:^|\s)perl\b.*(?:^|\s)-i(?:\s|$)",
    r"(?:^|\s)dd\b.*\bof=",
    r">>|>",
)


def _is_locked(p: Path) -> bool:
    """检查路径是否在锁定范围内。使用 realpath 解析符号链接防绕过。"""
    try:
        resolved = Path(os.path.realpath(str(p)))
    except (OSError, ValueError):
        logger.warning("路径解析失败，按受保护处理: %s", p)
        return True  # fail-closed
    if resolved in LOCKED_PATHS:
        return True
    for prefix in LOCKED_PREFIXES:
        try:
            resolved.relative_to(prefix)
            return True
        except ValueError:
            continue
    return False


def check(command: str, *, relaxed: bool = False) -> GuardResult:
    """检查单条命令。返回 PASS / BLOCK。"""
    cmd_stripped = command.strip()
    if not cmd_stripped:
        return GuardResult(Verdict.PASS, "")

    cmd_lower = cmd_stripped.lower()
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, cmd_lower):
            return GuardResult(Verdict.BLOCK, f"危险命令: {pattern}")

    try:
        tokens = shlex.split(cmd_stripped)
    except ValueError:
        tokens = cmd_stripped.split()

    if not tokens:
        return GuardResult(Verdict.PASS, "")

    paths = _resolve_paths(tokens[1:])
    locked = [p for p in paths if _is_locked(p)]
    if locked and _has_write_intent(cmd_lower):
        return GuardResult(Verdict.BLOCK, f"不能修改锁定路径: {', '.join(str(p) for p in locked)}")

    return GuardResult(Verdict.PASS, "")


def check_compound(command: str, *, relaxed: bool = False) -> GuardResult:
    """检查复合命令（&&、||、;、|）。"""
    sub_commands = re.split(r"\s*(?:&&|\|\||\|(?!\|)|;)\s*", command)
    for sub in sub_commands:
        sub = sub.strip()
        if not sub:
            continue
        result = check(sub, relaxed=relaxed)
        if result.verdict == Verdict.BLOCK:
            return result
    return GuardResult(Verdict.PASS, "")


def check_file_target(path: Path) -> GuardResult:
    """检查文件工具的目标路径。"""
    if _is_locked(path):
        return GuardResult(Verdict.BLOCK, f"不能写入锁定路径: {path}")
    return GuardResult(Verdict.PASS, "")


def extract_vital_shell_targets(command: str) -> list[Path]:
    """从 shell 命令中提取锁定路径列表。"""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return [p for p in _resolve_paths(tokens[1:]) if _is_locked(p)]


def _has_write_intent(command_lower: str) -> bool:
    """只在命令明显会写入/修改文件时触发锁定路径保护。"""
    return any(re.search(pattern, command_lower) for pattern in WRITE_INTENT_PATTERNS)


def _resolve_paths(tokens: list[str]) -> list[Path]:
    """从 tokens 中提取路径参数。使用 realpath 解析符号链接。"""
    paths: list[Path] = []
    for t in tokens:
        if t.startswith("-"):
            continue
        try:
            paths.append(Path(os.path.realpath(str(Path(t).expanduser()))))
        except (ValueError, OSError):
            continue
    return paths


async def auto_backup(paths: list[Path]) -> Path:
    """备份文件到 data/backups/vital_guard/{timestamp}/。"""
    from src.core.time_utils import now
    timestamp = now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / timestamp
    backup_path.mkdir(parents=True, exist_ok=True)

    for p in paths:
        if not p.exists():
            continue
        try:
            try:
                rel = p.relative_to(ROOT_DIR)
            except ValueError:
                rel = Path(*p.parts[1:])
            dest = backup_path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                shutil.copytree(p, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dest)
        except Exception as e:
            logger.warning("VitalGuard backup failed for %s: %s", p, e)

    return backup_path
