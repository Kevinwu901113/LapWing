import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.execution_sandbox import ExecutionSandbox, SandboxTier

logger = logging.getLogger("lapwing.skills.skill_executor")

_MAX_OUTPUT = 4000
_SANDBOX_MATURITIES = frozenset({"draft", "testing", "broken"})


@dataclass
class SkillResult:
    success: bool
    output: str
    error: str
    exit_code: int
    timed_out: bool = False


@dataclass(frozen=True)
class CapabilityExecutionContext:
    capability_id: str
    capability_version: str
    capability_content_hash: str
    maturity: str = "draft"
    dependencies: tuple[str, ...] = ()


class SkillExecutor:
    """技能执行引擎。根据 maturity 路由到沙盒或主机。"""

    def __init__(self, skill_store, sandbox_image: str = "lapwing-sandbox"):
        self._store = skill_store
        self._sandbox_image = sandbox_image
        self._sandbox = ExecutionSandbox(docker_image=sandbox_image)

    async def execute(
        self,
        skill_id: str,
        arguments: dict | None = None,
        timeout: int = 30,
    ) -> SkillResult:
        skill = self._store.read(skill_id)
        if skill is None:
            return SkillResult(
                success=False, output="", error=f"技能 {skill_id} 不存在", exit_code=-1,
            )

        meta = skill["meta"]
        code = skill["code"]
        maturity = meta.get("maturity", "draft")
        dependencies = meta.get("dependencies") or []
        args = arguments or {}

        skill_dir = self._store.skills_dir / skill_id

        if maturity in _SANDBOX_MATURITIES and dependencies:
            return SkillResult(
                success=False, output="",
                error=(
                    f"技能 '{skill_id}' 声明了外部依赖 {dependencies}，"
                    f"但 STRICT 沙箱禁止网络和写入。请改用 STANDARD 沙箱或预装依赖。"
                ),
                exit_code=-1,
            )

        if maturity in _SANDBOX_MATURITIES:
            result = await self._run_in_sandbox(code, args, dependencies, timeout, skill_dir=skill_dir)
        else:
            result = await self._run_on_host(code, args, dependencies, timeout, skill_dir=skill_dir)

        self._store.record_execution(
            skill_id,
            success=result.success,
            error=result.error if not result.success else None,
        )
        return result

    async def execute_directory(
        self,
        directory: Path,
        entry_script: str,
        arguments: dict | None = None,
        timeout: int | None = None,
        capability_context: CapabilityExecutionContext | None = None,
    ) -> SkillResult:
        directory = Path(directory).resolve()
        entry = _resolve_entry_script(directory, entry_script)
        if entry is None:
            return SkillResult(
                success=False,
                output="",
                error="entry_script must be a relative path inside the capability directory",
                exit_code=-1,
            )

        ctx = capability_context or CapabilityExecutionContext(
            capability_id=directory.name,
            capability_version="",
            capability_content_hash="",
        )
        maturity = ctx.maturity or "draft"
        dependencies = list(ctx.dependencies)
        if maturity in _SANDBOX_MATURITIES and dependencies:
            return SkillResult(
                success=False,
                output="",
                error=(
                    f"能力 '{ctx.capability_id}' 声明了外部依赖 {dependencies}，"
                    "但 STRICT 沙箱禁止网络和写入。请改用 STANDARD 沙箱或预装依赖。"
                ),
                exit_code=-1,
            )

        return await self._run_directory(
            directory=directory,
            entry_script=entry.relative_to(directory).as_posix(),
            arguments=arguments or {},
            dependencies=dependencies,
            timeout=timeout or 30,
            tier=SandboxTier.STRICT if maturity in _SANDBOX_MATURITIES else SandboxTier.STANDARD,
        )

    async def _run_in_sandbox(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
        skill_dir: Path | None = None,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_code = self._build_runner(arguments, dependencies)
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            if skill_dir and (skill_dir / "scripts").is_dir():
                scripts_src = skill_dir / "scripts"
                scripts_dst = Path(tmp_dir) / "scripts"
                shutil.copytree(scripts_src, scripts_dst)

            result = await self._sandbox.run(
                ["python3", "/workspace/runner.py"],
                tier=SandboxTier.STRICT,
                timeout=timeout,
                workspace=tmp_dir,
            )
            return SkillResult(
                success=(result.exit_code == 0),
                output=result.stdout,
                error=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as e:
            logger.error("沙盒执行异常: %s", e)
            return SkillResult(success=False, output="", error=str(e), exit_code=-1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def _run_on_host(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
        skill_dir: Path | None = None,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_host_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_code = self._build_runner(arguments, dependencies)
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            if skill_dir and (skill_dir / "scripts").is_dir():
                scripts_src = skill_dir / "scripts"
                scripts_dst = Path(tmp_dir) / "scripts"
                shutil.copytree(scripts_src, scripts_dst)

            result = await self._sandbox.run(
                ["python3", "/workspace/runner.py"],
                tier=SandboxTier.STANDARD,
                timeout=timeout,
                workspace=tmp_dir,
            )
            return SkillResult(
                success=(result.exit_code == 0),
                output=result.stdout,
                error=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as e:
            logger.error("STANDARD 沙盒执行异常: %s", e)
            return SkillResult(success=False, output="", error=str(e), exit_code=-1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_runner(self, arguments: dict, dependencies: list[str]) -> str:
        args_json = json.dumps(arguments, ensure_ascii=False)
        dep_install = ""
        if dependencies:
            dep_install = f"""
import subprocess, sys
_r = subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + {repr(dependencies)},
                    capture_output=True, text=True)
if _r.returncode != 0:
    print("依赖安装失败: " + _r.stderr[:500], file=sys.stderr)
    sys.exit(1)
"""
        return f'''import json
import sys
import importlib.util
from pathlib import Path
{dep_install}
def main():
    args = json.loads({repr(args_json)})
    skill_path = str(Path(__file__).parent / "skill.py")
    spec = importlib.util.spec_from_file_location("skill", skill_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.run(**args)
    print(json.dumps(result, ensure_ascii=False, default=str))

if __name__ == "__main__":
    main()
'''

    async def _run_directory(
        self,
        *,
        directory: Path,
        entry_script: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
        tier: SandboxTier,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_capability_")
        try:
            cap_dst = Path(tmp_dir) / "capability"
            ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
            shutil.copytree(directory, cap_dst, ignore=ignore)
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(
                self._build_directory_runner(entry_script, arguments, dependencies),
                encoding="utf-8",
            )
            result = await self._sandbox.run(
                ["python3", "/workspace/runner.py"],
                tier=tier,
                timeout=timeout,
                workspace=tmp_dir,
            )
            return SkillResult(
                success=(result.exit_code == 0),
                output=result.stdout,
                error=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as e:
            logger.error("目录能力执行异常: %s", e)
            return SkillResult(success=False, output="", error=str(e), exit_code=-1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_directory_runner(
        self,
        entry_script: str,
        arguments: dict,
        dependencies: list[str],
    ) -> str:
        args_json = json.dumps(arguments, ensure_ascii=False)
        dep_install = ""
        if dependencies:
            dep_install = f"""
import subprocess, sys
_r = subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + {repr(dependencies)},
                    capture_output=True, text=True)
if _r.returncode != 0:
    print("依赖安装失败: " + _r.stderr[:500], file=sys.stderr)
    sys.exit(1)
"""
        return f'''import json
import os
import subprocess
import sys
import importlib.util
from pathlib import Path
{dep_install}
def main():
    args = json.loads({repr(args_json)})
    entry = Path("/workspace/capability") / {entry_script!r}
    if entry.suffix == ".py":
        spec = importlib.util.spec_from_file_location("capability_entry", str(entry))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.run(**args)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return
    if entry.suffix == ".sh":
        env = dict(os.environ)
        env["LAPWING_CAPABILITY_ARGS"] = json.dumps(args, ensure_ascii=False)
        proc = subprocess.run(["sh", str(entry)], capture_output=True, text=True, env=env)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit(proc.returncode)
    print("unsupported entry_script type: " + entry.suffix, file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
'''


def _resolve_entry_script(directory: Path, entry_script: str) -> Path | None:
    if not entry_script or Path(entry_script).is_absolute():
        return None
    candidate = (directory / entry_script).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    if candidate.suffix not in {".py", ".sh"}:
        return None
    return candidate
