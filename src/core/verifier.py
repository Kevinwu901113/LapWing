"""统一验证模块：覆盖 shell / file / code / workspace 四类验证。"""

from __future__ import annotations

import asyncio
import py_compile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import ROOT_DIR
from src.tools.code_runner import CodeResult

_VERIFY_CONTENT_LIMIT = 600
_WORKSPACE_OUTPUT_LIMIT = 4000


@dataclass
class VerificationResult:
    passed: bool
    status: str
    reason: str = ""
    checks: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


def verify_shell_constraints_status(constraints):
    """返回兼容 shell_policy 的 VerificationStatus。"""
    from src.core.shell_policy import VerificationStatus

    target_directory = constraints.active_directory or constraints.target_directory
    if target_directory is None:
        return VerificationStatus(completed=False, reason="没有可验证的目标路径。")

    directory = Path(target_directory)
    if not directory.exists():
        return VerificationStatus(
            completed=False,
            directory_path=target_directory,
            reason=f"目标目录 `{target_directory}` 还不存在。",
        )
    if not directory.is_dir():
        return VerificationStatus(
            completed=False,
            directory_path=target_directory,
            reason=f"`{target_directory}` 不是目录。",
        )

    file_path: Path | None = None
    if constraints.required_filename:
        file_path = directory / constraints.required_filename
        if not file_path.exists() or not file_path.is_file():
            return VerificationStatus(
                completed=False,
                directory_path=target_directory,
                file_path=str(file_path),
                reason=f"目标文件 `{file_path}` 还不存在。",
            )
    elif constraints.required_extension:
        files = sorted(
            path for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == constraints.required_extension
        )
        if not files:
            return VerificationStatus(
                completed=False,
                directory_path=target_directory,
                reason=f"目录 `{target_directory}` 下还没有 {constraints.required_extension} 文件。",
            )
        file_path = files[0]

    file_content = ""
    if file_path is not None:
        try:
            file_content = file_path.read_text(encoding="utf-8")[:_VERIFY_CONTENT_LIMIT]
        except Exception as exc:
            return VerificationStatus(
                completed=False,
                directory_path=target_directory,
                file_path=str(file_path),
                reason=f"目标文件存在，但读取失败：{exc}",
            )

    return VerificationStatus(
        completed=True,
        directory_path=target_directory,
        file_path=str(file_path) if file_path is not None else None,
        file_content=file_content.strip(),
    )


def verify_shell_constraints(constraints) -> VerificationResult:
    status = verify_shell_constraints_status(constraints)
    artifacts: list[str] = []
    checks = [
        {
            "name": "shell_constraints",
            "passed": bool(status.completed),
            "reason": status.reason,
        }
    ]
    if status.directory_path:
        artifacts.append(status.directory_path)
    if status.file_path:
        artifacts.append(status.file_path)
    return VerificationResult(
        passed=bool(status.completed),
        status="passed" if status.completed else "failed",
        reason=status.reason,
        checks=checks,
        artifacts=artifacts,
    )


def verify_file_result(
    path: str,
    *,
    root_dir: Path | str = ROOT_DIR,
    expected_exists: bool = True,
    expected_extension: str | None = None,
    expected_contains: str | None = None,
) -> VerificationResult:
    root = Path(root_dir).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    checks: list[dict[str, Any]] = []
    artifacts = [str(resolved)]

    exists_ok = resolved.exists() if expected_exists else not resolved.exists()
    checks.append(
        {
            "name": "exists",
            "passed": exists_ok,
            "reason": "" if exists_ok else "文件存在性不符合预期。",
        }
    )
    if not exists_ok:
        return VerificationResult(
            passed=False,
            status="failed",
            reason="文件存在性不符合预期。",
            checks=checks,
            artifacts=artifacts,
        )

    if expected_exists and resolved.is_file() and expected_extension:
        ext_ok = resolved.suffix.lower() == expected_extension.lower()
        checks.append(
            {
                "name": "extension",
                "passed": ext_ok,
                "reason": "" if ext_ok else "文件扩展名不符合预期。",
            }
        )
        if not ext_ok:
            return VerificationResult(
                passed=False,
                status="failed",
                reason="文件扩展名不符合预期。",
                checks=checks,
                artifacts=artifacts,
            )

    if expected_exists and resolved.is_file() and expected_contains is not None:
        text = resolved.read_text(encoding="utf-8")
        content_ok = expected_contains in text
        checks.append(
            {
                "name": "contains",
                "passed": content_ok,
                "reason": "" if content_ok else "文件内容未包含预期片段。",
            }
        )
        if not content_ok:
            return VerificationResult(
                passed=False,
                status="failed",
                reason="文件内容未包含预期片段。",
                checks=checks,
                artifacts=artifacts,
            )

    return VerificationResult(
        passed=True,
        status="passed",
        checks=checks,
        artifacts=artifacts,
    )


