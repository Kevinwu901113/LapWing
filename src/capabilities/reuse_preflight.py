"""Runtime preflight checks for capability reuse.

Preflight consumes manifest metadata plus the latest valid EvalRecord. It does
not rescan capability files; static findings belong to the evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityStatus,
    SideEffect,
)
from src.eval.axes import AxisStatus, EvalAxis


@dataclass(frozen=True)
class CapabilityUseContext:
    user_task: str = ""
    sensitive_contexts: set[str] = field(default_factory=set)
    approved_sensitive_contexts: set[str] = field(default_factory=set)
    satisfied_preflight_checks: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ReusePreflightInput:
    capability: Any
    runtime_profile: Any
    auth_level: int
    current_context: CapabilityUseContext
    requested_arguments: dict[str, Any]
    execution_mode: Literal["retrieval", "run", "subtool"]
    latest_eval_record: Any
    available_tools: set[str] = field(default_factory=set)
    available_permissions: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ReusePreflightDecision:
    allowed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls) -> "ReusePreflightDecision":
        return cls(True, "preflight_passed")

    @classmethod
    def deny(cls, reason: str, **details: Any) -> "ReusePreflightDecision":
        return cls(False, reason, details)


_PROFILE_SIDE_EFFECTS: dict[str, set[str]] = {
    "standard": {SideEffect.NONE.value},
    "inner_tick": {SideEffect.NONE.value},
    "local_execution": {
        SideEffect.NONE.value,
        SideEffect.LOCAL_WRITE.value,
        SideEffect.LOCAL_DELETE.value,
        SideEffect.SHELL_EXEC.value,
    },
    "task_execution": {
        SideEffect.NONE.value,
        SideEffect.LOCAL_WRITE.value,
        SideEffect.LOCAL_DELETE.value,
        SideEffect.SHELL_EXEC.value,
    },
}

_KNOWN_PREFLIGHT_CHECKS = {
    "owner_confirmed_scope",
    "fresh_eval_record",
    "runtime_profile_allows_tools",
    "sensitive_context_approved",
    "rollback_available",
}


def run_reuse_preflight(inp: ReusePreflightInput) -> ReusePreflightDecision:
    manifest = inp.capability.manifest
    profile_name = str(getattr(inp.runtime_profile, "name", inp.runtime_profile) or "")

    if manifest.status != CapabilityStatus.ACTIVE:
        return ReusePreflightDecision.deny("status_not_active", status=manifest.status.value)
    if manifest.maturity != CapabilityMaturity.STABLE:
        return ReusePreflightDecision.deny("maturity_not_stable", maturity=manifest.maturity.value)
    if not _trust_satisfied(manifest.trust_required, inp.auth_level):
        return ReusePreflightDecision.deny("trust_not_satisfied", trust_required=manifest.trust_required)

    if manifest.risk_level == CapabilityRiskLevel.HIGH and profile_name in {"standard", "inner_tick"}:
        return ReusePreflightDecision.deny("risk_not_allowed_by_profile", risk_level=manifest.risk_level.value)

    if profile_name == "inner_tick":
        tags = {t.lower() for t in (manifest.tags or [])}
        if not (tags & {"auto_run", "inner_tick"}):
            return ReusePreflightDecision.deny("inner_tick_requires_auto_run_tag", tags=sorted(tags))

    missing_tools = set(manifest.required_tools) - set(inp.available_tools)
    if missing_tools:
        return ReusePreflightDecision.deny("required_tools_not_in_profile", missing_tools=sorted(missing_tools))

    missing_permissions = set(manifest.required_permissions) - set(inp.available_permissions)
    if missing_permissions:
        return ReusePreflightDecision.deny(
            "required_permissions_not_available",
            missing_permissions=sorted(missing_permissions),
        )

    task_text = inp.current_context.user_task.lower()
    for rule in manifest.do_not_apply_when:
        if rule and rule.lower() in task_text:
            return ReusePreflightDecision.deny("do_not_apply_when_matched", rule=rule)

    sensitive = {v.value if hasattr(v, "value") else str(v) for v in manifest.sensitive_contexts}
    intersection = sensitive & set(inp.current_context.sensitive_contexts)
    approved = set(inp.current_context.approved_sensitive_contexts)
    if intersection and not intersection.issubset(approved):
        return ReusePreflightDecision.deny(
            "sensitive_context_not_approved",
            sensitive_contexts=sorted(intersection),
        )

    side_effects = {v.value if hasattr(v, "value") else str(v) for v in manifest.side_effects}
    if not side_effects:
        return ReusePreflightDecision.deny("unknown_side_effects")
    allowed_effects = _PROFILE_SIDE_EFFECTS.get(profile_name)
    if allowed_effects is not None and not side_effects.issubset(allowed_effects):
        return ReusePreflightDecision.deny(
            "side_effects_not_allowed_by_profile",
            side_effects=sorted(side_effects - allowed_effects),
        )

    unknown_checks = set(manifest.required_preflight_checks) - _KNOWN_PREFLIGHT_CHECKS
    if unknown_checks:
        return ReusePreflightDecision.deny("unknown_required_preflight_checks", checks=sorted(unknown_checks))

    missing_checks = set(manifest.required_preflight_checks) - set(inp.current_context.satisfied_preflight_checks)
    if missing_checks:
        return ReusePreflightDecision.deny("required_preflight_checks_unsatisfied", checks=sorted(missing_checks))

    axis_denial = _axis_denial(manifest, inp.latest_eval_record)
    if axis_denial is not None:
        return axis_denial

    return ReusePreflightDecision.allow()


def _axis_denial(manifest: Any, record: Any) -> ReusePreflightDecision | None:
    required = [EvalAxis.FUNCTIONAL.value, EvalAxis.SAFETY.value]
    if manifest.sensitive_contexts:
        required.append(EvalAxis.PRIVACY.value)
    effects = {v.value if hasattr(v, "value") else str(v) for v in manifest.side_effects}
    if effects & {
        SideEffect.LOCAL_WRITE.value,
        SideEffect.LOCAL_DELETE.value,
        SideEffect.NETWORK_SEND.value,
        SideEffect.PUBLIC_OUTPUT.value,
        SideEffect.EXTERNAL_MUTATION.value,
        SideEffect.SHELL_EXEC.value,
    }:
        required.append(EvalAxis.REVERSIBILITY.value)

    axes = getattr(record, "axes", {}) or {}
    for axis in required:
        result = axes.get(axis)
        status = getattr(result, "status", AxisStatus.UNKNOWN)
        status_value = status.value if hasattr(status, "value") else str(status)
        if status_value != AxisStatus.PASS.value:
            return ReusePreflightDecision.deny("axis_not_pass", axis=axis, status=status_value)
    return None


def _trust_satisfied(required: str, auth_level: int) -> bool:
    required = (required or "developer").lower()
    ranks = {"guest": 1, "trusted": 2, "developer": 3, "owner": 3}
    return int(auth_level) >= ranks.get(required, 3)

