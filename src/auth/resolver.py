from __future__ import annotations

import os
import shlex
import subprocess
from typing import Any

from src.auth.models import SecretRef


def resolve_secret_ref(secret_ref: dict[str, Any]) -> str:
    kind = str(secret_ref.get("kind") or "").strip().lower()
    if kind == "literal":
        return str(secret_ref.get("value") or "")
    if kind == "env":
        env_name = str(secret_ref.get("name") or "").strip()
        if not env_name:
            raise ValueError("env secretRef 缺少 name")
        value = os.getenv(env_name, "")
        if not value:
            raise ValueError(f"环境变量未配置: {env_name}")
        return value
    if kind == "command":
        command = str(secret_ref.get("command") or "").strip()
        if not command:
            raise ValueError("command secretRef 缺少 command")
        result = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise ValueError(f"secretRef command 执行失败: {stderr}")
        return result.stdout.strip()
    raise ValueError(f"未知 secretRef kind: {kind}")