def verify_code_result(
    result: CodeResult,
    *,
    require_stdout: bool = False,
) -> VerificationResult:
    checks: list[dict[str, Any]] = []

    checks.append(
        {
            "name": "not_timeout",
            "passed": not result.timed_out,
            "reason": "代码执行超时。" if result.timed_out else "",
        }
    )
    if result.timed_out:
        return VerificationResult(
            passed=False,
            status="timeout",
            reason="代码执行超时。",
            checks=checks,
        )

    exit_ok = result.exit_code == 0
    checks.append(
        {
            "name": "exit_code",
            "passed": exit_ok,
            "reason": "" if exit_ok else f"exit code={result.exit_code}",
        }
    )
    if not exit_ok:
        return VerificationResult(
            passed=False,
            status="failed",
            reason=result.stderr.strip() or f"exit code={result.exit_code}",
            checks=checks,
        )

    if require_stdout:
        stdout_ok = bool(result.stdout.strip())
        checks.append(
            {
                "name": "stdout",
                "passed": stdout_ok,
                "reason": "" if stdout_ok else "执行成功但无输出。",
            }
        )
        if not stdout_ok:
            return VerificationResult(
                passed=False,
                status="failed",
                reason="执行成功但无输出。",
                checks=checks,
            )

    return VerificationResult(
        passed=True,
        status="passed",
        checks=checks,
    )


async def _run_command(command: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_raw, stderr_raw = await proc.communicate()
    code = proc.returncode if proc.returncode is not None else -1
    stdout = stdout_raw.decode("utf-8", errors="replace")[:_WORKSPACE_OUTPUT_LIMIT]
    stderr = stderr_raw.decode("utf-8", errors="replace")[:_WORKSPACE_OUTPUT_LIMIT]
    return code, stdout, stderr


async def verify_workspace(
    changed_files: list[str],
    *,
    root_dir: Path | str = ROOT_DIR,
    pytest_targets: list[str] | None = None,
) -> VerificationResult:
    root = Path(root_dir).resolve()
    checks: list[dict[str, Any]] = []
    normalized: list[Path] = []

    for item in changed_files:
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if root not in resolved.parents and resolved != root:
            return VerificationResult(
                passed=False,
                status="failed",
                reason=f"检测到越界路径：{item}",
                checks=[{"name": "path_scope", "passed": False, "reason": "越界路径"}],
                artifacts=[],
            )
        normalized.append(resolved)

    python_files = [path for path in normalized if path.suffix == ".py" and path.exists()]
    for path in python_files:
        try:
            py_compile.compile(str(path), doraise=True)
            checks.append(
                {
                    "name": f"py_compile:{path.name}",
                    "passed": True,
                    "reason": "",
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": f"py_compile:{path.name}",
                    "passed": False,
                    "reason": str(exc),
                }
            )
            return VerificationResult(
                passed=False,
                status="quick_failed",
                reason=f"快速语法校验失败: {path}",
                checks=checks,
                artifacts=[str(path) for path in normalized],
            )

    targets: list[str] = []
    if pytest_targets:
        targets = [target for target in pytest_targets if target.strip()]
    elif normalized:
        targets = [
            str(path.relative_to(root))
            for path in normalized
            if path.exists() and "tests" in path.parts and path.suffix == ".py"
        ]

    if targets:
        pytest_bin = root / "venv" / "bin" / "pytest"
        if pytest_bin.exists():
            command = [str(pytest_bin), "-q", *targets]
        else:
            command = ["pytest", "-q", *targets]

        code, stdout, stderr = await _run_command(command, cwd=root)
        checks.append(
            {
                "name": "pytest",
                "passed": code == 0,
                "reason": "" if code == 0 else stderr or stdout or f"exit code={code}",
                "targets": targets,
            }
        )
        if code != 0:
            return VerificationResult(
                passed=False,
                status="failed",
                reason=stderr or stdout or f"pytest 失败，exit code={code}",
                checks=checks,
                artifacts=[str(path) for path in normalized],
            )

    return VerificationResult(
        passed=True,
        status="passed",
        checks=checks,
        artifacts=[str(path) for path in normalized],
    )
