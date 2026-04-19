import asyncio
import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

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


class SkillExecutor:
    """技能执行引擎。根据 maturity 路由到沙盒或主机。"""

    def __init__(self, skill_store, sandbox_image: str = "lapwing-sandbox"):
        self._store = skill_store
        self._sandbox_image = sandbox_image

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

        if maturity in _SANDBOX_MATURITIES:
            result = await self._run_in_sandbox(code, args, dependencies, timeout)
        else:
            result = await self._run_on_host(code, args, dependencies, timeout)

        self._store.record_execution(
            skill_id,
            success=result.success,
            error=result.error if not result.success else None,
        )
        return result

    async def _run_in_sandbox(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")

            runner_code = self._build_runner(arguments, dependencies)
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "-v", f"{tmp_dir}:/workspace:ro",
                "--user", "sandboxuser",
                self._sandbox_image,
                "python3", "/workspace/runner.py",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return SkillResult(
                    success=False, output="", error="沙盒执行超时", exit_code=-1, timed_out=True,
                )

            stdout = raw_out.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            stderr = raw_err.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            exit_code = proc.returncode if proc.returncode is not None else -1

            return SkillResult(
                success=(exit_code == 0),
                output=stdout,
                error=stderr,
                exit_code=exit_code,
            )

        except FileNotFoundError:
            return SkillResult(
                success=False, output="",
                error="Docker 未安装或不可用", exit_code=-1,
            )
        except Exception as e:
            logger.error("沙盒执行异常: %s", e)
            return SkillResult(
                success=False, output="", error=str(e), exit_code=-1,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def _run_on_host(
        self,
        code: str,
        arguments: dict,
        dependencies: list[str],
        timeout: int,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_host_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")

            runner_code = self._build_runner(arguments, [])
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(runner_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp_dir,
            )

            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return SkillResult(
                    success=False, output="", error="主机执行超时", exit_code=-1, timed_out=True,
                )

            stdout = raw_out.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            stderr = raw_err.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
            exit_code = proc.returncode if proc.returncode is not None else -1

            return SkillResult(
                success=(exit_code == 0),
                output=stdout,
                error=stderr,
                exit_code=exit_code,
            )
        except Exception as e:
            logger.error("主机执行异常: %s", e)
            return SkillResult(
                success=False, output="", error=str(e), exit_code=-1,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_runner(self, arguments: dict, dependencies: list[str]) -> str:
        args_json = json.dumps(arguments, ensure_ascii=False)
        dep_install = ""
        if dependencies:
            dep_install = f"""
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + {repr(dependencies)},
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
