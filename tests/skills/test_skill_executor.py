import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.execution_sandbox import SandboxTier
from src.skills.skill_executor import CapabilityExecutionContext, SkillExecutor, SkillResult
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


class TestStableUsesDockerStandard:
    async def test_stable_uses_standard_tier_docker(self, executor):
        """Stable skills must run in Docker STANDARD, not run_local."""
        from unittest.mock import patch, AsyncMock

        captured_cmd = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured_cmd.extend(args)
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b'{"ok": true}', b'')
            mock_proc.returncode = 0
            return mock_proc

        with patch("src.core.execution_sandbox.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            await executor._run_on_host(
                'def run():\n    return {"ok": True}',
                {},
                [],
                timeout=30,
            )

        cmd_str = " ".join(captured_cmd)
        assert "docker" in cmd_str, "Must run via Docker"
        assert "--network none" not in cmd_str, "STANDARD must not use --network none"
        assert "--memory" in cmd_str, "Must have memory limit"

    async def test_stable_passes_dependencies(self, executor):
        """Stable skills must pass dependencies to the runner for pip install."""
        from unittest.mock import patch, AsyncMock
        import tempfile, os

        written_runner = []

        original_run = executor._sandbox.run

        async def spy_run(*args, workspace=None, **kwargs):
            if workspace:
                runner_path = os.path.join(workspace, "runner.py")
                if os.path.exists(runner_path):
                    with open(runner_path) as f:
                        written_runner.append(f.read())
            return await original_run(*args, workspace=workspace, **kwargs)

        with patch.object(executor._sandbox, "run", side_effect=spy_run):
            await executor._run_on_host(
                'def run():\n    return {}',
                {},
                ["requests"],
                timeout=30,
            )

        assert written_runner, "Runner script should have been written"
        assert "pip" in written_runner[0], "Runner must contain pip install for dependencies"
        assert "requests" in written_runner[0]


class TestScriptsDirectoryAccess:
    async def test_skill_dir_path_available(self, executor, skill_store):
        """Executor should know where the skill directory is."""
        skill_store.create(
            "skill_dir_exec",
            "目录执行",
            "测试目录路径",
            'def run():\n    return {"ok": True}',
        )
        skill = skill_store.read("skill_dir_exec")
        assert "skill_dir_exec/SKILL.md" in skill["file_path"]


class TestExecuteDirectory:
    async def test_execute_directory_uses_existing_sandbox_copy(self, executor, tmp_path):
        cap_dir = tmp_path / "capability"
        script = cap_dir / "scripts" / "main.py"
        script.parent.mkdir(parents=True)
        script.write_text('def run(x=1):\n    return {"result": x * 2}\n', encoding="utf-8")

        captured = {}

        async def fake_run(cmd, *, tier, timeout, workspace):
            captured["cmd"] = cmd
            captured["tier"] = tier
            captured["timeout"] = timeout
            copied_entry = Path(workspace) / "capability" / "scripts" / "main.py"
            captured["copied_entry_exists"] = copied_entry.is_file()
            return SimpleNamespace(
                exit_code=0,
                stdout='{"result": 10}\n',
                stderr="",
                timed_out=False,
            )

        with patch.object(executor._sandbox, "run", side_effect=fake_run):
            result = await executor.execute_directory(
                cap_dir,
                "scripts/main.py",
                arguments={"x": 5},
                timeout=11,
                capability_context=CapabilityExecutionContext(
                    capability_id="cap_01",
                    capability_version="0.1.0",
                    capability_content_hash="abc",
                    maturity="stable",
                    side_effects=("local_write",),
                ),
            )

        assert result.success is True
        assert captured["cmd"] == ["python3", "/workspace/runner.py"]
        assert captured["tier"] == SandboxTier.STANDARD
        assert captured["timeout"] == 11
        assert captured["copied_entry_exists"] is True

    async def test_execute_directory_rejects_path_traversal(self, executor, tmp_path):
        cap_dir = tmp_path / "capability"
        cap_dir.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("def run():\n    return {}\n", encoding="utf-8")

        result = await executor.execute_directory(cap_dir, "../outside.py")

        assert result.success is False
        assert "relative path inside" in result.error

    async def test_execute_directory_rejects_dependencies_in_strict(self, executor, tmp_path):
        cap_dir = tmp_path / "capability"
        script = cap_dir / "scripts" / "main.py"
        script.parent.mkdir(parents=True)
        script.write_text("def run():\n    return {}\n", encoding="utf-8")

        result = await executor.execute_directory(
            cap_dir,
            "scripts/main.py",
            capability_context=CapabilityExecutionContext(
                capability_id="cap_01",
                capability_version="0.1.0",
                capability_content_hash="abc",
                maturity="testing",
                dependencies=("requests",),
            ),
        )

        assert result.success is False
        assert "STRICT" in result.error


class TestExecuteDirectorySideEffects:
    async def test_executable_script_none_side_effect_runs_strict(self, executor, tmp_path):
        cap_dir = tmp_path / "capability"
        script = cap_dir / "scripts" / "main.py"
        script.parent.mkdir(parents=True)
        script.write_text("def run():\n    return {}\n", encoding="utf-8")

        captured = {}

        async def fake_run(cmd, *, tier, timeout, workspace):
            captured["tier"] = tier
            return SimpleNamespace(exit_code=0, stdout="{}", stderr="", timed_out=False)

        with patch.object(executor._sandbox, "run", side_effect=fake_run):
            result = await executor.execute_directory(
                cap_dir,
                "scripts/main.py",
                capability_context=CapabilityExecutionContext(
                    capability_id="cap_01",
                    capability_version="0.1.0",
                    capability_content_hash="abc",
                    maturity="stable",
                    side_effects=("none",),
                ),
            )

        assert result.success is True
        assert captured["tier"] == SandboxTier.STRICT

    async def test_executable_script_with_side_effects_uses_standard(self, executor, tmp_path):
        cap_dir = tmp_path / "capability"
        script = cap_dir / "scripts" / "main.py"
        script.parent.mkdir(parents=True)
        script.write_text("def run():\n    return {}\n", encoding="utf-8")

        captured = {}

        async def fake_run(cmd, *, tier, timeout, workspace):
            captured["tier"] = tier
            return SimpleNamespace(exit_code=0, stdout="{}", stderr="", timed_out=False)

        with patch.object(executor._sandbox, "run", side_effect=fake_run):
            result = await executor.execute_directory(
                cap_dir,
                "scripts/main.py",
                capability_context=CapabilityExecutionContext(
                    capability_id="cap_01",
                    capability_version="0.1.0",
                    capability_content_hash="abc",
                    maturity="stable",
                    side_effects=("local_write", "network_send"),
                ),
            )

        assert result.success is True
        assert captured["tier"] == SandboxTier.STANDARD


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

        with patch("src.core.execution_sandbox.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
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

        with patch("src.core.execution_sandbox.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            await executor._run_in_sandbox('def run():\n    return {}', {}, [], timeout=30)

        for i, arg in enumerate(captured_cmd):
            if arg == "--memory":
                assert captured_cmd[i + 1] == "256m"
                break
        else:
            pytest.fail("--memory flag not found")
