"""统一执行沙盒 — 三档位 Docker 隔离。"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from enum import Enum

from src.core.credential_sanitizer import redact_secrets, sanitize_env, truncate_head_tail

logger = logging.getLogger("lapwing.core.execution_sandbox")

_DEFAULT_IMAGE = "lapwing-sandbox:latest"
_BRIDGE_NETWORK = "lapwing-sandbox"
_MAX_OUTPUT = 4000


class SandboxTier(Enum):
    STRICT = "strict"
    STANDARD = "standard"
    PRIVILEGED = "privileged"


_TIER_DEFAULTS: dict[SandboxTier, dict] = {
    SandboxTier.STRICT: {
        "memory": "256m",
        "cpus": "0.5",
        "network": "none",
        "workspace_ro": True,
    },
    SandboxTier.STANDARD: {
        "memory": "512m",
        "cpus": "1.0",
        "network": _BRIDGE_NETWORK,
        "workspace_ro": False,
    },
    SandboxTier.PRIVILEGED: {
        "memory": "1024m",
        "cpus": "2.0",
        "network": "host",
        "workspace_ro": False,
    },
}


def _load_tier_defaults() -> dict[SandboxTier, dict]:
    try:
        from config.settings import (
            SANDBOX_NETWORK,
            SANDBOX_STRICT_MEMORY_MB, SANDBOX_STRICT_CPUS,
            SANDBOX_STANDARD_MEMORY_MB, SANDBOX_STANDARD_CPUS,
            SANDBOX_PRIVILEGED_MEMORY_MB, SANDBOX_PRIVILEGED_CPUS,
        )
        return {
            SandboxTier.STRICT: {
                "memory": f"{SANDBOX_STRICT_MEMORY_MB}m",
                "cpus": str(SANDBOX_STRICT_CPUS),
                "network": "none",
                "workspace_ro": True,
            },
            SandboxTier.STANDARD: {
                "memory": f"{SANDBOX_STANDARD_MEMORY_MB}m",
                "cpus": str(SANDBOX_STANDARD_CPUS),
                "network": SANDBOX_NETWORK,
                "workspace_ro": False,
            },
            SandboxTier.PRIVILEGED: {
                "memory": f"{SANDBOX_PRIVILEGED_MEMORY_MB}m",
                "cpus": str(SANDBOX_PRIVILEGED_CPUS),
                "network": "host",
                "workspace_ro": False,
            },
        }
    except ImportError:
        return _TIER_DEFAULTS


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class ExecutionSandbox:
    """Unified Docker execution sandbox with three security tiers."""

    def __init__(self, docker_image: str = _DEFAULT_IMAGE):
        self._image = docker_image

    @staticmethod
    async def ensure_sandbox_network(network_name: str = _BRIDGE_NETWORK) -> None:
        """启动时确保 Docker 沙箱网络存在。"""
        import shutil
        import subprocess
        if not shutil.which("docker"):
            logger.info("Docker 未安装，跳过沙箱网络检查")
            return
        try:
            result = await asyncio.to_thread(
                subprocess.run, ["docker", "network", "inspect", network_name],
                capture_output=True)
            if result.returncode != 0:
                logger.info("创建 Docker 沙箱网络: %s", network_name)
                cr = await asyncio.to_thread(
                    subprocess.run, ["docker", "network", "create", network_name],
                    capture_output=True, text=True)
                if cr.returncode != 0:
                    logger.warning("沙箱网络创建失败: %s", cr.stderr.strip())
        except FileNotFoundError:
            logger.info("Docker 不可用，跳过")

    def _build_docker_flags(
        self,
        tier: SandboxTier,
        workspace: str | None,
    ) -> list[str]:
        cfg = _load_tier_defaults()[tier]
        flags = [
            "--rm",
            "--cap-drop=ALL",
            "--user", "sandboxuser",
            "--memory", cfg["memory"],
            "--memory-swap", cfg["memory"],
            "--cpus", cfg["cpus"],
        ]

        network = cfg["network"]
        if network == "host":
            flags.append("--network=host")
        else:
            flags.extend(["--network", network])

        if workspace:
            mount = f"{workspace}:/workspace"
            if cfg["workspace_ro"]:
                mount += ":ro"
            flags.extend(["-v", mount])
            flags.extend(["-w", "/workspace"])

        if tier != SandboxTier.PRIVILEGED:
            flags.extend(["--read-only"])
            flags.extend(["--tmpfs", "/tmp:rw,size=64m"])

        return flags

    async def run(
        self,
        command: list[str],
        *,
        tier: SandboxTier,
        timeout: int = 30,
        workspace: str | None = None,
        env: dict[str, str] | None = None,
        max_output: int = _MAX_OUTPUT,
    ) -> SandboxResult:
        """Run a command in a Docker container with the given tier."""
        allow_network = tier != SandboxTier.STRICT
        clean_env = sanitize_env(env, allow_network=allow_network) if env else None

        if clean_env and "PATH" in clean_env:
            _container_dirs = "/usr/local/bin:/usr/local/sbin"
            if _container_dirs not in clean_env["PATH"]:
                clean_env["PATH"] = _container_dirs + ":" + clean_env["PATH"]

        docker_flags = self._build_docker_flags(tier, workspace)

        docker_cmd = ["docker", "run"] + docker_flags
        if clean_env:
            for k, v in clean_env.items():
                docker_cmd.extend(["-e", f"{k}={v}"])
        docker_cmd.append(self._image)
        docker_cmd.extend(command)

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return SandboxResult(
                    stdout="", stderr="", exit_code=-1, timed_out=True,
                )

            stdout = redact_secrets(truncate_head_tail(
                raw_out.decode("utf-8", errors="replace"), max_output,
            ))
            stderr = redact_secrets(truncate_head_tail(
                raw_err.decode("utf-8", errors="replace"), max_output,
            ))
            exit_code = proc.returncode if proc.returncode is not None else -1
            return SandboxResult(
                stdout=stdout, stderr=stderr, exit_code=exit_code,
            )
        except FileNotFoundError:
            return SandboxResult(
                stdout="", stderr="Docker 未安装或不可用", exit_code=-1,
            )
        except Exception as e:
            logger.error("沙盒执行异常: %s", e)
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1)

    async def run_local(
        self,
        command: list[str],
        *,
        timeout: int = 30,
        cwd: str | None = None,
        max_output: int = _MAX_OUTPUT,
    ) -> SandboxResult:
        """Run a command locally with sanitized env and process-group isolation."""
        # 直接读 os.environ：沙箱需要复制+清洗当前进程的完整环境变量
        clean_env = sanitize_env(dict(os.environ))

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=clean_env,
                start_new_session=True,
            )
            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                import signal
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    proc.kill()
                await proc.communicate()
                return SandboxResult(
                    stdout="", stderr="", exit_code=-1, timed_out=True,
                )

            stdout = redact_secrets(truncate_head_tail(
                raw_out.decode("utf-8", errors="replace"), max_output,
            ))
            stderr = redact_secrets(truncate_head_tail(
                raw_err.decode("utf-8", errors="replace"), max_output,
            ))
            exit_code = proc.returncode if proc.returncode is not None else -1
            return SandboxResult(
                stdout=stdout, stderr=stderr, exit_code=exit_code,
            )
        except Exception as e:
            logger.error("本地执行异常: %s", e)
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1)
