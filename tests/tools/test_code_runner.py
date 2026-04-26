"""code_runner 集成测试（Docker STRICT 沙盒执行）。"""

import os
import stat

import pytest
from unittest.mock import patch, AsyncMock
from src.tools.code_runner import run_python


@pytest.mark.asyncio
async def test_simple_print():
    """正常代码执行并返回 stdout。"""
    result = await run_python('print("hello")')
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_arithmetic():
    """数值计算正确性。"""
    result = await run_python("print(2 + 2)")
    assert result.exit_code == 0
    assert result.stdout.strip() == "4"


@pytest.mark.asyncio
async def test_syntax_error_returns_stderr():
    """语法错误时 exit_code != 0，stderr 非空。"""
    result = await run_python("def foo(:\n    pass")
    assert result.exit_code != 0
    assert result.stderr != ""
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_runtime_error_returns_stderr():
    """运行时错误时 exit_code != 0，stderr 包含错误信息。"""
    result = await run_python("1 / 0")
    assert result.exit_code != 0
    assert "ZeroDivisionError" in result.stderr


@pytest.mark.asyncio
async def test_timeout():
    """超时代码被中止，timed_out=True。"""
    result = await run_python("import time; time.sleep(999)", timeout=1)
    assert result.timed_out is True
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_stdout_truncated():
    """超过 2000 字符的 stdout 被截断。"""
    result = await run_python("print('A' * 3000)")
    assert result.exit_code == 0
    assert len(result.stdout) < 3000
    assert "truncated" in result.stdout.lower()


@pytest.mark.asyncio
async def test_multiline_output():
    """多行输出正确拼接。"""
    code = "for i in range(3):\n    print(i)"
    result = await run_python(code)
    assert result.exit_code == 0
    assert result.stdout.strip() == "0\n1\n2"


@pytest.mark.asyncio
async def test_tmp_dir_isolated():
    """临时目录隔离：脚本在独立目录运行，执行后目录被清理。"""
    import os
    result = await run_python("import os; print(os.getcwd())")
    assert result.exit_code == 0
    cwd = result.stdout.strip()
    # 临时目录应该不再存在（已清理）
    assert not os.path.exists(cwd)


@pytest.mark.asyncio
async def test_env_vars_sanitized():
    """Subprocess must not see parent's API keys."""
    import os
    os.environ["LLM_API_KEY"] = "sk-test-secret-key-for-testing"
    try:
        result = await run_python(
            "import os; print(os.environ.get('LLM_API_KEY', 'NOT_FOUND'))"
        )
        assert result.exit_code == 0
        assert "NOT_FOUND" in result.stdout
        assert "sk-test" not in result.stdout
    finally:
        del os.environ["LLM_API_KEY"]


@pytest.mark.asyncio
async def test_output_redacts_secrets():
    """If code prints a secret pattern, output should be redacted."""
    result = await run_python('print("my key is ghp_ABCDEFghijklmnopqrstuvwxyz0123456789ABCDEF")')
    assert result.exit_code == 0
    assert "ghp_" not in result.stdout
    assert "REDACTED" in result.stdout


@pytest.mark.asyncio
async def test_uses_docker_strict_sandbox():
    """run_python must use Docker STRICT tier, not run_local."""
    captured_cmd = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_cmd.extend(args)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b'hello\n', b'')
        mock_proc.returncode = 0
        return mock_proc

    with patch("src.core.execution_sandbox.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        await run_python('print("hello")')

    cmd_str = " ".join(captured_cmd)
    assert "docker" in cmd_str, "Must run via Docker"
    assert "--network none" in cmd_str or "--network\nnone" in " ".join(f"\n{a}" for a in captured_cmd), "STRICT must use --network none"
    assert "--cap-drop" in cmd_str, "Must drop all capabilities"


@pytest.mark.asyncio
async def test_script_readable_by_sandbox_user():
    """STRICT 容器内的非 root 用户必须能读取挂载的脚本。"""
    captured_workspace = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_workspace
        for arg in args:
            if isinstance(arg, str) and ":/workspace" in arg:
                captured_workspace = arg.split(":/workspace", 1)[0]
                break
        assert captured_workspace is not None

        script_path = os.path.join(captured_workspace, "script.py")
        dir_mode = stat.S_IMODE(os.stat(captured_workspace).st_mode)
        file_mode = stat.S_IMODE(os.stat(script_path).st_mode)
        assert dir_mode & 0o005 == 0o005
        assert file_mode & 0o004 == 0o004

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"hello\n", b"")
        mock_proc.returncode = 0
        return mock_proc

    with patch("src.core.execution_sandbox.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        result = await run_python('print("hello")')

    assert result.exit_code == 0
    assert captured_workspace is not None
    assert not os.path.exists(captured_workspace)


@pytest.mark.asyncio
async def test_network_blocked_in_strict():
    """Code running in STRICT sandbox must not have network access."""
    result = await run_python(
        'import socket\ntry:\n    socket.create_connection(("8.8.8.8", 53), timeout=3)\n    print("CONNECTED")\nexcept Exception as e:\n    print(f"BLOCKED: {e}")',
        timeout=10,
    )
    assert "CONNECTED" not in result.stdout
    assert "BLOCKED" in result.stdout
