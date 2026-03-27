"""CoderAgent 单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.base import AgentTask
from src.agents.coder import CoderAgent, _extract_code
from src.core.verifier import VerificationResult
from src.tools.file_editor import FileEditResult, TransactionResult
from src.tools.code_runner import CodeResult
from config.settings import ROOT_DIR


# ---- 辅助 ----

def make_task(user_message: str = "写一个计算阶乘的函数") -> AgentTask:
    return AgentTask(
        chat_id="42",
        user_message=user_message,
        history=[],
        user_facts=[],
    )


def make_workspace_task(user_message: str = "帮我在仓库里做多文件修改") -> AgentTask:
    return AgentTask(
        chat_id="42",
        user_message=user_message,
        history=[],
        user_facts=[],
        mode="workspace_patch",
    )


def make_router(side_effects: list) -> MagicMock:
    router = MagicMock()
    router.complete = AsyncMock(side_effect=side_effects)
    return router


def make_memory() -> MagicMock:
    return MagicMock()


OK_CODE_RESPONSE = "```python\nprint('hello')\n```"
OK_CODE = "print('hello')"
OK_RESULT = CodeResult(stdout="hello\n", stderr="", exit_code=0)
ERR_RESULT = CodeResult(stdout="", stderr="NameError: name 'x' is not defined", exit_code=1)
FIXED_CODE_RESPONSE = "```python\nx = 1\nprint(x)\n```"
FIXED_CODE = "x = 1\nprint(x)"
FIXED_RESULT = CodeResult(stdout="1\n", stderr="", exit_code=0)
FIXED_CODE_RESPONSE_2 = "```python\nx = 2\nprint(x)\n```"
WORKSPACE_PLAN_RESPONSE = (
    '{"summary":"更新 README 和配置","operations":[{"op":"append_to_file",'
    '"path":"README.md","content":"\\n- updated by coder"}],'
    '"pytest_targets":["tests/test_demo.py"],"reason":"补充说明"}'
)


# ---- 测试：_extract_code ----

class TestExtractCode:
    def test_extracts_python_block(self):
        assert _extract_code("```python\nprint(1)\n```") == "print(1)"

    def test_extracts_generic_block(self):
        assert _extract_code("```\nprint(1)\n```") == "print(1)"

    def test_extracts_bare_print(self):
        assert _extract_code("print('hello')") == "print('hello')"

    def test_extracts_bare_import(self):
        assert _extract_code("import os\nprint(os.getcwd())") == "import os\nprint(os.getcwd())"

    def test_returns_none_for_plain_text(self):
        assert _extract_code("这是一段普通文字") is None

    def test_returns_none_for_empty(self):
        assert _extract_code("") is None

    def test_strips_whitespace_inside_block(self):
        assert _extract_code("```python\n  print(1)  \n```") == "print(1)"


# ---- 测试：execute 主流程 ----

class TestExecute:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """生成代码 → 执行成功 → 返回格式化结果。"""
        router = make_router([OK_CODE_RESPONSE])
        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message}"), \
             patch("src.agents.coder.code_runner.run_python", return_value=OK_RESULT):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert "```python" in result.content
        assert "hello" in result.content
        assert result.needs_persona_formatting is False
        assert result.metadata["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_auto_fix_on_error(self):
        """执行失败时自动调用修复，修复后成功则返回修复代码的结果。"""
        router = make_router([OK_CODE_RESPONSE, FIXED_CODE_RESPONSE])

        async def mock_run(code, **kwargs):
            if code == OK_CODE:
                return ERR_RESULT
            return FIXED_RESULT

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message} {code} {error}"), \
             patch("src.agents.coder.code_runner.run_python", side_effect=mock_run):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert result.metadata["exit_code"] == 0
        assert "1" in result.content  # FIXED_RESULT 的 stdout

    @pytest.mark.asyncio
    async def test_returns_error_when_fix_also_fails(self):
        """自动修复后仍失败，返回错误信息而不是崩溃。"""
        router = make_router([OK_CODE_RESPONSE, FIXED_CODE_RESPONSE])

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message} {code} {error}"), \
             patch("src.agents.coder.code_runner.run_python", return_value=ERR_RESULT):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert result.metadata["exit_code"] != 0
        assert "出错" in result.content

    @pytest.mark.asyncio
    async def test_timeout_shown_in_result(self):
        """执行超时时，回复中显示超时提示。"""
        timeout_result = CodeResult(stdout="", stderr="", exit_code=-1, timed_out=True)
        router = make_router([OK_CODE_RESPONSE])

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message}"), \
             patch("src.agents.coder.code_runner.run_python", return_value=timeout_result):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert "超时" in result.content
        assert result.metadata["timed_out"] is True

    @pytest.mark.asyncio
    async def test_generate_failure_returns_friendly_message(self):
        """代码生成 LLM 调用失败，返回友好提示。"""
        router = MagicMock()
        router.complete = AsyncMock(side_effect=RuntimeError("API error"))

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message}"):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert "失败" in result.content
        assert result.needs_persona_formatting is True

    @pytest.mark.asyncio
    async def test_llm_returns_no_code_block(self):
        """LLM 返回纯文字（无代码块），返回友好提示。"""
        router = make_router(["这个问题很复杂，我来解释一下..."])

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message}"):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert "失败" in result.content

    @pytest.mark.asyncio
    async def test_no_output_shown_when_stdout_empty(self):
        """代码执行成功但没有 print 输出时，显示「无输出」。"""
        no_output_result = CodeResult(stdout="", stderr="", exit_code=0)
        router = make_router([OK_CODE_RESPONSE])

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message}"), \
             patch("src.agents.coder.code_runner.run_python", return_value=no_output_result):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert "无输出" in result.content

    @pytest.mark.asyncio
    async def test_fix_llm_failure_falls_back_to_original_error(self):
        """修复代码的 LLM 调用失败，直接返回原始错误结果。"""
        # 第一次生成成功，第二次修复抛异常
        router = MagicMock()
        router.complete = AsyncMock(side_effect=[OK_CODE_RESPONSE, RuntimeError("timeout")])

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message} {code} {error}"), \
             patch("src.agents.coder.code_runner.run_python", return_value=ERR_RESULT):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        # 应该返回原始的错误结果
        assert "出错" in result.content
        assert result.metadata["exit_code"] != 0

    @pytest.mark.asyncio
    async def test_snippet_fix_loop_stops_after_three_attempts(self):
        """snippet 模式修复循环上限为 3 次执行。"""
        router = make_router([OK_CODE_RESPONSE, FIXED_CODE_RESPONSE, FIXED_CODE_RESPONSE_2])

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message} {code} {error}"), \
             patch("src.agents.coder.code_runner.run_python", return_value=ERR_RESULT):
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert result.metadata["mode"] == "snippet"
        assert result.metadata["attempts"] == 3
        assert result.metadata["verification"]["passed"] is False
        assert router.complete.await_count == 3

    @pytest.mark.asyncio
    async def test_workspace_patch_success_sets_metadata(self):
        """workspace_patch 模式成功时返回结构化 metadata。"""
        router = make_router([WORKSPACE_PLAN_RESPONSE])
        changed_file = str((ROOT_DIR / "README.md").resolve())
        tx = TransactionResult(
            success=True,
            results=[
                FileEditResult(
                    success=True,
                    operation="append_to_file",
                    path=changed_file,
                    changed=True,
                )
            ],
            changed_files=[changed_file],
            rolled_back=False,
        )
        verify_ok = VerificationResult(
            passed=True,
            status="passed",
            checks=[{"name": "pytest", "passed": True}],
            artifacts=[changed_file],
        )

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message}"), \
             patch("src.agents.coder.file_editor.transactional_apply", return_value=tx) as mock_tx, \
             patch("src.agents.coder.verifier.verify_workspace", new=AsyncMock(return_value=verify_ok)) as mock_verify:
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_workspace_task(), router)

        assert "已完成 workspace 多文件修改" in result.content
        assert result.metadata["mode"] == "workspace_patch"
        assert result.metadata["attempts"] == 1
        assert result.metadata["changed_files"] == [changed_file]
        assert result.metadata["verification"]["passed"] is True
        assert result.metadata["rolled_back"] is False
        assert mock_tx.call_count == 1
        assert mock_verify.await_count == 1

    @pytest.mark.asyncio
    async def test_workspace_patch_retries_until_max_attempts_on_verification_failure(self):
        """workspace_patch 验证失败时最多修复 3 轮。"""
        router = make_router([
            WORKSPACE_PLAN_RESPONSE,
            WORKSPACE_PLAN_RESPONSE,
            WORKSPACE_PLAN_RESPONSE,
        ])
        changed_file = str((ROOT_DIR / "README.md").resolve())
        tx = TransactionResult(
            success=True,
            results=[
                FileEditResult(
                    success=True,
                    operation="append_to_file",
                    path=changed_file,
                    changed=True,
                )
            ],
            changed_files=[changed_file],
            rolled_back=False,
        )
        verify_fail = VerificationResult(
            passed=False,
            status="failed",
            reason="pytest 失败",
            checks=[{"name": "pytest", "passed": False}],
            artifacts=[changed_file],
        )

        with patch("src.agents.coder.load_prompt", return_value="prompt {user_message}"), \
             patch("src.agents.coder.file_editor.transactional_apply", return_value=tx) as mock_tx, \
             patch("src.agents.coder.verifier.verify_workspace", new=AsyncMock(return_value=verify_fail)) as mock_verify:
            agent = CoderAgent(memory=make_memory())
            result = await agent.execute(make_workspace_task(), router)

        assert "workspace 修改未完全通过验证" in result.content
        assert result.metadata["mode"] == "workspace_patch"
        assert result.metadata["attempts"] == 3
        assert result.metadata["verification"]["passed"] is False
        assert mock_tx.call_count == 3
        assert mock_verify.await_count == 3
        assert router.complete.await_count == 3

    @pytest.mark.asyncio
    async def test_workspace_patch_transaction_failure_reports_rollback(self):
        """事务失败且回滚时，metadata 标记 rolled_back。"""
        plan = {
            "summary": "do changes",
            "operations": [{"op": "append_to_file", "path": "README.md", "content": "x"}],
            "pytest_targets": [],
            "reason": "x",
        }
        changed_file = str((ROOT_DIR / "README.md").resolve())
        tx_fail = TransactionResult(
            success=False,
            results=[],
            changed_files=[changed_file],
            rolled_back=True,
            reason="目标锚点不存在",
        )
        router = MagicMock()

        agent = CoderAgent(memory=make_memory())
        with patch.object(agent, "_plan_workspace", new=AsyncMock(return_value=plan)), \
             patch.object(agent, "_fix_workspace_plan", new=AsyncMock(return_value=None)), \
             patch("src.agents.coder.file_editor.transactional_apply", return_value=tx_fail):
            result = await agent.execute(make_workspace_task(), router)

        assert "回滚状态：已发生回滚" in result.content
        assert result.metadata["mode"] == "workspace_patch"
        assert result.metadata["attempts"] == 1
        assert result.metadata["rolled_back"] is True
        assert result.metadata["verification"]["passed"] is False
