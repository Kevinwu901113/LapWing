"""Coder Agent — 生成并执行 Python 代码。"""

import logging
import re

from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.prompt_loader import load_prompt
from src.tools import code_runner
from src.tools.code_runner import CodeResult

logger = logging.getLogger("lapwing.agents.coder")

# 输出中代码块的最大显示行数
_MAX_CODE_LINES = 80


class CoderAgent(BaseAgent):
    """编写和运行 Python 代码，帮助解决编程问题。"""

    name = "coder"
    description = "编写和运行 Python 代码，帮助解决编程问题"
    capabilities = ["生成 Python 代码", "运行代码并返回结果", "调试代码错误"]

    def __init__(self, memory) -> None:
        self._memory = memory

    async def execute(self, task: AgentTask, router) -> AgentResult:
        """生成代码 → 执行 → 自动修复（最多 1 次）→ 返回结果。"""
        # 1. 生成代码
        code = await self._generate_code(task.user_message, router)
        if code is None:
            return AgentResult(
                content="代码生成失败，请重新描述你的需求。",
                needs_persona_formatting=True,
            )

        # 2. 执行代码
        result = await code_runner.run_python(code)

        # 3. 如果失败，尝试自动修复一次
        if result.exit_code != 0 and not result.timed_out:
            logger.info("[coder] 执行失败，尝试自动修复")
            fixed_code = await self._fix_code(code, result.stderr, router)
            if fixed_code is not None and fixed_code != code:
                result = await code_runner.run_python(fixed_code)
                code = fixed_code

        # 4. 组装回复
        return AgentResult(
            content=self._format_reply(code, result),
            needs_persona_formatting=False,
            metadata={"exit_code": result.exit_code, "timed_out": result.timed_out},
        )

    async def _generate_code(self, user_message: str, router) -> str | None:
        """调用 LLM 生成 Python 代码，返回提取出的代码字符串。"""
        prompt = load_prompt("coder_generate").replace("{user_message}", user_message)
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=1024,
            )
            return _extract_code(raw)
        except Exception as e:
            logger.warning(f"[coder] 代码生成出错: {e}")
            return None

    async def _fix_code(self, code: str, error: str, router) -> str | None:
        """调用 LLM 修复有错误的代码。"""
        prompt = (
            load_prompt("coder_fix")
            .replace("{code}", code)
            .replace("{error}", error[:500])
        )
        try:
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=1024,
            )
            return _extract_code(raw)
        except Exception as e:
            logger.warning(f"[coder] 代码修复出错: {e}")
            return None

    def _format_reply(self, code: str, result: CodeResult) -> str:
        """将代码和执行结果格式化为用户友好的回复。"""
        parts: list[str] = []

        # 代码块（超过上限时截断）
        code_lines = code.splitlines()
        if len(code_lines) > _MAX_CODE_LINES:
            displayed = "\n".join(code_lines[:_MAX_CODE_LINES])
            parts.append(f"```python\n{displayed}\n# ... (已截断)\n```")
        else:
            parts.append(f"```python\n{code}\n```")

        # 执行结果
        if result.timed_out:
            parts.append("⏱ 执行超时（超过 10 秒），已中止。")
        elif result.exit_code == 0:
            if result.stdout.strip():
                parts.append(f"**执行结果：**\n```\n{result.stdout.strip()}\n```")
            else:
                parts.append("**执行结果：** （无输出）")
        else:
            parts.append(f"**执行出错（exit code {result.exit_code}）：**\n```\n{result.stderr.strip()}\n```")

        return "\n\n".join(parts)


def _extract_code(text: str) -> str | None:
    """从 LLM 响应中提取 ```python 代码块内的内容。"""
    # 优先匹配 ```python ... ```
    match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 降级：匹配任意 ``` ... ```
    match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 如果没有代码块，检查是否整个响应就是代码
    stripped = text.strip()
    if stripped.startswith("def ") or stripped.startswith("import ") or stripped.startswith("print("):
        return stripped
    return None
