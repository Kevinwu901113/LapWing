import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.skills.skill_executor import SkillExecutor, SkillResult
from src.skills.skill_store import SkillStore


@pytest.fixture
def skill_store(tmp_path):
    return SkillStore(skills_dir=tmp_path / "skills")


@pytest.fixture
def executor(skill_store):
    return SkillExecutor(skill_store=skill_store, sandbox_image="lapwing-sandbox")


class TestSkillResult:
    def test_success_result(self):
        r = SkillResult(success=True, output='{"x": 1}', error="", exit_code=0)
        assert r.success is True
        assert r.timed_out is False

    def test_timeout_result(self):
        r = SkillResult(success=False, output="", error="", exit_code=-1, timed_out=True)
        assert r.timed_out is True


class TestExecuteRouting:
    async def test_nonexistent_skill_fails(self, executor):
        result = await executor.execute("skill_nope")
        assert result.success is False
        assert "不存在" in result.error

    async def test_draft_routes_to_sandbox(self, executor, skill_store):
        skill_store.create(
            "skill_sandbox",
            "沙盒测试",
            "测试沙盒路由",
            'def run():\n    return {"ok": True}',
        )
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=True, output='{"ok": true}', error="", exit_code=0,
            )
            result = await executor.execute("skill_sandbox")
            mock_sb.assert_called_once()
            assert result.success is True

    async def test_stable_routes_to_host(self, executor, skill_store):
        skill_store.create(
            "skill_host",
            "主机测试",
            "测试主机路由",
            'def run():\n    return {"ok": True}',
        )
        skill_store.update_meta("skill_host", maturity="stable")
        with patch.object(executor, "_run_on_host", new_callable=AsyncMock) as mock_host:
            mock_host.return_value = SkillResult(
                success=True, output='{"ok": true}', error="", exit_code=0,
            )
            result = await executor.execute("skill_host")
            mock_host.assert_called_once()
            assert result.success is True

    async def test_broken_routes_to_sandbox(self, executor, skill_store):
        skill_store.create(
            "skill_broken",
            "损坏测试",
            "测试损坏路由",
            'def run():\n    return {}',
        )
        skill_store.update_meta("skill_broken", maturity="broken")
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=True, output='{}', error="", exit_code=0,
            )
            await executor.execute("skill_broken")
            mock_sb.assert_called_once()


class TestRecordExecution:
    async def test_success_records_to_store(self, executor, skill_store):
        skill_store.create(
            "skill_rec",
            "记录测试",
            "测试执行记录",
            'def run():\n    return {}',
        )
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=True, output='{}', error="", exit_code=0,
            )
            await executor.execute("skill_rec")
        skill = skill_store.read("skill_rec")
        assert skill["meta"]["usage_count"] == 1
        assert skill["meta"]["success_count"] == 1

    async def test_failure_records_error(self, executor, skill_store):
        skill_store.create(
            "skill_err",
            "错误测试",
            "测试错误记录",
            'def run():\n    raise ValueError("boom")',
        )
        with patch.object(executor, "_run_in_sandbox", new_callable=AsyncMock) as mock_sb:
            mock_sb.return_value = SkillResult(
                success=False, output="", error="ValueError: boom", exit_code=1,
            )
            await executor.execute("skill_err")
        skill = skill_store.read("skill_err")
        assert skill["meta"]["usage_count"] == 1
        assert skill["meta"]["success_count"] == 0
        assert "boom" in skill["meta"]["last_error"]


class TestRunOnHost:
    async def test_host_executes_code(self, executor, skill_store):
        skill_store.create(
            "skill_host_real",
            "主机真实测试",
            "在主机上真实执行",
            'def run(x=1):\n    return {"result": x * 2}',
        )
        skill_store.update_meta("skill_host_real", maturity="stable")
        result = await executor._run_on_host(
            'def run(x=1):\n    return {"result": x * 2}',
            {"x": 5},
            [],
            timeout=10,
        )
        assert result.success is True
        assert '"result": 10' in result.output

    async def test_host_timeout(self, executor):
        result = await executor._run_on_host(
            'import time\ndef run():\n    time.sleep(100)\n    return {}',
            {},
            [],
            timeout=1,
        )
        assert result.success is False
        assert result.timed_out is True


class TestSandboxDockerFlags:
    async def test_sandbox_docker_command_includes_resource_limits(self, executor):
        """Verify the Docker command includes --memory, --cpus, --cap-drop."""
        from unittest.mock import patch, AsyncMock

        captured_cmd = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured_cmd.extend(args)
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b'{"ok": true}', b'')
            mock_proc.returncode = 0
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            await executor._run_in_sandbox(
                'def run():\n    return {"ok": True}',
                {},
                [],
                timeout=30,
            )

        cmd_str = " ".join(captured_cmd)
        assert "--memory" in cmd_str, "Missing --memory flag"
        assert "--cpus" in cmd_str, "Missing --cpus flag"
        assert "--cap-drop" in cmd_str, "Missing --cap-drop flag"

    async def test_sandbox_docker_command_memory_256m(self, executor):
        """Verify memory limit is 256m for sandbox mode."""
        from unittest.mock import patch, AsyncMock

        captured_cmd = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured_cmd.extend(args)
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b'{}', b'')
            mock_proc.returncode = 0
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            await executor._run_in_sandbox('def run():\n    return {}', {}, [], timeout=30)

        for i, arg in enumerate(captured_cmd):
            if arg == "--memory":
                assert captured_cmd[i + 1] == "256m"
                break
        else:
            pytest.fail("--memory flag not found")
