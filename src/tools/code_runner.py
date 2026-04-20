"""Python 代码执行沙箱 — 在临时目录中安全运行用户代码。"""

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.credential_sanitizer import sanitize_env, redact_secrets, truncate_head_tail

logger = logging.getLogger("lapwing.tools.code_runner")

# 输出截断上限（字符数）
_MAX_OUTPUT = 2000


@dataclass
class CodeResult:
    """代码执行结果。"""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


async def run_python(code: str, timeout: int = 10) -> CodeResult:
    """在隔离的临时目录中执行 Python 代码。

    Args:
        code: 要执行的 Python 源代码
        timeout: 超时秒数，默认 10 秒

    Returns:
        CodeResult，包含 stdout、stderr、exit_code 和 timed_out 标志
    """
    tmp_dir = tempfile.mkdtemp(prefix="lapwing_coder_")
    script_path = Path(tmp_dir) / "script.py"
    try:
        script_path.write_text(code, encoding="utf-8")

        clean_env = sanitize_env(dict(os.environ))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp_dir,
            env=clean_env,
        )

        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.warning(f"[code_runner] 执行超时（{timeout}s）")
            return CodeResult(stdout="", stderr="", exit_code=-1, timed_out=True)

        stdout = redact_secrets(truncate_head_tail(
            raw_out.decode("utf-8", errors="replace"), _MAX_OUTPUT
        ))
        stderr = redact_secrets(truncate_head_tail(
            raw_err.decode("utf-8", errors="replace"), _MAX_OUTPUT
        ))
        exit_code = proc.returncode if proc.returncode is not None else -1

        logger.info(f"[code_runner] 执行完成 exit_code={exit_code}, stdout={len(stdout)}字节")
        return CodeResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    except Exception as e:
        logger.error(f"[code_runner] 执行异常: {e}")
        return CodeResult(stdout="", stderr=str(e), exit_code=-1)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
