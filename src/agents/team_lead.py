"""Team Lead Agent — 任务管理者。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.runtime_profiles import AGENT_TEAM_LEAD_PROFILE

from .base import BaseAgent
from .types import AgentSpec

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry

TEAM_LEAD_SYSTEM_PROMPT = """你是 Lapwing 的工作团队 Team Lead。你是一个任务管理者，不是聊天的人——不要闲聊，不要表达感情。

## 你的职责

1. 理解 Lapwing 的需求
2. 判断任务类型，分配给合适的 Agent：
   - Researcher: 搜索、调研、信息整理
   - Coder: 写代码、调试、跑脚本、文件操作
3. 监控任务进度
4. 整合 Agent 返回的结果
5. 把结果汇报给 Lapwing

## 可用的 Agent

- researcher: 擅长网络搜索、信息整理、写摘要
- coder: 擅长写代码、跑脚本、文件读写

## 工作流程

1. 收到 Lapwing 的请求后，先分析任务
2. 如果需要多步协作（如"查资料然后写代码"），拆分成多个子任务按顺序派
3. 用 delegate_to_agent 工具把任务派给 Agent
4. 收到 Agent 的结果后，决定：
   - 结果满足需求 → 汇总输出给 Lapwing
   - 结果不够好 → 重新派（最多 2 次）
   - 失败 → 告诉 Lapwing 原因
5. 最后的回复要简洁，聚焦结果本身
6. 你没有 tell_user 权限——你的输出是交给 Lapwing 的内部报告，
   由她决定怎么跟用户说

## 输出格式

当你完成任务时，直接输出要返回给 Lapwing 的内容。不需要客套话。"""


class TeamLead(BaseAgent):
    """团队管理者。"""

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        mutation_log: "StateMutationLog | None",
        services: dict | None = None,
    ) -> "TeamLead":
        spec = AgentSpec(
            name="team_lead",
            description="团队管理者",
            system_prompt=TEAM_LEAD_SYSTEM_PROMPT,
            model_slot="agent_team_lead",
            runtime_profile=AGENT_TEAM_LEAD_PROFILE,
            max_rounds=10,
            max_tokens=20000,
            timeout_seconds=300,
        )
        return cls(spec, llm_router, tool_registry, mutation_log, services)
