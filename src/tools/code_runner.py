"""Python 代码执行沙箱 — 在 Docker STRICT 容器中运行 LLM 生成的代码。"""

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.execution_sandbox import ExecutionSandbox, SandboxTier

logger = logging.getLogger("lapwing.tools.code_runner")

_MAX_OUTPUT = 2000
_sandbox = ExecutionSandbox()


@dataclass
class CodeResult:
    """代码执行结果。"""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


async def run_python(code: str, timeout: int = 10) -> CodeResult:
    """在 Docker STRICT 沙盒中执行 Python 代码。"""
    tmp_dir = tempfile.mkdtemp(prefix="lapwing_coder_")
    tmp_path = Path(tmp_dir)
    script_path = tmp_path / "script.py"
    try:
        script_path.write_text(code, encoding="utf-8")
        tmp_path.chmod(0o755)
        script_path.chmod(0o644)

        result = await _sandbox.run(
            ["python3", "/workspace/script.py"],
            tier=SandboxTier.STRICT,
            timeout=timeout,
            workspace=tmp_dir,
            max_output=_MAX_OUTPUT,
        )

        logger.info(f"[code_runner] 执行完成 exit_code={result.exit_code}")
        return CodeResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )
    except Exception as e:
        logger.error(f"[code_runner] 执行异常: {e}")
        return CodeResult(stdout="", stderr=str(e), exit_code=-1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
