"""安全的本地 Shell 执行器。"""

import asyncio
import time
import getpass
import json
import logging
import pwd
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import (
    LOGS_DIR,
    SHELL_ALLOW_SUDO,
    SHELL_DEFAULT_CWD,
    SHELL_ENABLED,
    SHELL_MAX_OUTPUT_CHARS,
    SHELL_TIMEOUT,
)
from src.core.credential_sanitizer import redact_secrets, truncate_head_tail
from src.core.execution_sandbox import ExecutionSandbox, SandboxTier

# Docker sandbox 配置（可选）
_SHELL_BACKEND = "local"  # "local" | "docker"
_DOCKER_IMAGE = "lapwing-sandbox:latest"
_DOCKER_WORKSPACE = "/home/lapwing/workspace"

def _load_docker_config():
    """从环境变量加载 Docker 配置。"""
    import os
    global _SHELL_BACKEND, _DOCKER_IMAGE, _DOCKER_WORKSPACE
    _SHELL_BACKEND = os.getenv("SHELL_BACKEND", "local")
    _DOCKER_IMAGE = os.getenv("SHELL_DOCKER_IMAGE", "lapwing-sandbox:latest")
    _DOCKER_WORKSPACE = os.getenv("SHELL_DOCKER_WORKSPACE", "/home/lapwing/workspace")

_load_docker_config()
_sandbox = ExecutionSandbox(docker_image=_DOCKER_IMAGE)

logger = logging.getLogger("lapwing.tools.shell_executor")

_CURRENT_USER = getpass.getuser()
_LOG_FILE = LOGS_DIR / "shell_execution.log"
_DEFAULT_CWD = str(Path(SHELL_DEFAULT_CWD).resolve())
_PROTECTED_PREFIXES = (
    "/etc",
    "/usr",
    "/boot",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/root",
)
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};\s*:", "检测到 fork bomb，已拒绝执行。"),
    (r"(?:^|[;&|]\s*|(?:sudo)\s+)rm\s+-rf\s+/(?:\s|$)", "检测到破坏性删除根目录命令，已拒绝执行。"),
    (r"\bdd\b", "检测到危险磁盘写入命令 dd，已拒绝执行。"),
    (r"\bmkfs(?:\.\w+)?\b", "检测到格式化磁盘命令，已拒绝执行。"),
    (r"\b(?:shutdown|reboot|poweroff|halt)\b", "检测到关机或重启命令，已拒绝执行。"),
    (r"\bsystemctl\s+(?:reboot|poweroff|halt|suspend)\b", "检测到系统级电源命令，已拒绝执行。"),
]
_INTERACTIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:vim|vi|nano|emacs)\b", "检测到交互式编辑器，已拒绝执行。"),
    (r"\b(?:top|htop|watch)\b", "检测到交互式监控命令，已拒绝执行。"),
    (r"\b(?:less|more|man)\b", "检测到交互式分页命令，已拒绝执行。"),
    (r"\btail\s+-f\b", "检测到持续跟随输出的命令，已拒绝执行。"),
    (r"\bread\s+[A-Za-z_][A-Za-z0-9_]*", "检测到需要交互输入的命令，已拒绝执行。"),
]
_WRITE_PATTERNS = (
    r"\brm\b",
    r"\bmv\b",
    r"\bcp\b",
    r"\binstall\b",
    r"\btee\b",
    r"\bsed\s+-i\b",
    r"\bperl\s+-i\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bchgrp\b",
    r"\bln\b",
    r"\btouch\b",
    r"\bmkdir\b",
    r"\brmdir\b",
    r"\btruncate\b",
)


@dataclass
class ShellResult:
    """Shell 命令执行结果。"""

    stdout: str
    stderr: str
    return_code: int
    timed_out: bool = False
    blocked: bool = False
    reason: str = ""
    cwd: str = _DEFAULT_CWD
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "timed_out": self.timed_out,
            "blocked": self.blocked,
            "reason": self.reason,
            "cwd": self.cwd,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "duration": self.duration,
        }


def _truncate_output(text: str) -> tuple[str, bool]:
    limit = max(SHELL_MAX_OUTPUT_CHARS, 1)
    if len(text) <= limit:
        return redact_secrets(text), False
    return redact_secrets(truncate_head_tail(text, limit)), True


def _looks_like_write_command(command: str) -> bool:
    lowered = command.lower()
    if ">" in lowered:
        return True
    return any(re.search(pattern, lowered) for pattern in _WRITE_PATTERNS)


def _other_home_prefixes() -> list[str]:
    prefixes: list[str] = []
    for entry in pwd.getpwall():
        home_dir = str(entry.pw_dir).strip()
        if not home_dir.startswith("/home/"):
            continue
        if entry.pw_name == _CURRENT_USER:
            continue
        prefixes.append(home_dir)
    return prefixes


