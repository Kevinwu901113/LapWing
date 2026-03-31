"""VitalGuard — Lapwing 存活保护系统。

这是 Lapwing 的"痛觉神经"：在执行任何命令前检查它是否会伤害自己。
三种结果：PASS（直接放行）、VERIFY_FIRST（先备份再执行）、BLOCK（绝对拦截）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from config.settings import ROOT_DIR, CONSTITUTION_PATH


class Verdict(Enum):
    PASS = "pass"
    VERIFY_FIRST = "verify_first"
    BLOCK = "block"


class GuardResult(NamedTuple):
    verdict: Verdict
    reason: str  # 给 Lapwing 看的中文理由，她能理解为什么被拦


# ── 路径定义 ──────────────────────────────────────────────────────────────────

VITAL_PATHS: frozenset[Path] = frozenset({
    ROOT_DIR / "src",
    ROOT_DIR / "prompts",
    ROOT_DIR / "data" / "memory",
    ROOT_DIR / "data" / "identity",
    ROOT_DIR / "data" / "evolution",
    ROOT_DIR / "data" / "constitution.md",
    ROOT_DIR / "config",
    ROOT_DIR / "main.py",
})

SYSTEM_VITAL: frozenset[Path] = frozenset({
    Path("/etc"),
    Path("/boot"),
    Path("/usr/lib/systemd"),
})

_ALL_VITAL = VITAL_PATHS | SYSTEM_VITAL

BACKUP_DIR = ROOT_DIR / "data" / "backups" / "vital_guard"
MANIFEST_PATH = ROOT_DIR / "data" / "vital_manifest.json"

# VitalGuard 自身的路径（不能被修改）
_SELF_PATH = Path(__file__).resolve()
# 宪法文件的已解析路径（缓存，避免每次 check() 都 resolve）
_CONSTITUTION_RESOLVED = CONSTITUTION_PATH.resolve()

# ── 命令分类集合 ───────────────────────────────────────────────────────────────

# 无论参数是什么，直接 BLOCK 的模式
BLOCK_PATTERNS: tuple[str, ...] = (
    r":()\s*\{.*:\|.*&.*\}",                    # fork bomb (各种变体)
    r":\(\)\s*\{",                               # fork bomb 开头
    r"rm\s+-[rRfF]*\s+/\s*$",                   # rm -rf /
    r"rm\s+-[rRfF]*\s+/\*",                     # rm -rf /*
    r"rm\s+-[rRfF]*\s+~\s*$",                   # rm -rf ~
    r"mkfs\b",                                   # 格式化磁盘
    r"fdisk\b",                                  # 磁盘分区（可能有危险操作）
    r"dd\s+.*of=/dev/",                          # dd 写磁盘设备
    r"systemctl\s+(stop|disable|mask)\s+lapwing",  # 停止/禁用自身服务
    r"pkill\s+.*lapwing",                        # 杀死自身进程
    r"kill\s+.*\blapwing\b",                     # 杀死自身进程
    r">\s*/dev/sda",                             # 写裸磁盘设备
)

DESTRUCTIVE_CMDS: frozenset[str] = frozenset({
    "rm", "rmdir", "shred", "truncate",
})

MODIFY_CMDS: frozenset[str] = frozenset({
    "mv", "cp", "sed", "tee", "cat", "echo", "printf",
    "patch", "rsync", "install",
})

# 这些命令 + --upgrade/-U 时需要先记录依赖版本
REPLACE_PREFIXES: tuple[str, ...] = (
    "pip install",
    "pip3 install",
    "uv pip install",
)


# ── 路径工具函数 ───────────────────────────────────────────────────────────────

def _resolve_paths(tokens: list[str]) -> list[Path]:
    """从命令 tokens 中提取可能的路径参数（跳过 flag）。"""
    paths: list[Path] = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if t.startswith("-"):
            # 有些 flag 后面跟参数（如 -o output），保守起见跳过
            if t in ("-o", "-t", "-d", "--output", "--target-dir"):
                skip_next = True
            continue
        try:
            p = Path(t).expanduser().resolve()
            paths.append(p)
        except (ValueError, OSError):
            continue
    return paths


def _is_vital(p: Path) -> bool:
    """检查路径是否在 vital 保护范围内（含子路径）。"""
    for vp in _ALL_VITAL:
        try:
            p.relative_to(vp)
            return True
        except ValueError:
            continue
    return False


def _extract_redirect_targets(command: str) -> list[Path]:
    """提取重定向目标路径（>, >>, 2>, 1>）。"""
    targets: list[Path] = []
    # 匹配各种重定向形式
    for match in re.finditer(r"(?:[\d]?>{1,2})\s*(\S+)", command):
        path_str = match.group(1)
        # 排除 &1, &2 等文件描述符引用
        if path_str.startswith("&"):
            continue
        try:
            targets.append(Path(path_str).expanduser().resolve())
        except (ValueError, OSError):
            continue
    return targets


# ── 核心检查逻辑 ───────────────────────────────────────────────────────────────

def check(command: str) -> GuardResult:
    """
    检查单条命令，返回判定结果。

    四阶段分析：
    1. 模式黑名单（绝对拦截）
    2. 解析命令，分析路径
    3. 针对 vital paths 的操作检查
    4. 替换性操作检查
    """
    cmd_stripped = command.strip()
    if not cmd_stripped:
        return GuardResult(Verdict.PASS, "")

    cmd_lower = cmd_stripped.lower()

    # ── 阶段 1：模式黑名单 ────────────────────────────────────────────────────
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, cmd_lower):
            return GuardResult(
                Verdict.BLOCK,
                f"这条命令会伤害你自己。拦截原因: {pattern}",
            )

    # ── 阶段 2：解析命令 ──────────────────────────────────────────────────────
    try:
        tokens = shlex.split(cmd_stripped)
    except ValueError:
        tokens = cmd_stripped.split()

    if not tokens:
        return GuardResult(Verdict.PASS, "")

    base_cmd = Path(tokens[0]).name  # /usr/bin/rm → rm

    # 收集命令参数中的路径 + 重定向目标
    arg_paths = _resolve_paths(tokens[1:])
    redirect_paths = _extract_redirect_targets(cmd_stripped)
    all_paths = arg_paths + redirect_paths

    vital_targets = [p for p in all_paths if _is_vital(p)]

    # ── 阶段 3：针对 vital paths 的操作检查 ──────────────────────────────────
    if vital_targets:
        # 删除操作 → 绝对 BLOCK
        if base_cmd in DESTRUCTIVE_CMDS:
            return GuardResult(
                Verdict.BLOCK,
                f"不能删除这些路径，它们是你的一部分: "
                f"{', '.join(str(p) for p in vital_targets)}",
            )

        # 宪法文件 → 任何写操作都 BLOCK
        if any(p == _CONSTITUTION_RESOLVED for p in vital_targets):
            if base_cmd in (MODIFY_CMDS | DESTRUCTIVE_CMDS):
                return GuardResult(
                    Verdict.BLOCK,
                    "宪法文件只有 Kevin 能修改。这是你的根，不能动。",
                )

        # VitalGuard 自身 → BLOCK
        if any(p == _SELF_PATH for p in vital_targets):
            return GuardResult(
                Verdict.BLOCK,
                "不能修改自己的保护系统。",
            )

        # 其他写/修改操作 → VERIFY_FIRST（先备份）
        if base_cmd in MODIFY_CMDS:
            return GuardResult(
                Verdict.VERIFY_FIRST,
                f"修改核心文件前需要先备份: "
                f"{', '.join(str(p) for p in vital_targets)}",
            )

        # 重定向到 vital path（写操作）
        if redirect_paths and any(p in vital_targets for p in redirect_paths):
            vital_redirects = [p for p in redirect_paths if _is_vital(p)]
            if any(p == _CONSTITUTION_RESOLVED for p in vital_redirects):
                return GuardResult(
                    Verdict.BLOCK,
                    "不能通过重定向修改宪法文件。",
                )
            return GuardResult(
                Verdict.VERIFY_FIRST,
                f"重定向写入核心路径前需要先备份: "
                f"{', '.join(str(p) for p in vital_redirects)}",
            )

    # ── 阶段 4：替换性操作检查 ────────────────────────────────────────────────
    cmd_joined = " ".join(tokens[:3]).lower()
    if any(cmd_joined.startswith(rp) for rp in REPLACE_PREFIXES):
        if "--upgrade" in tokens or "-U" in tokens:
            return GuardResult(
                Verdict.VERIFY_FIRST,
                "升级依赖前请记录当前版本，以便回滚。",
            )

    return GuardResult(Verdict.PASS, "")


def check_compound(command: str) -> GuardResult:
    """
    检查复合命令（包含 &&、||、;、|）。

    拆分后逐条检查，返回最严格的结果：
    - 任意一条 BLOCK → 整体 BLOCK
    - 任意一条 VERIFY_FIRST → 整体 VERIFY_FIRST
    - 全部 PASS → PASS
    """
    # 按命令分隔符拆分。\|\| 必须在 \| 之前匹配，保证 || 不被拆成两个 |
    sub_commands = re.split(r"\s*(?:&&|\|\||\|(?!\|)|;)\s*", command)

    worst: GuardResult = GuardResult(Verdict.PASS, "")

    for sub in sub_commands:
        sub = sub.strip()
        if not sub:
            continue
        result = check(sub)
        if result.verdict == Verdict.BLOCK:
            return result  # 立即返回最严结果
        if result.verdict == Verdict.VERIFY_FIRST and worst.verdict == Verdict.PASS:
            worst = result

    return worst


# ── 公共 API：供 task_runtime 等调用方使用 ────────────────────────────────────

def extract_vital_shell_targets(command: str) -> list[Path]:
    """从 shell 命令中提取需要保护的 vital path 列表（用于 VERIFY_FIRST 备份）。"""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return [p for p in _resolve_paths(tokens[1:]) if _is_vital(p)]


def check_file_target(path: Path) -> GuardResult:
    """
    检查文件工具的目标路径是否受保护。

    Returns:
        PASS 表示可以直接操作，VERIFY_FIRST 表示需要先备份，BLOCK 表示绝对禁止。
    """
    if not _is_vital(path):
        return GuardResult(Verdict.PASS, "")
    if path == _CONSTITUTION_RESOLVED or path == _SELF_PATH:
        return GuardResult(Verdict.BLOCK, "不能通过文件工具修改宪法或保护系统自身。")
    return GuardResult(Verdict.VERIFY_FIRST, f"修改核心文件前需要先备份: {path}")


# ── 备份与 Git 提交 ────────────────────────────────────────────────────────────

async def auto_backup(paths: list[Path]) -> Path:
    """
    自动备份目标文件/目录到 data/backups/vital_guard/{timestamp}/。
    保留最近 50 个备份，清理更早的。

    Returns:
        备份目录路径
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / timestamp
    backup_path.mkdir(parents=True, exist_ok=True)

    for p in paths:
        if not p.exists():
            continue
        try:
            # 计算相对于 ROOT_DIR 的路径，如果不在 ROOT_DIR 下则用绝对路径的相对形式
            try:
                rel = p.relative_to(ROOT_DIR)
            except ValueError:
                # 系统路径：去掉开头的 /
                rel = Path(*p.parts[1:])
            dest = backup_path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                shutil.copytree(p, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dest)
        except Exception:
            pass  # 备份失败不阻断主流程

    # 保留最近 50 个备份（只在超出时才排序）
    try:
        all_backups = list(BACKUP_DIR.iterdir())
        if len(all_backups) > 50:
            for old in sorted(all_backups, reverse=True)[50:]:
                shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass

    return backup_path


