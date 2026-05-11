"""ResidentOperator Agent — persistent-identity browser/credential operations.

v1 scope (blueprint §16 O-1, Slice I.1):
  Built-in agent kind registered in the catalog so that:
    delegate_to_agent(agent_name="resident_operator", task=...)
  can be dispatched at the catalog/spec layer. The full runtime — an LLM
  loop that translates Kevin's task descriptions into kernel.execute(Action)
  sequences with interrupt-aware waiting — depends on wiring the kernel
  into the cognition pipeline (Slice I.2 / PR-10 facade work). Until that
  wiring lands, this class exposes:
    - the AgentSpec.runtime_profile + model_slot needed by the catalog
    - a minimal execute() that signals "not yet wired" so existing
      delegate paths fail predictably rather than throwing AttributeError

The §15.1 closed-loop e2e test (PR-08) drives the kernel directly through
a synthesized worker coroutine, demonstrating the resume mechanics work
end-to-end without yet needing this class.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.config import get_settings
from src.core.runtime_profiles import STANDARD_PROFILE

from .base import BaseAgent
from .types import AgentMessage, AgentResult, AgentSpec

if TYPE_CHECKING:
    from src.core.llm_router import LLMRouter
    from src.logging.state_mutation_log import StateMutationLog
    from src.tools.registry import ToolRegistry

logger = logging.getLogger("lapwing.agents.resident_operator")


RESIDENT_OPERATOR_SYSTEM_PROMPT = """你是 Lapwing 的 Resident Operator。

你专门处理需要持久身份的浏览器操作:
  - 登录态会话(github / gmail / 微博 等)
  - 长会话:多步骤、可能跨多页的操作
  - 可能触发 owner 介入(CAPTCHA、2FA、WAF 验证)的任务

你不做调研性搜索 — 那是 Researcher 的活。你不写代码 — 那是 Coder 的活。
你只做"已经登录过的网站上的具体操作"。

当遇到 CAPTCHA 或登录要求时:
  - 直接报告状态,Lapwing 会通知 Kevin
  - Kevin 通过桌面端 /interrupts 列表 approve 后,你会自动从中断点继续
  - 不要尝试绕过任何验证

你的输出是给 Lapwing 的内部报告,不是给 Kevin 的回复。"""


class ResidentOperator(BaseAgent):
    """Persistent-identity browser operator.

    v1: registered as a builtin agent kind. Runtime LLM loop is a stub
    until kernel-to-cognition wiring lands. See module docstring.
    """

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        mutation_log: "StateMutationLog | None",
        services: dict | None = None,
    ) -> "ResidentOperator":
        settings = get_settings()
        # Use agent_team.researcher's resource budget as a temporary default
        # — resident_operator gets its own [agent_team.resident_operator]
        # block when full integration lands.
        cfg = getattr(settings.agent_team, "resident_operator", None) or settings.agent_team.researcher
        spec = AgentSpec(
            name="resident_operator",
            description="持久身份浏览器操作员",
            system_prompt=RESIDENT_OPERATOR_SYSTEM_PROMPT,
            model_slot="agent_execution",
            runtime_profile=STANDARD_PROFILE,
            max_rounds=cfg.max_rounds,
            max_tokens=cfg.max_tokens,
            timeout_seconds=cfg.timeout_seconds,
        )
        return cls(spec, llm_router, tool_registry, mutation_log, services)

    async def execute(self, message: AgentMessage) -> AgentResult:
        """v1 stub. The §15.1 e2e test drives the kernel directly through
        a synthesized worker; full LLM-driven execution lands when the
        kernel is wired into cognition (post-PR-10)."""
        logger.warning(
            "ResidentOperator.execute called but full runtime not yet wired; "
            "task=%r will be handled by direct kernel.execute in cognition once "
            "Slice I.2 facade integration completes.",
            message.content[:200] if message.content else "",
        )
        return AgentResult(
            success=False,
            answer=(
                "ResidentOperator runtime not yet wired. The agent kind is "
                "registered in the catalog; full LLM-driven dispatch lands "
                "when cognition wires the kernel directly. For v1 §15.1 the "
                "kernel.execute path is exercised by the e2e test."
            ),
        )
