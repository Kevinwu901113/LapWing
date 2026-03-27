"""Shell 任务策略：统一前置/后置判定与验证触发。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from src.core.shell_policy import (
    AlternativeProposal,
    CommandIntent,
    ExecutionConstraints,
    ExecutionSessionState,
    VerificationStatus,
)
from src.tools.shell_executor import ShellResult

PolicyAction = Literal["allow", "require_consent", "block"]


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    reason: str = ""
    failure_type: str | None = None
    alternative: AlternativeProposal | None = None
    should_verify: bool = False


@dataclass(frozen=True)
class ShellRuntimePolicy:
    analyze_command: Callable[[str], CommandIntent]
    should_request_consent_for_command: Callable[
        [ExecutionConstraints, CommandIntent, ExecutionSessionState],
        AlternativeProposal | None,
    ]
    failure_type_from_result: Callable[[ShellResult], str | None]
    infer_permission_denied_alternative: Callable[[ExecutionConstraints], str | None]
    should_validate_after_success: Callable[[ExecutionConstraints, CommandIntent, ShellResult], bool]
    verify_constraints: Callable[[ExecutionConstraints], VerificationStatus]
    failure_reason_builder: Callable[[ShellResult], str]

    def before_execute(
        self,
        *,
        constraints: ExecutionConstraints,
        intent: CommandIntent,
        state: ExecutionSessionState,
    ) -> PolicyDecision:
        proposal = self.should_request_consent_for_command(constraints, intent, state)
        if proposal is None:
            return PolicyDecision(action="allow")

        target_directory = constraints.active_directory or constraints.target_directory
        reason = (
            f"这条命令会把目标从 `{target_directory}` 改到 "
            f"`{proposal.directory}`，需要先征求用户同意。"
        )
        return PolicyDecision(
            action="require_consent",
            reason=reason,
            failure_type="requires_consent",
            alternative=proposal,
        )

    def after_execute(
        self,
        *,
        constraints: ExecutionConstraints,
        intent: CommandIntent,
        state: ExecutionSessionState,
        result: ShellResult,
        shell_allow_sudo: bool,
    ) -> PolicyDecision:
        failure_type = self.failure_type_from_result(result)
        if failure_type is not None:
            reason = self.failure_reason_builder(result)
            alternative: AlternativeProposal | None = None
            if (
                failure_type == "permission_denied"
                and not state.consent_required
                and constraints.target_directory is not None
                and not shell_allow_sudo
            ):
                alt_dir = self.infer_permission_denied_alternative(constraints)
                if alt_dir is not None:
                    alternative = AlternativeProposal(
                        directory=alt_dir,
                        reason=reason,
                        blocked_command=intent.command,
                    )
            return PolicyDecision(
                action="block",
                reason=reason,
                failure_type=failure_type,
                alternative=alternative,
            )

        if self.should_validate_after_success(constraints, intent, result):
            return PolicyDecision(action="allow", should_verify=True)

        return PolicyDecision(action="allow")

    def verify(self, constraints: ExecutionConstraints) -> VerificationStatus:
        return self.verify_constraints(constraints)
