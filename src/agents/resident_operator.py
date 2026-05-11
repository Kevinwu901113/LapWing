"""ResidentOperator Agent — persistent-identity browser/credential operations.

Post-v1 A §2.2 — drives Action(browser, profile=personal, ...) sequences
through kernel.execute, handles interrupts via the
try / wait_for_resume / except InterruptCancelled / finally cleanup
pattern (blueprint §8.4).

The kernel handle reaches this worker via self._services["kernel"] —
populated by LapwingBrain._build_services from AppContainer._init_kernel
(PR-13).

v1 task surface: deterministic mini-parser keyed on the first whitespace-
delimited token of message.content. This intentionally avoids an LLM loop
inside the worker — V-A3 acceptance verifies the kernel.execute → interrupt
→ resume closed loop, not natural-language understanding. An LLM-driven
task interpreter is post-v1 C work.

Recognised tasks:
  navigate <url>        # Action(browser, personal, navigate, {url})

Anything else returns AgentResult(success=False, answer="unsupported task").
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.config import get_settings
from src.core.runtime_profiles import STANDARD_PROFILE
from src.lapwing_kernel.pipeline.continuation_registry import (
    ContinuationRegistry,
    InterruptCancelled,
)
from src.lapwing_kernel.primitives.action import Action

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


# Statuses that mean "the action is suspended waiting on out-of-band action".
# The executor returns "interrupted" for policy-driven INTERRUPT; the
# BrowserAdapter returns captcha_required / waf_challenge / etc. for in-band
# challenge detection. All carry an interrupt_id when a continuation exists.
_INTERRUPTIBLE_STATUSES = frozenset(
    {
        "interrupted",
        "captcha_required",
        "waf_challenge",
        "auth_required",
        "user_attention_required",
    }
)


class ResidentOperator(BaseAgent):
    """Persistent-identity browser operator.

    Translates an AgentMessage into one or more kernel.execute(Action) calls.
    Handles policy/adapter interrupts by suspending on
    ContinuationRegistry.wait_for_resume and retrying after owner approval.
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
        cfg = (
            getattr(settings.agent_team, "resident_operator", None)
            or settings.agent_team.researcher
        )
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
        kernel = (self._services or {}).get("kernel")
        if kernel is None:
            return AgentResult(
                task_id=message.task_id,
                status="failed",
                result="",
                reason=(
                    "ResidentOperator requires services['kernel']. "
                    "AppContainer._init_kernel must compose the kernel "
                    "and LapwingBrain._build_services must inject it."
                ),
            )

        url = self._parse_navigate_task(message.content or "")
        if url is None:
            return AgentResult(
                task_id=message.task_id,
                status="failed",
                result="",
                reason=(
                    "ResidentOperator v1 task format: 'navigate <url>'. "
                    f"Received: {(message.content or '')[:80]!r}"
                ),
            )

        return await self._run_with_resume(
            kernel=kernel,
            message=message,
            action_factory=lambda: Action.new(
                "browser",
                "navigate",
                resource_profile="personal",
                args={"url": url},
            ),
        )

    @staticmethod
    def _parse_navigate_task(content: str) -> str | None:
        """Pull the URL out of 'navigate <url>'. Whitespace-tolerant."""
        parts = content.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[0].lower() != "navigate":
            return None
        url = parts[1].strip()
        return url or None

    async def _run_with_resume(
        self, *, kernel, message: AgentMessage, action_factory
    ) -> AgentResult:
        """The blueprint §8.4 try / wait_for_resume / except / finally pattern.

        Loop: execute the Action; on interruptible status, await owner
        approval via ContinuationRegistry; retry the same Action. On
        InterruptCancelled (owner denied or kernel-restart), unwind cleanly.
        """
        suspension_refs: list[str] = []
        registry = ContinuationRegistry.instance()

        try:
            obs = await kernel.execute(action_factory())
            while obs.status in _INTERRUPTIBLE_STATUSES:
                if not obs.interrupt_id:
                    return AgentResult(
                        task_id=message.task_id,
                        status="blocked",
                        result="",
                        reason=(
                            f"non-resumable interrupt status {obs.status!r} "
                            "without interrupt_id"
                        ),
                    )
                interrupt = kernel.interrupts.get(obs.interrupt_id)
                if interrupt is None or not interrupt.continuation_ref:
                    return AgentResult(
                        task_id=message.task_id,
                        status="blocked",
                        result="",
                        reason=(
                            f"non-resumable interrupt {obs.interrupt_id} "
                            "(no continuation_ref) — owner takeover required "
                            "out-of-band"
                        ),
                    )

                ref = interrupt.continuation_ref
                suspension_refs.append(ref)
                try:
                    await registry.wait_for_resume(ref)
                except InterruptCancelled as exc:
                    return AgentResult(
                        task_id=message.task_id,
                        status="blocked",
                        result="",
                        reason=f"interrupt cancelled: {exc}",
                    )

                # Owner approved — retry the same Action. The state that
                # makes the second pass succeed lives outside this worker:
                # CredentialUseState.mark_used for credential.use, or a
                # browser-manager-side flag for CAPTCHA pages. The worker
                # does not need to know which.
                obs = await kernel.execute(action_factory())

            if obs.status == "ok":
                return AgentResult(
                    task_id=message.task_id,
                    status="done",
                    result=obs.summary or "ok",
                )
            return AgentResult(
                task_id=message.task_id,
                status="failed",
                result="",
                reason=obs.summary or obs.status,
            )
        finally:
            # Defensive: wait_for_resume already removes the handle on
            # resume/cancel. This catches the corner case where the worker
            # crashed mid-loop and leaves no orphan futures behind.
            for ref in suspension_refs:
                if registry.has(ref):
                    registry.cancel(ref, reason="worker_finally_cleanup")
