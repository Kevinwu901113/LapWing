"""Phase 3A tests: PromotionPlanner computes allowed/blocked transitions
without mutating store state."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.capabilities.evaluator import EvalFinding, EvalRecord, FindingSeverity
from src.capabilities.promotion import PromotionPlan, PromotionPlanner
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)


def _make_manifest(**overrides) -> CapabilityManifest:
    defaults = {
        "id": "test_skill_01",
        "name": "Test Skill",
        "description": "A test capability.",
        "type": CapabilityType.SKILL,
        "scope": CapabilityScope.WORKSPACE,
        "version": "0.1.0",
        "maturity": CapabilityMaturity.DRAFT,
        "status": CapabilityStatus.ACTIVE,
        "risk_level": CapabilityRiskLevel.LOW,
    }
    defaults.update(overrides)
    return CapabilityManifest(**defaults)


def _make_eval(*, passed: bool = True, errors: int = 0, warnings: int = 0) -> EvalRecord:
    findings = []
    for i in range(errors):
        findings.append(EvalFinding(
            severity=FindingSeverity.ERROR,
            code=f"err_{i}", message=f"Error {i}",
        ))
    for i in range(warnings):
        findings.append(EvalFinding(
            severity=FindingSeverity.WARNING,
            code=f"warn_{i}", message=f"Warning {i}",
        ))
    return EvalRecord(
        capability_id="test_skill_01",
        scope="workspace",
        content_hash="abc123",
        passed=passed,
        score=max(0.0, 1.0 - errors * 0.3 - warnings * 0.1),
        findings=findings,
    )


@pytest.fixture
def planner():
    return PromotionPlanner()


# ── draft -> testing ────────────────────────────────────────────────────

class TestDraftToTesting:
    def test_allowed_without_eval(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.DRAFT)
        plan = planner.plan_transition(m, "testing")
        assert plan.allowed

    def test_allowed_with_passing_eval(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.DRAFT)
        ev = _make_eval(passed=True)
        plan = planner.plan_transition(m, "testing", eval_record=ev)
        assert plan.allowed

    def test_allowed_with_warnings_only(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.DRAFT)
        ev = _make_eval(passed=False, warnings=1)
        plan = planner.plan_transition(m, "testing", eval_record=ev)
        assert plan.allowed

    def test_blocked_with_errors(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.DRAFT)
        ev = _make_eval(passed=False, errors=1)
        plan = planner.plan_transition(m, "testing", eval_record=ev)
        assert not plan.allowed
        assert len(plan.blocking_findings) > 0


# ── testing -> stable ───────────────────────────────────────────────────

class TestTestingToStable:
    def test_blocked_without_eval(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.TESTING)
        plan = planner.plan_transition(m, "stable")
        assert not plan.allowed

    def test_allowed_with_passing_eval_low_risk(self, planner):
        m = _make_manifest(
            maturity=CapabilityMaturity.TESTING,
            risk_level=CapabilityRiskLevel.LOW,
        )
        ev = _make_eval(passed=True)
        plan = planner.plan_transition(m, "stable", eval_record=ev)
        assert plan.allowed

    def test_blocked_with_failing_eval(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.TESTING)
        ev = _make_eval(passed=False, errors=1)
        plan = planner.plan_transition(m, "stable", eval_record=ev)
        assert not plan.allowed

    def test_medium_risk_with_passing_eval_allowed(self, planner):
        m = _make_manifest(
            maturity=CapabilityMaturity.TESTING,
            risk_level=CapabilityRiskLevel.MEDIUM,
        )
        ev = _make_eval(passed=True)
        plan = planner.plan_transition(m, "stable", eval_record=ev)
        assert plan.allowed

    def test_high_risk_blocked_without_approval(self, planner):
        m = _make_manifest(
            maturity=CapabilityMaturity.TESTING,
            risk_level=CapabilityRiskLevel.HIGH,
        )
        ev = _make_eval(passed=True)
        plan = planner.plan_transition(m, "stable", eval_record=ev, approval=None)
        assert not plan.allowed
        assert plan.required_approval

    def test_high_risk_allowed_with_approval(self, planner):
        m = _make_manifest(
            maturity=CapabilityMaturity.TESTING,
            risk_level=CapabilityRiskLevel.HIGH,
        )
        ev = _make_eval(passed=True)
        plan = planner.plan_transition(m, "stable", eval_record=ev, approval={"approved": True})
        assert plan.allowed


# ── quarantined -> stable blocked ───────────────────────────────────────

class TestQuarantinedToStable:
    def test_quarantined_to_stable_blocked(self, planner):
        m = _make_manifest(
            maturity=CapabilityMaturity.TESTING,
            status=CapabilityStatus.QUARANTINED,
        )
        plan = planner.plan_transition(m, "stable")
        assert not plan.allowed


# ── archived transitions ────────────────────────────────────────────────

class TestArchivedTransitions:
    def test_archived_cannot_transition(self, planner):
        m = _make_manifest(
            maturity=CapabilityMaturity.STABLE,
            status=CapabilityStatus.ARCHIVED,
        )
        plan = planner.plan_transition(m, "testing")
        assert not plan.allowed

    def test_archived_to_archived_planned(self, planner):
        m = _make_manifest(status=CapabilityStatus.ARCHIVED)
        plan = planner.plan_transition(m, "archived")
        assert plan.allowed

    def test_archived_to_disabled_planned(self, planner):
        m = _make_manifest(status=CapabilityStatus.ARCHIVED)
        plan = planner.plan_transition(m, "disabled")
        assert plan.allowed


# ── stable -> broken ────────────────────────────────────────────────────

class TestStableToBroken:
    def test_requires_failure_evidence(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.STABLE)
        plan = planner.plan_transition(m, "broken")
        assert not plan.allowed

    def test_allowed_with_failure_evidence(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.STABLE)
        plan = planner.plan_transition(m, "broken", failure_evidence={"reason": "tests failing"})
        assert plan.allowed


# ── broken -> repairing ─────────────────────────────────────────────────

class TestBrokenToRepairing:
    def test_always_allowed(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.BROKEN)
        plan = planner.plan_transition(m, "repairing")
        assert plan.allowed


# ── repairing -> testing / draft ────────────────────────────────────────

class TestRepairingTransitions:
    def test_to_testing_allowed_without_eval(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.REPAIRING)
        plan = planner.plan_transition(m, "testing")
        assert plan.allowed

    def test_to_testing_allowed_with_passing_eval(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.REPAIRING)
        ev = _make_eval(passed=True)
        plan = planner.plan_transition(m, "testing", eval_record=ev)
        assert plan.allowed

    def test_to_draft_allowed(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.REPAIRING)
        plan = planner.plan_transition(m, "draft")
        assert plan.allowed


# ── testing -> draft (downgrade) ────────────────────────────────────────

class TestTestingToDraft:
    def test_downgrade_allowed(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.TESTING)
        plan = planner.plan_transition(m, "draft")
        assert plan.allowed


# ── No mutation ─────────────────────────────────────────────────────────

class TestNoMutation:
    def test_planner_does_not_mutate_manifest(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.DRAFT)
        original = m.model_dump()
        planner.plan_transition(m, "testing")
        assert m.model_dump() == original

    def test_planner_does_not_mutate_eval_record(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.TESTING)
        ev = _make_eval(passed=True)
        original_score = ev.score
        planner.plan_transition(m, "stable", eval_record=ev)
        assert ev.score == original_score


# ── Unknown transition ──────────────────────────────────────────────────

class TestUnknownTransition:
    def test_unknown_transition_blocked(self, planner):
        m = _make_manifest(maturity=CapabilityMaturity.STABLE)
        plan = planner.plan_transition(m, "draft")
        assert not plan.allowed
