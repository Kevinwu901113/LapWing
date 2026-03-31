"""Coder Agent — 代码生成、工作区补丁和多轮修复。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from config.settings import ROOT_DIR
from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.task_runtime import TaskLoopStep, TaskRuntime
from src.core import verifier
from src.core.prompt_loader import load_prompt
from src.tools import code_runner, file_editor
from src.tools.code_runner import CodeResult
from src.tools.types import ToolExecutionRequest

logger = logging.getLogger("lapwing.agents.coder")

_MAX_CODE_LINES = 80
_MAX_FIX_ATTEMPTS = 3


class CoderAgent(BaseAgent):
    """编写、运行、修复代码；支持 snippet 和 workspace_patch 两种模式。"""

    name = "coder"
    description = "编写和运行 Python 代码，帮助解决编程问题"
    capabilities = ["生成 Python 代码", "运行代码并返回结果", "调试代码错误", "修改项目文件"]

    def __init__(self, memory, runtime: TaskRuntime | None = None) -> None:
        self._memory = memory
        self._runtime = runtime

    async def execute(self, task: AgentTask, router) -> AgentResult:
        mode = task.mode if task.mode in {"snippet", "workspace_patch"} else "snippet"
        if mode == "workspace_patch":
            return await self._execute_workspace_patch(task, router)
        return await self._execute_snippet(task, router)

    async def _execute_snippet(self, task: AgentTask, router) -> AgentResult:
        code = await self._generate_code(task.chat_id, task.user_message, router)
        if code is None:
            return AgentResult(
                content="代码生成失败，请重新描述你的需求。",
                needs_persona_formatting=True,
                metadata={
                    "exit_code": -1,
                    "timed_out": False,
                    "stdout": "",
                    "stderr": "代码生成失败",
                    "mode": "snippet",
                    "attempts": 0,
                    "changed_files": [],
                    "verification": {"passed": False, "status": "failed", "reason": "代码生成失败"},
                    "rolled_back": False,
                },
            )

        attempts = 0
        result: CodeResult | None = None
        verify_result = verifier.VerificationResult(
            passed=False,
            status="failed",
            reason="未执行",
        )

        if self._runtime is not None:
            async def _snippet_step(round_index: int) -> TaskLoopStep:
                nonlocal attempts, result, verify_result, code
                attempt = round_index + 1
                attempts = attempt
                result, verify_result = await self._run_snippet_once(code)
                if verify_result.passed:
                    return TaskLoopStep(completed=True)

                if result.timed_out or attempt == _MAX_FIX_ATTEMPTS:
                    return TaskLoopStep(stop=True)

                logger.info("[coder] snippet 执行失败，开始第 %s 次修复", attempt)
                fixed_code = await self._fix_code(
                    chat_id=task.chat_id,
                    code=code,
                    error=verify_result.reason or result.stderr,
                    router=router,
                )
                if fixed_code is None or fixed_code == code:
                    return TaskLoopStep(stop=True)
                code = fixed_code
                return TaskLoopStep()

            await self._runtime.run_task_loop(
                max_rounds=_MAX_FIX_ATTEMPTS,
                step_runner=_snippet_step,
            )
        else:
            for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                attempts = attempt
                result = await code_runner.run_python(code)
                verify_result = verifier.verify_code_result(result)
                if verify_result.passed:
                    break

                if result.timed_out or attempt == _MAX_FIX_ATTEMPTS:
                    break

                logger.info("[coder] snippet 执行失败，开始第 %s 次修复", attempt)
                fixed_code = await self._fix_code(
                    chat_id=task.chat_id,
                    code=code,
                    error=verify_result.reason or result.stderr,
                    router=router,
                )
                if fixed_code is None or fixed_code == code:
                    break
                code = fixed_code

        assert result is not None
        return AgentResult(
            content=self._format_snippet_reply(code, result, verify_result),
            needs_persona_formatting=False,
            metadata={
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "mode": "snippet",
                "attempts": attempts,
                "changed_files": [],
                "verification": {
                    "passed": verify_result.passed,
                    "status": verify_result.status,
                    "reason": verify_result.reason,
                    "checks": verify_result.checks,
                    "artifacts": verify_result.artifacts,
                },
                "rolled_back": False,
            },
        )

    async def _execute_workspace_patch(self, task: AgentTask, router) -> AgentResult:
        plan = await self._plan_workspace(task.chat_id, task.user_message, router)
        if plan is None:
            return AgentResult(
                content="我没有生成出可执行的多文件修改计划，请补充更具体的目标文件和修改内容。",
                needs_persona_formatting=True,
                metadata={
                    "mode": "workspace_patch",
                    "attempts": 0,
                    "changed_files": [],
                    "verification": {"passed": False, "status": "failed", "reason": "计划生成失败"},
                    "rolled_back": False,
                },
            )

        attempts = 0
        changed_files: list[str] = []
        rolled_back = False
        verify_result = verifier.VerificationResult(
            passed=False,
            status="failed",
            reason="未执行",
        )
        tx_result = None
        tx_payload: dict[str, Any] | None = None

        if self._runtime is not None:
            async def _workspace_step(round_index: int) -> TaskLoopStep:
                nonlocal attempts, tx_payload, changed_files, rolled_back, verify_result, plan
                attempt = round_index + 1
                attempts = attempt

                tx_payload = await self._apply_workspace_patch_runtime(plan["operations"])
                rolled_back = rolled_back or bool(tx_payload.get("rolled_back", False))
                changed_files = [str(item) for item in tx_payload.get("changed_files", [])]

                if not bool(tx_payload.get("success", False)):
                    failure_reason = str(tx_payload.get("reason", "")).strip() or "编辑事务失败。"
                    if attempt == _MAX_FIX_ATTEMPTS:
                        verify_result = verifier.VerificationResult(
                            passed=False,
                            status="failed",
                            reason=failure_reason,
                        )
                        return TaskLoopStep(stop=True)
                    next_plan = await self._fix_workspace_plan(
                        chat_id=task.chat_id,
                        user_message=task.user_message,
                        previous_plan=plan,
                        failure_reason=failure_reason,
                        router=router,
                    )
                    if next_plan is None:
                        verify_result = verifier.VerificationResult(
                            passed=False,
                            status="failed",
                            reason=failure_reason,
                        )
                        return TaskLoopStep(stop=True)
                    plan = next_plan
                    return TaskLoopStep()

                verify_result = await self._verify_workspace_runtime(
                    changed_files=changed_files,
                    pytest_targets=plan.get("pytest_targets") or None,
                )
                if verify_result.passed:
                    return TaskLoopStep(completed=True)

                if attempt == _MAX_FIX_ATTEMPTS:
                    return TaskLoopStep(stop=True)

                next_plan = await self._fix_workspace_plan(
                    chat_id=task.chat_id,
                    user_message=task.user_message,
                    previous_plan=plan,
                    failure_reason=verify_result.reason or "验证失败",
                    router=router,
                )
                if next_plan is None:
                    return TaskLoopStep(stop=True)
                plan = next_plan
                return TaskLoopStep()

            await self._runtime.run_task_loop(
                max_rounds=_MAX_FIX_ATTEMPTS,
                step_runner=_workspace_step,
            )
        else:
            for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                attempts = attempt
                tx_result = file_editor.transactional_apply(
                    plan["operations"],
                    root_dir=ROOT_DIR,
                )
                rolled_back = rolled_back or tx_result.rolled_back
                changed_files = tx_result.changed_files

                if not tx_result.success:
                    failure_reason = tx_result.reason or "编辑事务失败。"
                    if attempt == _MAX_FIX_ATTEMPTS:
                        verify_result = verifier.VerificationResult(
                            passed=False,
                            status="failed",
                            reason=failure_reason,
                        )
                        break
                next_plan = await self._fix_workspace_plan(
                    chat_id=task.chat_id,
                    user_message=task.user_message,
                    previous_plan=plan,
                    failure_reason=failure_reason,
                    router=router,
                )
                if next_plan is None:
                    verify_result = verifier.VerificationResult(
                        passed=False,
                        status="failed",
                        reason=failure_reason,
                    )
                    break
                plan = next_plan
                continue

                verify_result = await verifier.verify_workspace(
                    changed_files=changed_files,
                    root_dir=ROOT_DIR,
                    pytest_targets=plan.get("pytest_targets") or None,
                )
                if verify_result.passed:
                    break

                if attempt == _MAX_FIX_ATTEMPTS:
                    break

                next_plan = await self._fix_workspace_plan(
                    chat_id=task.chat_id,
                    user_message=task.user_message,
                    previous_plan=plan,
                    failure_reason=verify_result.reason or "验证失败",
                    router=router,
                )
                if next_plan is None:
                    break
                plan = next_plan

        success = bool((tx_result and tx_result.success) or (tx_payload and tx_payload.get("success"))) and verify_result.passed
        summary = str(plan.get("summary", "")).strip() if isinstance(plan, dict) else ""
        changed_rel = [
            str(Path(path).resolve().relative_to(ROOT_DIR.resolve()))
            if Path(path).resolve().is_absolute() and ROOT_DIR.resolve() in Path(path).resolve().parents
            else str(path)
            for path in changed_files
        ]

        if success:
            content = self._format_workspace_success(summary, changed_rel, verify_result)
        else:
            content = self._format_workspace_failure(summary, changed_rel, verify_result, rolled_back)

        return AgentResult(
            content=content,
            needs_persona_formatting=False,
            metadata={
                "mode": "workspace_patch",
                "attempts": attempts,
                "changed_files": changed_files,
                "verification": {
                    "passed": verify_result.passed,
                    "status": verify_result.status,
                    "reason": verify_result.reason,
                    "checks": verify_result.checks,
                    "artifacts": verify_result.artifacts,
                },
                "rolled_back": rolled_back,
            },
        )

    async def _run_snippet_once(
        self,
        code: str,
    ) -> tuple[CodeResult, verifier.VerificationResult]:
        if self._runtime is None:
            result = await code_runner.run_python(code)
            return result, verifier.verify_code_result(result)

        run_exec = await self._runtime.execute_tool(
            request=ToolExecutionRequest(
                name="run_python_code",
                arguments={"code": code},
            ),
            profile="coder_snippet",
            workspace_root=str(ROOT_DIR),
        )
        payload = run_exec.payload
        exit_code_raw = payload.get("exit_code", -1)
        try:
            exit_code = int(exit_code_raw)
        except (TypeError, ValueError):
            exit_code = -1
        result = CodeResult(
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            exit_code=exit_code,
            timed_out=bool(payload.get("timed_out", False)),
        )
        verify_exec = await self._runtime.execute_tool(
            request=ToolExecutionRequest(
                name="verify_code_result",
                arguments={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                },
            ),
            profile="coder_snippet",
            workspace_root=str(ROOT_DIR),
        )
        verify_result = self._verification_from_payload(verify_exec.payload)
        return result, verify_result

    async def _apply_workspace_patch_runtime(
        self,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        assert self._runtime is not None
        execution = await self._runtime.execute_tool(
            request=ToolExecutionRequest(
                name="apply_workspace_patch",
                arguments={"operations": operations},
            ),
            profile="coder_workspace",
            workspace_root=str(ROOT_DIR),
        )
        return execution.payload

    async def _verify_workspace_runtime(
        self,
        *,
        changed_files: list[str],
        pytest_targets: list[str] | None,
    ) -> verifier.VerificationResult:
        assert self._runtime is not None
        execution = await self._runtime.execute_tool(
            request=ToolExecutionRequest(
                name="verify_workspace",
                arguments={
                    "changed_files": changed_files,
                    "pytest_targets": pytest_targets or [],
                },
            ),
            profile="coder_workspace",
            workspace_root=str(ROOT_DIR),
        )
        return self._verification_from_payload(execution.payload)

    def _verification_from_payload(self, payload: dict[str, Any]) -> verifier.VerificationResult:
        return verifier.VerificationResult(
            passed=bool(payload.get("passed", False)),
            status=str(payload.get("status", "failed")),
            reason=str(payload.get("reason", "")),
            checks=list(payload.get("checks") or []),
            artifacts=[str(item) for item in (payload.get("artifacts") or [])],
        )

    async def _generate_code(self, chat_id: str, user_message: str, router) -> str | None:
        prompt = load_prompt("coder_generate").replace("{user_message}", user_message)
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=1024,
                session_key=f"chat:{chat_id}",
                origin="agent.coder.generate_code",
            )
            return _extract_code(raw)
        except Exception as exc:
            logger.warning(f"[coder] 代码生成出错: {exc}")
            return None

    async def _fix_code(self, *, chat_id: str, code: str, error: str, router) -> str | None:
        prompt = (
            load_prompt("coder_fix")
            .replace("{code}", code)
            .replace("{error}", error[:1000])
        )
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=1024,
                session_key=f"chat:{chat_id}",
                origin="agent.coder.fix_code",
            )
            return _extract_code(raw)
        except Exception as exc:
            logger.warning(f"[coder] 代码修复出错: {exc}")
            return None

    async def _plan_workspace(self, chat_id: str, user_message: str, router) -> dict[str, Any] | None:
        prompt = load_prompt("coder_workspace_plan").replace("{user_message}", user_message)
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=1400,
                session_key=f"chat:{chat_id}",
                origin="agent.coder.plan_workspace",
            )
        except Exception as exc:
            logger.warning(f"[coder] workspace 计划生成失败: {exc}")
            return None
        return _parse_workspace_plan(raw)

    async def _fix_workspace_plan(
        self,
        *,
        chat_id: str,
        user_message: str,
        previous_plan: dict[str, Any],
        failure_reason: str,
        router,
    ) -> dict[str, Any] | None:
        prompt = (
            load_prompt("coder_workspace_fix")
            .replace("{user_message}", user_message)
            .replace("{previous_plan}", json.dumps(previous_plan, ensure_ascii=False))
            .replace("{failure_reason}", failure_reason[:1500])
        )
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=1400,
                session_key=f"chat:{chat_id}",
                origin="agent.coder.fix_workspace_plan",
            )
        except Exception as exc:
            logger.warning(f"[coder] workspace 修复计划生成失败: {exc}")
            return None
        return _parse_workspace_plan(raw)

    def _format_snippet_reply(
        self,
        code: str,
        result: CodeResult,
        verification: verifier.VerificationResult,
    ) -> str:
        parts: list[str] = []

        code_lines = code.splitlines()
        if len(code_lines) > _MAX_CODE_LINES:
            displayed = "\n".join(code_lines[:_MAX_CODE_LINES])
            parts.append(f"```python\n{displayed}\n# ... (已截断)\n```")
        else:
            parts.append(f"```python\n{code}\n```")

        if result.timed_out:
            parts.append("执行超时（超过 10 秒），已中止。")
        elif result.exit_code == 0:
            if result.stdout.strip():
                parts.append(f"**执行结果：**\n```\n{result.stdout.strip()}\n```")
            else:
                parts.append("**执行结果：** （无输出）")
        else:
            error_text = verification.reason or result.stderr.strip()
            parts.append(f"**执行出错（exit code {result.exit_code}）：**\n```\n{error_text}\n```")

        return "\n\n".join(parts)

    def _format_workspace_success(
        self,
        summary: str,
        changed_files: list[str],
        verification: verifier.VerificationResult,
    ) -> str:
        lines = ["已完成 workspace 多文件修改。"]
        if summary:
            lines.append(f"计划摘要：{summary}")
        if changed_files:
            lines.append("改动文件：")
            lines.extend(f"- `{path}`" for path in changed_files)
        if verification.checks:
            lines.append("验证：通过")
        return "\n".join(lines)

    def _format_workspace_failure(
        self,
        summary: str,
        changed_files: list[str],
        verification: verifier.VerificationResult,
        rolled_back: bool,
    ) -> str:
        lines = ["workspace 修改未完全通过验证。"]
        if summary:
            lines.append(f"最后计划摘要：{summary}")
        if changed_files:
            lines.append("涉及文件：")
            lines.extend(f"- `{path}`" for path in changed_files)
        if verification.reason:
            lines.append(f"失败原因：{verification.reason}")
        lines.append("回滚状态：" + ("已发生回滚" if rolled_back else "未回滚"))
        return "\n".join(lines)


def _extract_code(text: str) -> str | None:
    match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    if stripped.startswith("def ") or stripped.startswith("import ") or stripped.startswith("print("):
        return stripped
    return None


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _parse_workspace_plan(raw: str) -> dict[str, Any] | None:
    data = _extract_json(raw)
    if data is None:
        return None

    operations = data.get("operations")
    if not isinstance(operations, list) or not operations:
        return None
    normalized_ops: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            return None
        op_name = str(operation.get("op", "")).strip()
        path = str(operation.get("path", "")).strip()
        if not op_name or not path:
            return None
        normalized_ops.append(operation)

    pytest_targets = data.get("pytest_targets")
    if isinstance(pytest_targets, list):
        normalized_targets = [str(item) for item in pytest_targets if str(item).strip()]
    else:
        normalized_targets = []

    return {
        "summary": str(data.get("summary", "")).strip(),
        "reason": str(data.get("reason", "")).strip(),
        "operations": normalized_ops,
        "pytest_targets": normalized_targets,
    }