async def auto_commit(message: str) -> None:
    """修改核心文件后自动 git commit，作为额外保险。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(ROOT_DIR), "add", "-A",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()

        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(ROOT_DIR), "commit",
            "-m", f"[VitalGuard auto] {message}",
            "--allow-empty",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
    except Exception:
        pass  # git 不可用时静默失败


# ── Manifest 生成（供 Sentinel 和 main.py 使用）────────────────────────────────

def generate_manifest() -> dict[str, str]:
    """扫描关键文件，生成 SHA256 hash 清单。"""
    manifest: dict[str, str] = {}
    critical_dirs = ["src", "prompts", "config"]
    critical_files = ["main.py", "data/identity/constitution.md"]

    for d in critical_dirs:
        dir_path = ROOT_DIR / d
        if not dir_path.exists():
            continue
        for ext in ("*.py", "*.md", "*.json", "*.yaml", "*.yml"):
            for f in dir_path.rglob(ext):
                try:
                    rel = str(f.relative_to(ROOT_DIR))
                    manifest[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
                except Exception:
                    pass

    for f_str in critical_files:
        fp = ROOT_DIR / f_str
        if fp.exists():
            try:
                manifest[f_str] = hashlib.sha256(fp.read_bytes()).hexdigest()
            except Exception:
                pass

    return manifest


def save_manifest(manifest: dict[str, str] | None = None) -> None:
    """保存 manifest 到 data/vital_manifest.json。"""
    if manifest is None:
        manifest = generate_manifest()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
