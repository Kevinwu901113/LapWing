"""Coder Agent — 写代码和执行。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseAgent
from .types import AgentSpec

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry

CODER_SYSTEM_PROMPT = """你是 Lapwing 团队的 Coder。你擅长写代码、调试、跑脚本。

## 你的职责

1. 根据需求写代码或修改代码
2. 执行代码或 shell 命令验证
3. 返回代码结果或执行输出

## 你的工作区

你的所有文件操作都在 data/agent_workspace/ 目录下。你不能直接修改 src/ 下的生产代码。

如果任务涉及修改系统代码，产出 patch 文件到 patches/ 目录，由 Kevin 审核后合入。

## 你的边界

- 你是执行者，不闲聊
- 不做需求评判，按指令完成
- 代码要简洁、可读
- 失败时报告错误信息和尝试过的方案

## 输出格式

完成任务后，输出简洁的总结：做了什么、结果如何、产出文件在哪。"""


class Coder(BaseAgent):
    """代码和执行 Agent。"""

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        dispatcher: "Dispatcher",
    ) -> "Coder":
        spec = AgentSpec(
            name="coder",
            description="写代码和执行",
            system_prompt=CODER_SYSTEM_PROMPT,
            model_slot="agent_execution",
            tools=["ws_file_read", "ws_file_write", "ws_file_list", "execute_shell", "run_python_code"],
            max_rounds=20,
            max_tokens=50000,
            timeout_seconds=600,
        )
        return cls(spec, llm_router, tool_registry, dispatcher)
