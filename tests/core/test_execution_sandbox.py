import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from src.core.execution_sandbox import (
    ExecutionSandbox,
    SandboxTier,
    SandboxResult,
)


class TestSandboxTier:
    def test_strict_has_no_network(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STRICT, workspace=None)
        assert "--network" in flags
        idx = flags.index("--network")
        assert flags[idx + 1] == "none"

    def test_standard_uses_bridge(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STANDARD, workspace=None)
        idx = flags.index("--network")
        assert flags[idx + 1] == "lapwing-sandbox"

    def test_privileged_uses_host(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.PRIVILEGED, workspace=None)
        assert "--network=host" in flags

    def test_all_tiers_have_cap_drop(self):
        sb = ExecutionSandbox()
        for tier in SandboxTier:
            flags = sb._build_docker_flags(tier, workspace=None)
            assert "--cap-drop=ALL" in flags

    def test_all_tiers_have_rm(self):
        sb = ExecutionSandbox()
        for tier in SandboxTier:
            flags = sb._build_docker_flags(tier, workspace=None)
            assert "--rm" in flags

    def test_all_tiers_have_user(self):
        sb = ExecutionSandbox()
        for tier in SandboxTier:
            flags = sb._build_docker_flags(tier, workspace=None)
            assert "--user" in flags


class TestSandboxResourceLimits:
    def test_strict_memory_256m(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STRICT, workspace=None)
        idx = flags.index("--memory")
        assert flags[idx + 1] == "256m"

    def test_strict_cpus_half(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STRICT, workspace=None)
        idx = flags.index("--cpus")
        assert flags[idx + 1] == "0.5"

    def test_standard_memory_512m(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STANDARD, workspace=None)
        idx = flags.index("--memory")
        assert flags[idx + 1] == "512m"

    def test_standard_cpus_one(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STANDARD, workspace=None)
        idx = flags.index("--cpus")
        assert flags[idx + 1] == "1.0"

    def test_privileged_memory_1g(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.PRIVILEGED, workspace=None)
        idx = flags.index("--memory")
        assert flags[idx + 1] == "1024m"


class TestSandboxWorkspaceMount:
    def test_strict_mounts_readonly(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STRICT, workspace="/tmp/work")
        mount_flags = [f for f in flags if "/tmp/work" in f]
        assert any(":ro" in f for f in mount_flags)

    def test_standard_mounts_readwrite(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STANDARD, workspace="/tmp/work")
        mount_flags = [f for f in flags if "/tmp/work" in f]
        assert mount_flags
        assert not any(":ro" in f for f in mount_flags)


class TestSandboxRun:
    async def test_run_returns_result(self):
        sb = ExecutionSandbox()

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate.return_value = (b'hello\n', b'')
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await sb.run(
                ["echo", "hello"],
                tier=SandboxTier.STRICT,
                timeout=10,
            )

        assert isinstance(result, SandboxResult)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    async def test_run_timeout(self):
        sb = ExecutionSandbox()
        call_count = 0

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()

            async def communicate_side_effect():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # First call: block until cancelled by wait_for
                    await asyncio.sleep(999)
                    return (b'', b'')
                else:
                    # Second call (after kill): return immediately
                    return (b'', b'')

            proc.communicate = communicate_side_effect
            proc.kill = MagicMock()
            proc.returncode = -9
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await sb.run(
                ["sleep", "999"],
                tier=SandboxTier.STRICT,
                timeout=0.1,
            )

        assert result.timed_out is True
        assert result.exit_code == -1

    async def test_run_redacts_output_secrets(self):
        sb = ExecutionSandbox()

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate.return_value = (
                b'key=ghp_ABCDEFghijklmnopqrstuvwxyz0123456789ABCDEF\n', b''
            )
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await sb.run(
                ["cat", "secret"],
                tier=SandboxTier.STRICT,
                timeout=10,
            )

        assert "ghp_" not in result.stdout
        assert "REDACTED" in result.stdout

    async def test_run_docker_not_found(self):
        sb = ExecutionSandbox()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await sb.run(
                ["echo", "hi"],
                tier=SandboxTier.STRICT,
                timeout=10,
            )

        assert result.exit_code == -1
        assert "Docker" in result.stderr


class TestSandboxRunLocal:
    async def test_run_local_basic(self):
        sb = ExecutionSandbox()
        result = await sb.run_local(
            ["python3", "-c", "print('hello')"],
            timeout=5,
        )
        assert result.exit_code == 0
        assert "hello" in result.stdout

    async def test_run_local_sanitizes_env(self):
        import os
        os.environ["TEST_SECRET_API_KEY"] = "should-not-see"
        try:
            sb = ExecutionSandbox()
            result = await sb.run_local(
                ["python3", "-c", "import os; print(os.environ.get('TEST_SECRET_API_KEY', 'NOT_FOUND'))"],
                timeout=5,
            )
            assert "NOT_FOUND" in result.stdout
        finally:
            del os.environ["TEST_SECRET_API_KEY"]

    async def test_run_local_timeout(self):
        sb = ExecutionSandbox()
        result = await sb.run_local(
            ["python3", "-c", "import time; time.sleep(999)"],
            timeout=0.5,
        )
        assert result.timed_out is True
        assert result.exit_code == -1
