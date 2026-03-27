"""verifier 模块测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.core import verifier
from src.core.shell_policy import ExecutionConstraints, verify_constraints
from src.tools.code_runner import CodeResult


def test_shell_verifier_compat_with_legacy_status(tmp_path):
    target_dir = tmp_path / "workspace"
    target_dir.mkdir(parents=True)
    (target_dir / "note.txt").write_text("hello", encoding="utf-8")

    constraints = ExecutionConstraints(
        original_user_message="创建 txt 文件",
        target_directory=str(target_dir),
        required_extension=".txt",
        is_write_request=True,
    )

    legacy = verify_constraints(constraints)
    unified = verifier.verify_shell_constraints(constraints)

    assert legacy.completed is True
    assert unified.passed is True
    assert str(target_dir) in unified.artifacts


def test_verify_file_result(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("Lapwing", encoding="utf-8")

    passed = verifier.verify_file_result(
        str(target),
        root_dir=tmp_path,
        expected_extension=".md",
        expected_contains="Lap",
    )
    failed = verifier.verify_file_result(
        str(target),
        root_dir=tmp_path,
        expected_extension=".txt",
    )

    assert passed.passed is True
    assert failed.passed is False
    assert "扩展名" in failed.reason


def test_verify_code_result():
    ok = verifier.verify_code_result(CodeResult(stdout="ok\n", stderr="", exit_code=0))
    bad = verifier.verify_code_result(CodeResult(stdout="", stderr="boom", exit_code=1))

    assert ok.passed is True
    assert bad.passed is False
    assert "boom" in bad.reason


@pytest.mark.asyncio
async def test_verify_workspace_quick_compile_failure(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def x(:\n    pass\n", encoding="utf-8")

    result = await verifier.verify_workspace(
        [str(bad_file)],
        root_dir=tmp_path,
    )

    assert result.passed is False
    assert result.status == "quick_failed"


@pytest.mark.asyncio
async def test_verify_workspace_runs_pytest_targets(tmp_path):
    target_file = tmp_path / "tests" / "test_demo.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("def test_demo():\n    assert True\n", encoding="utf-8")

    with patch("src.core.verifier._run_command", new=AsyncMock(return_value=(0, "ok", ""))) as mock_run:
        result = await verifier.verify_workspace(
            [str(target_file)],
            root_dir=tmp_path,
            pytest_targets=["tests/test_demo.py"],
        )

    assert result.passed is True
    assert any(check.get("name") == "pytest" for check in result.checks)
    assert mock_run.await_count == 1
