"""shell_executor 集成测试（真实子进程执行）。"""

import pytest
from unittest.mock import patch, AsyncMock

from src.tools import shell_executor


@pytest.fixture
def isolated_shell_log(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_executor, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(shell_executor, "_LOG_FILE", tmp_path / "shell_execution.log")
    return tmp_path / "shell_execution.log"


@pytest.mark.asyncio
async def test_execute_returns_real_stdout_and_default_cwd(isolated_shell_log):
    result = await shell_executor.execute("pwd")

    assert result.return_code == 0
    assert result.cwd == shell_executor._DEFAULT_CWD
    assert result.stdout.strip() == shell_executor._DEFAULT_CWD
    assert result.stderr == ""
    assert result.blocked is False
    assert isolated_shell_log.exists()


@pytest.mark.asyncio
async def test_execute_times_out(monkeypatch, isolated_shell_log):
    monkeypatch.setattr(shell_executor, "SHELL_TIMEOUT", 1)

    result = await shell_executor.execute("sleep 2")

    assert result.timed_out is True
    assert result.return_code == -1
    assert "超时" in result.reason


@pytest.mark.asyncio
async def test_execute_blocks_dangerous_command(isolated_shell_log):
    result = await shell_executor.execute("rm -rf /")

    assert result.blocked is True
    assert result.return_code == -1
    assert "删除根目录" in result.reason


@pytest.mark.asyncio
async def test_execute_blocks_protected_path_write(isolated_shell_log):
    result = await shell_executor.execute("touch /etc/lapwing-test")

    assert result.blocked is True
    assert "/etc" in result.reason


def test_workspace_owner_is_not_treated_as_other_home(monkeypatch):
    monkeypatch.setattr(shell_executor, "_WORKSPACE_OWNER", "kevin")
    assert "/home/kevin" not in shell_executor._other_home_prefixes()


@pytest.mark.asyncio
async def test_execute_blocks_interactive_command(isolated_shell_log):
    result = await shell_executor.execute("vim README.md")

    assert result.blocked is True
    assert "交互式编辑器" in result.reason


@pytest.mark.asyncio
async def test_execute_truncates_long_output(monkeypatch, isolated_shell_log):
    monkeypatch.setattr(shell_executor, "SHELL_MAX_OUTPUT_CHARS", 10)

    result = await shell_executor.execute("printf '123456789012345'")

    assert result.return_code == 0
    assert result.stdout_truncated is True
    assert result.stdout.startswith("123")
    assert "truncated" in result.stdout.lower()


@pytest.mark.asyncio
async def test_execute_blocks_sudo_by_default(monkeypatch, isolated_shell_log):
    monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", False)

    result = await shell_executor.execute("sudo apt update")

    assert result.blocked is True
    assert "sudo" in result.reason.lower()


@pytest.mark.asyncio
async def test_execute_allows_sudo_when_enabled(monkeypatch, isolated_shell_log):
    monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", True)

    # whoami doesn't need sudo but verifies the sudo block is lifted
    result = await shell_executor.execute("whoami")

    assert result.blocked is False
    assert result.return_code == 0


@pytest.mark.asyncio
async def test_execute_still_blocks_dangerous_sudo_commands(monkeypatch, isolated_shell_log):
    # 即使启用了 sudo，仍然拦截本身危险的命令
    monkeypatch.setattr(shell_executor, "SHELL_ALLOW_SUDO", True)

    result = await shell_executor.execute("sudo rm -rf /")

    assert result.blocked is True
    assert "删除根目录" in result.reason


@pytest.mark.asyncio
async def test_docker_backend_blocks_dangerous_command(monkeypatch, isolated_shell_log):
    """Docker backend must still block dangerous commands (fork bomb, rm -rf /)."""
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "docker")

    result = await shell_executor.execute("rm -rf /")

    assert result.blocked is True
    assert "删除根目录" in result.reason


@pytest.mark.asyncio
async def test_docker_backend_blocks_interactive_command(monkeypatch, isolated_shell_log):
    """Docker backend must still block interactive commands."""
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "docker")

    result = await shell_executor.execute("vim README.md")

    assert result.blocked is True
    assert "交互式编辑器" in result.reason


@pytest.mark.asyncio
async def test_docker_backend_uses_bridge_network(monkeypatch, isolated_shell_log):
    """Docker backend must NOT use --network=host."""
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "docker")

    captured_cmd = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_cmd.extend(args)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b'output', b'')
        mock_proc.returncode = 0
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        await shell_executor.execute("echo hello")

    cmd_str = " ".join(captured_cmd)
    assert "--network=host" not in cmd_str, "Must not use --network=host"
    assert "--network" in cmd_str, "Must specify a network"


@pytest.mark.asyncio
async def test_local_backend_sanitizes_env(monkeypatch, isolated_shell_log):
    """Local backend must sanitize env vars (no API keys leak)."""
    import os
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "local")
    monkeypatch.setenv("FAKE_API_KEY", "secret-test-value")

    result = await shell_executor.execute("env")

    assert result.return_code == 0
    assert "FAKE_API_KEY" not in result.stdout
    assert "secret-test-value" not in result.stdout


@pytest.mark.asyncio
async def test_local_backend_redacts_secrets_in_output(monkeypatch, isolated_shell_log):
    """Local backend must redact secret patterns in output."""
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "local")

    result = await shell_executor.execute('echo "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"')

    assert result.return_code == 0
    assert "ghp_" not in result.stdout
    assert "REDACTED" in result.stdout
