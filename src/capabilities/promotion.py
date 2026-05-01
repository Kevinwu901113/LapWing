"""Promotion planner — computes allowed transitions without mutating state.

Returns PromotionPlan objects.  Does NOT update manifests, change
maturity/status, call CapabilityStore, wire into promote_skill, or
write stable state.

Phase 3A: planning foundation only — not wired into promotion execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityStatus,
)

if TYPE_CHECKING:
    from src.capabilities.evaluator import EvalRecord
    from src.capabilities.policy import PolicyDecision
    from src.capabilities.schema import CapabilityManifest


# ── Allowed maturity transitions ────────────────────────────────────────

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    CapabilityMaturity.DRAFT.value: {CapabilityMaturity.TESTING.value},
    CapabilityMaturity.TESTING.value: {CapabilityMaturity.STABLE.value, CapabilityMaturity.DRAFT.value},
    CapabilityMaturity.STABLE.value: {CapabilityMaturity.BROKEN.value},
    CapabilityMaturity.BROKEN.value: {CapabilityMaturity.REPAIRING.value},
    CapabilityMaturity.REPAIRING.value: {CapabilityMaturity.TESTING.value, CapabilityMaturity.DRAFT.value},
}

# Maturities that can transition to disabled/archived (any can, via status change)
_STATUS_TERMINAL_TRANSITIONS: set[str] = {
    CapabilityStatus.DISABLED.value,
    CapabilityStatus.ARCHIVED.value,
}


# ── Promotion plan ──────────────────────────────────────────────────────


@dataclass
class PromotionPlan:
    capability_id: str
    scope: str
    from_maturity: str
    to_maturity: str
    allowed: bool
    required_approval: bool = False
    required_evidence: list[str] = field(default_factory=list)
    blocking_findings: list[dict[str, Any]] = field(default_factory=list)
    policy_decisions: list[dict[str, Any]] = field(default_factory=list)
    eval_record_id: str | None = None
    explanation: str = ""


# ── Planner ─────────────────────────────────────────────────────────────


class PromotionPlanner:
    """Plan maturity transitions without executing them.

    Computes whether a transition would be allowed and what evidence
    or approval would be required.  Never mutates store or manifest state.
    """

    def plan_transition(
        self,
        manifest: "CapabilityManifest",
        to_maturity: str,
        *,
        eval_record: "EvalRecord | None" = None,
        approval: Any | None = None,
        failure_evidence: dict[str, Any] | None = None,
    ) -> PromotionPlan:
        """Compute whether a maturity transition is allowed."""

        from_mat = manifest.maturity.value
        status = manifest.status.value
        risk = manifest.risk_level.value
        plan = PromotionPlan(
            capability_id=manifest.id,
            scope=manifest.scope.value,
            from_maturity=from_mat,
            to_maturity=to_maturity,
            allowed=False,
        )

        # Terminal status transitions (disabled/archived) can be planned
        if to_maturity in _STATUS_TERMINAL_TRANSITIONS:
            plan.allowed = True
            plan.explanation = f"Transition to {to_maturity} can be planned"
            return plan

        # Archived cannot transition (future restore not implemented)
        if status == CapabilityStatus.ARCHIVED.value:
            plan.explanation = "Archived capability cannot transition"
            plan.required_evidence.append("restore_mechanism (not implemented)")
            return plan

        # Quarantined restrictions
        if status == CapabilityStatus.QUARANTINED.value:
            if to_maturity == CapabilityMaturity.STABLE.value:
                plan.explanation = "Quarantined capability cannot promote directly to stable"
                plan.blocking_findings.append({
                    "code": "quarantined_to_stable_blocked",
                    "message": "Quarantined capability cannot promote to stable",
                })
                return plan

        # Check if the transition is known
        allowed_targets = _ALLOWED_TRANSITIONS.get(from_mat, set())
        if to_maturity not in allowed_targets and to_maturity not in _STATUS_TERMINAL_TRANSITIONS:
            plan.explanation = (
                f"Unknown transition: {from_mat} -> {to_maturity}. "
                f"Allowed transitions from {from_mat}: {sorted(allowed_targets)}"
            )
            return plan

        # ── draft -> testing ─────────────────────────────────────
        if from_mat == CapabilityMaturity.DRAFT.value and to_maturity == CapabilityMaturity.TESTING.value:
            return self._plan_draft_to_testing(manifest, plan, eval_record, approval)

        # ── testing -> stable ────────────────────────────────────
        if from_mat == CapabilityMaturity.TESTING.value and to_maturity == CapabilityMaturity.STABLE.value:
            return self._plan_testing_to_stable(manifest, plan, eval_record, approval)

        # ── testing -> draft (downgrade) ─────────────────────────
        if from_mat == CapabilityMaturity.TESTING.value and to_maturity == CapabilityMaturity.DRAFT.value:
            plan.allowed = True
            plan.explanation = "Downgrade from testing to draft is always allowed"
            return plan

        # ── stable -> broken ─────────────────────────────────────
        if from_mat == CapabilityMaturity.STABLE.value and to_maturity == CapabilityMaturity.BROKEN.value:
            return self._plan_stable_to_broken(manifest, plan, failure_evidence)

        # ── broken -> repairing ──────────────────────────────────
        if from_mat == CapabilityMaturity.BROKEN.value and to_maturity == CapabilityMaturity.REPAIRING.value:
            plan.allowed = True
            plan.explanation = "Transition from broken to repairing is always allowed"
            return plan

        # ── repairing -> testing ─────────────────────────────────
        if from_mat == CapabilityMaturity.REPAIRING.value and to_maturity == CapabilityMaturity.TESTING.value:
            return self._plan_repairing_to_testing(manifest, plan, eval_record)

        # ── repairing -> draft ───────────────────────────────────
        if from_mat == CapabilityMaturity.REPAIRING.value and to_maturity == CapabilityMaturity.DRAFT.value:
            plan.allowed = True
            plan.explanation = "Reset from repairing to draft is always allowed"
            return plan

        plan.explanation = f"Transition {from_mat} -> {to_maturity} not supported"
        return plan

    # ── Specific transition planners ──────────────────────────────

    def _plan_draft_to_testing(
        self,
        manifest: "CapabilityManifest",
        plan: PromotionPlan,
        eval_record: "EvalRecord | None",
        approval: Any | None,
    ) -> PromotionPlan:
        # Requires valid format and evaluator pass or only low-severity warnings
        if eval_record is None:
            plan.allowed = True
            plan.explanation = "draft -> testing allowed; recommend running evaluator first"
            plan.required_evidence.append("evaluator_pass_recommended")
            return plan

        if eval_record.passed:
            plan.allowed = True
            plan.eval_record_id = eval_record.created_at
            plan.explanation = "draft -> testing allowed with passing evaluation"
            return plan

        # Not passed — check if there are only warnings (no errors)
        has_errors = any(
            f.severity.value == "error"
            for f in eval_record.findings
            if hasattr(f, "severity")
        )
        if not has_errors:
            plan.allowed = True
            plan.eval_record_id = eval_record.created_at
            plan.explanation = "draft -> testing allowed (only warnings in evaluation)"
            return plan

        plan.blocking_findings = [
            {"code": f.code, "message": f.message}
            for f in eval_record.findings
            if hasattr(f, "severity") and f.severity.value == "error"
        ]
        plan.explanation = "draft -> testing blocked by evaluation errors"
        return plan

    def _plan_testing_to_stable(
        self,
        manifest: "CapabilityManifest",
        plan: PromotionPlan,
        eval_record: "EvalRecord | None",
        approval: Any | None,
    ) -> PromotionPlan:
        risk = manifest.risk_level

        # High risk can never auto-promote
        if risk == CapabilityRiskLevel.HIGH:
            has_approval = False
            if approval is not None:
                has_approval = getattr(approval, "approved", False) if hasattr(approval, "approved") else bool(approval)
            if not has_approval:
                plan.required_approval = True
                plan.required_evidence.append("explicit_owner_approval")
                plan.explanation = "testing -> stable blocked: high risk requires explicit owner approval (cannot auto-promote)"
                return plan
            plan.required_approval = True

        # Requires evaluator pass
        if eval_record is None:
            plan.required_evidence.append("evaluator_pass")
            plan.explanation = "testing -> stable blocked: no eval record available"
            return plan

        if not eval_record.passed:
            plan.blocking_findings = [
                {"code": f.code, "message": f.message}
                for f in eval_record.findings
                if hasattr(f, "severity") and f.severity.value == "error"
            ]
            plan.explanation = "testing -> stable blocked by evaluation failures"
            return plan

        # Medium risk requires approval or eval evidence
        if risk == CapabilityRiskLevel.MEDIUM:
            has_approval = False
            if approval is not None:
                has_approval = getattr(approval, "approved", False) if hasattr(approval, "approved") else bool(approval)
            if has_approval:
                plan.required_approval = True
            else:
                plan.required_evidence.append("eval_passing_sufficient")

        plan.allowed = True
        plan.eval_record_id = eval_record.created_at
        plan.explanation = "testing -> stable allowed"
        return plan

    def _plan_stable_to_broken(
        self,
        manifest: "CapabilityManifest",
        plan: PromotionPlan,
        failure_evidence: dict[str, Any] | None,
    ) -> PromotionPlan:
        if failure_evidence is None:
            plan.required_evidence.append("failure_evidence")
            plan.explanation = "stable -> broken requires failure evidence"
            return plan

        plan.allowed = True
        plan.explanation = "stable -> broken planned with failure evidence"
        plan.required_evidence.append("failure_evidence_provided")
        return plan

    def _plan_repairing_to_testing(
        self,
        manifest: "CapabilityManifest",
        plan: PromotionPlan,
        eval_record: "EvalRecord | None",
    ) -> PromotionPlan:
        if eval_record is None:
            plan.allowed = True
            plan.explanation = "repairing -> testing allowed; recommend evaluation"
            plan.required_evidence.append("evaluator_pass_recommended")
            return plan

        if eval_record.passed:
            plan.allowed = True
            plan.eval_record_id = eval_record.created_at
            plan.explanation = "repairing -> testing allowed with passing evaluation"
            return plan

        has_errors = any(
            f.severity.value == "error"
            for f in eval_record.findings
            if hasattr(f, "severity")
        )
        if not has_errors:
            plan.allowed = True
            plan.eval_record_id = eval_record.created_at
            plan.explanation = "repairing -> testing allowed (only warnings)"
            return plan

        plan.blocking_findings = [
            {"code": f.code, "message": f.message}
            for f in eval_record.findings
            if hasattr(f, "severity") and f.severity.value == "error"
        ]
        plan.explanation = "repairing -> testing blocked by evaluation errors"
        return plan
