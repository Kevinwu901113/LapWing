"""shell_executor 集成测试（真实子进程执行）。"""

import pytest

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
    assert result.stdout == "1234567890"
    assert result.stdout_truncated is True


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