def _blocked_reason(command: str) -> str | None:
    stripped = command.strip()
    if not stripped:
        return "命令为空。"

    lowered = stripped.lower()

    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, lowered):
            return reason

    if not SHELL_ALLOW_SUDO and re.search(r"\bsudo\b", lowered):
        return "不允许通过 sudo 执行命令。如需启用，请在 config/.env 中设置 SHELL_ALLOW_SUDO=true，并配置系统 sudoers 免密。"

    for pattern, reason in _INTERACTIVE_PATTERNS:
        if re.search(pattern, lowered):
            return reason

    if _looks_like_write_command(lowered):
        for prefix in _PROTECTED_PREFIXES:
            if re.search(rf"(?<!\w){re.escape(prefix.lower())}(?:/|\b)", lowered):
                return f"检测到对受保护路径 `{prefix}` 的修改意图，已拒绝执行。"

        for prefix in _other_home_prefixes():
            if re.search(rf"(?<!\w){re.escape(prefix.lower())}(?:/|\b)", lowered):
                return f"检测到对其他用户目录 `{prefix}` 的修改意图，已拒绝执行。"

    return None


def _build_blocked_result(reason: str) -> ShellResult:
    return ShellResult(
        stdout="",
        stderr="",
        return_code=-1,
        blocked=True,
        reason=reason,
        cwd=_DEFAULT_CWD,
    )


def _append_log(record: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with _LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _log_execution(command: str, result: ShellResult) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "command": command,
        **result.to_dict(),
    }
    await asyncio.to_thread(_append_log, record)


async def _execute_docker(command: str) -> ShellResult:
    """在 Docker 容器中执行命令（沙箱隔离）。"""
    result = await _sandbox.run(
        ["bash", "-c", command],
        tier=SandboxTier.STANDARD,
        timeout=SHELL_TIMEOUT,
        workspace=_DOCKER_WORKSPACE,
    )
    shell_result = ShellResult(
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.exit_code,
        timed_out=result.timed_out,
        reason="" if result.exit_code == 0 else (
            f"Docker 命令执行超时（{SHELL_TIMEOUT}s）。" if result.timed_out
            else f"Docker 命令以退出码 {result.exit_code} 结束。"
        ),
        cwd="/workspace",
        stdout_truncated=len(result.stdout) >= SHELL_MAX_OUTPUT_CHARS,
        stderr_truncated=len(result.stderr) >= SHELL_MAX_OUTPUT_CHARS,
    )
    await _log_execution(f"[docker] {command}", shell_result)
    return shell_result


async def execute(command: str) -> ShellResult:
    """执行 shell 命令并返回真实结果。"""
    start = time.perf_counter()
    if not SHELL_ENABLED:
        result = _build_blocked_result("本地 shell 执行已禁用。")
        await _log_execution(command, result)
        return result

    reason = _blocked_reason(command)
    if reason is not None:
        logger.warning(f"[shell] 拒绝执行命令: {command!r} — {reason}")
        result = _build_blocked_result(reason)
        await _log_execution(command, result)
        return result

    if _SHELL_BACKEND == "docker":
        return await _execute_docker(command)

    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_DEFAULT_CWD,
        )

        try:
            raw_stdout, raw_stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=SHELL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raw_stdout, raw_stderr = await proc.communicate()

            stdout, stdout_truncated = _truncate_output(
                raw_stdout.decode("utf-8", errors="replace")
            )
            stderr, stderr_truncated = _truncate_output(
                raw_stderr.decode("utf-8", errors="replace")
            )
            result = ShellResult(
                stdout=stdout,
                stderr=stderr,
                return_code=-1,
                timed_out=True,
                reason=f"命令执行超时（{SHELL_TIMEOUT}s）。",
                cwd=_DEFAULT_CWD,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
            logger.warning(f"[shell] 命令超时: {command!r}")
            await _log_execution(command, result)
            return result

        stdout, stdout_truncated = _truncate_output(
            raw_stdout.decode("utf-8", errors="replace")
        )
        stderr, stderr_truncated = _truncate_output(
            raw_stderr.decode("utf-8", errors="replace")
        )
        return_code = proc.returncode if proc.returncode is not None else -1
        result = ShellResult(
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            reason="" if return_code == 0 else f"命令以退出码 {return_code} 结束。",
            cwd=_DEFAULT_CWD,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        logger.info(f"[shell] 命令执行完成 exit={return_code}: {command!r}")
        await _log_execution(command, result)
        return result

    except Exception as exc:
        logger.exception(f"[shell] 执行异常: {command!r}")
        result = ShellResult(
            stdout="",
            stderr=str(exc),
            return_code=-1,
            reason="执行 shell 命令时发生异常。",
            cwd=_DEFAULT_CWD,
        )
        await _log_execution(command, result)
        return result
