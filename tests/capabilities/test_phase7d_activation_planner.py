"""Phase 7D-A tests: activation planner model, gate logic, storage, blocking conditions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.quarantine_activation_planner import (
    QuarantineActivationPlan,
    list_quarantine_activation_plans,
    plan_quarantine_activation,
    view_quarantine_activation_plan,
)
from src.capabilities.store import CapabilityStore


# ── helpers ────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _create_quarantine_capability(
    store: CapabilityStore,
    cap_id: str,
    *,
    write_manifest: bool = True,
    write_review: bool = True,
    write_audit: bool = True,
    review_status: str = "approved_for_testing",
    audit_passed: bool = True,
    audit_recommended: str = "approved_for_testing",
    status: str = "quarantined",
    maturity: str = "draft",
    risk_level: str = "low",
    overrides: dict | None = None,
) -> Path:
    qroot = store.data_dir / "quarantine"
    qroot.mkdir(parents=True, exist_ok=True)
    qdir = qroot / cap_id
    qdir.mkdir(parents=True, exist_ok=True)

    fm = {
        "id": cap_id,
        "name": f"Test {cap_id}",
        "description": "Quarantined test package.",
        "type": "skill",
        "scope": "user",
        "version": "0.1.0",
        "maturity": maturity,
        "status": status,
        "risk_level": risk_level,
        "triggers": ["when test"],
        "tags": ["test"],
        "trust_required": "developer",
        "required_tools": [],
        "required_permissions": [],
    }
    if overrides:
        fm.update(overrides)

    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nTest.\n\n"
        "## Procedure\n1. Test\n\n"
        "## Verification\nPass.\n\n"
        "## Failure handling\nRetry."
    )
    (qdir / "CAPABILITY.md").write_text(md, encoding="utf-8")

    if write_manifest:
        (qdir / "manifest.json").write_text(json.dumps({
            k: v for k, v in fm.items() if k not in ("version",)
        }, indent=2), encoding="utf-8")

    import_report = {
        "capability_id": cap_id,
        "source_type": "local_package",
        "source_path_hash": "abc123",
        "imported_at": "2026-05-01T00:00:00+00:00",
        "original_content_hash": "hash-abc",
        "target_scope": "user",
        "eval_passed": True,
        "eval_score": 1.0,
        "eval_findings": [],
        "policy_findings": [],
        "files_summary": {"scripts": [], "tests": [], "examples": []},
        "name": f"Test {cap_id}",
        "type": "skill",
        "risk_level": risk_level,
        "quarantine_reason": "Test",
    }
    (qdir / "import_report.json").write_text(
        json.dumps(import_report, indent=2), encoding="utf-8",
    )

    if write_review:
        rev_dir = qdir / "quarantine_reviews"
        rev_dir.mkdir(parents=True, exist_ok=True)
        review = {
            "capability_id": cap_id,
            "review_id": "review_test001",
            "review_status": review_status,
            "reviewer": "tester",
            "reason": "All checks passed",
            "created_at": "2026-05-02T10:00:00+00:00",
        }
        (rev_dir / "review_test001.json").write_text(
            json.dumps(review, indent=2), encoding="utf-8",
        )

    if write_audit:
        aud_dir = qdir / "quarantine_audit_reports"
        aud_dir.mkdir(parents=True, exist_ok=True)
        audit = {
            "capability_id": cap_id,
            "audit_id": "audit_test001",
            "created_at": "2026-05-02T09:00:00+00:00",
            "passed": audit_passed,
            "risk_level": risk_level,
            "findings": [
                {"severity": "info", "code": "test_finding", "message": "Test finding"},
                {"severity": "warning", "code": "quarantined_restricted", "message": "Quarantined"},
            ],
            "recommended_review_status": audit_recommended,
            "remediation_suggestions": ["Fix it"],
        }
        (aud_dir / "audit_test001.json").write_text(
            json.dumps(audit, indent=2), encoding="utf-8",
        )

    return qdir


def _create_pending_request(store: CapabilityStore, cap_id: str, **overrides) -> dict:
    """Create a pending transition request in the store."""
    qdir = store.data_dir / "quarantine" / cap_id
    req_dir = qdir / "quarantine_transition_requests"
    req_dir.mkdir(parents=True, exist_ok=True)

    req_id = f"qtr_{cap_id[:8]}"
    req = {
        "request_id": req_id,
        "capability_id": cap_id,
        "created_at": "2026-05-03T00:00:00+00:00",
        "requested_target_scope": "user",
        "requested_target_maturity": "testing",
        "status": "pending",
        "reason": "Ready for testing",
        "risk_level": "low",
        "required_approval": False,
        "findings_summary": {},
        "content_hash_at_request": "",
        "source_review_id": "review_test001",
        "source_audit_id": "audit_test001",
    }
    req.update(overrides)
    (req_dir / f"{req_id}.json").write_text(
        json.dumps(req, indent=2), encoding="utf-8",
    )
    return req


# ── Model tests ────────────────────────────────────────────────────────


class TestQuarantineActivationPlanModel:
    """Serialization, deserialization, and data integrity."""

    def test_serialize_deserialize_round_trip(self):
        plan = QuarantineActivationPlan(
            plan_id="qap_abc123",
            capability_id="my-cap",
            request_id="qtr_xyz",
            created_at="2026-05-03T00:00:00Z",
            created_by="operator",
            source_review_id="rev1",
            source_audit_id="aud1",
            target_scope="user",
            target_status="active",
            target_maturity="testing",
            allowed=True,
            required_approval=False,
            blocking_findings=[],
            evaluator_findings=[{"code": "ok", "severity": "info", "message": "Pass"}],
            copy_plan={"target_scope": "user", "total_files": 5},
            content_hash="hash1",
            risk_level="low",
            explanation="All good.",
        )
        d = plan.to_dict()
        rt = QuarantineActivationPlan.from_dict(d)
        assert rt.plan_id == plan.plan_id
        assert rt.capability_id == plan.capability_id
        assert rt.allowed is True
        assert rt.target_scope == "user"
        assert rt.required_approval is False

    def test_compact_summary(self):
        plan = QuarantineActivationPlan(
            plan_id="qap_xyz",
            capability_id="cap-x",
            request_id="qtr_y",
            created_at="2026-05-03T00:00:00Z",
            created_by="op",
            source_review_id="rev1",
            source_audit_id="aud1",
            target_scope="workspace",
            target_status="active",
            target_maturity="testing",
            allowed=False,
            required_approval=True,
            blocking_findings=[{"type": "test", "detail": "Blocked"}],
            risk_level="high",
            explanation="Blocked.",
        )
        summary = plan.compact_summary()
        assert summary["plan_id"] == "qap_xyz"
        assert summary["capability_id"] == "cap-x"
        assert summary["allowed"] is False
        assert summary["blocking_count"] == 1
        assert "explanation" not in summary

    def test_blocked_plan_has_blocking_findings(self):
        plan = QuarantineActivationPlan(
            plan_id="p1", capability_id="c1", request_id="r1",
            created_at="t", created_by=None, source_review_id=None,
            source_audit_id=None, target_scope="user",
            target_status="active", target_maturity="testing",
            allowed=False, required_approval=False,
            blocking_findings=[{"type": "no_review", "detail": "Missing"}],
            explanation="Blocked",
        )
        assert plan.allowed is False
        assert len(plan.blocking_findings) == 1
        assert plan.blocking_findings[0]["type"] == "no_review"

    def test_tool_output_strips_internal_paths(self):
        plan = QuarantineActivationPlan(
            plan_id="p1", capability_id="c1", request_id="r1",
            created_at="t", created_by=None, source_review_id=None,
            source_audit_id=None, target_scope="user",
            target_status="active", target_maturity="testing",
            allowed=True, required_approval=False,
            copy_plan={
                "_source_quarantine_dir": "quarantine/c1",
                "_target_base_dir": "user",
                "target_scope": "user",
                "total_files": 3,
            },
            explanation="OK",
        )
        output = plan.tool_output()
        copy_plan = output.get("copy_plan", {})
        assert "_source_quarantine_dir" not in copy_plan
        assert "_target_base_dir" not in copy_plan
        assert copy_plan.get("target_scope") == "user"
        assert copy_plan.get("total_files") == 3


# ── Planner gate tests ─────────────────────────────────────────────────


class TestPlanActivationAllowed:
    """Tests for allowed activation plans."""

    def test_valid_quarantined_cap_with_pending_request_creates_allowed_plan(
        self, tmp_path
    ):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-1")
        _create_pending_request(store, "test-cap-1")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-1",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["would_activate"] is False
        plan = result["plan"]
        assert plan["allowed"] is True
        assert plan["target_scope"] == "user"
        assert plan["target_status"] == "active"
        assert plan["target_maturity"] == "testing"
        assert plan["blocking_findings"] == []

    def test_persist_plan_writes_plan_json(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-2")
        _create_pending_request(store, "test-cap-2")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-2",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
            dry_run=False,
        )

        plan = result["plan"]
        plan_id = plan["plan_id"]
        plan_path = (
            store.data_dir / "quarantine" / "test-cap-2"
            / "quarantine_activation_plans" / f"{plan_id}.json"
        )
        assert plan_path.is_file()
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        assert saved["plan_id"] == plan_id
        assert saved["allowed"] is True

    def test_dry_run_writes_no_plan_file(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-3")
        _create_pending_request(store, "test-cap-3")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-3",
            evaluator=evaluator,
            policy=policy,
            dry_run=True,
        )

        plan = result["plan"]
        plan_id = plan["plan_id"]
        plans_dir = (
            store.data_dir / "quarantine" / "test-cap-3"
            / "quarantine_activation_plans"
        )
        assert not plans_dir.exists() or not list(plans_dir.glob("*.json"))

    def test_explicit_request_id(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-4")
        req = _create_pending_request(store, "test-cap-4")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-4",
            request_id=req["request_id"],
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is True
        assert result["plan"]["request_id"] == req["request_id"]

    def test_specified_target_scope_overrides_request(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-5")
        _create_pending_request(store, "test-cap-5", requested_target_scope="user")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-5",
            target_scope="workspace",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is True
        assert result["plan"]["target_scope"] == "workspace"


# ── Blocking condition tests ────────────────────────────────────────────


class TestPlanActivationBlocked:
    """Tests for blocked activation plans."""

    def test_missing_capability_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="nonexistent",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "not_found" in blocking_types

    def test_missing_request_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-no-req")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-no-req",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "no_pending_request" in blocking_types

    def test_cancelled_request_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-cancelled")
        _create_pending_request(store, "test-cap-cancelled", status="cancelled")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-cancelled",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "request_not_pending" in blocking_types or "no_pending_request" in blocking_types

    def test_rejected_request_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-rejected")
        _create_pending_request(store, "test-cap-rejected", status="rejected")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-rejected",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "request_not_pending" in blocking_types or "no_pending_request" in blocking_types

    def test_no_approved_review_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-no-review", write_review=False)
        _create_pending_request(store, "test-cap-no-review", source_review_id="")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-no-review",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert any(t in blocking_types for t in ("no_review", "review_not_found", "review_not_approved"))

    def test_review_not_approved_for_testing_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(
            store, "test-cap-bad-review", review_status="needs_changes"
        )
        _create_pending_request(store, "test-cap-bad-review")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-bad-review",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "review_not_approved" in blocking_types

    def test_no_audit_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-no-audit", write_audit=False)
        _create_pending_request(store, "test-cap-no-audit", source_audit_id="")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-no-audit",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert any(t in blocking_types for t in ("no_audit", "audit_not_found"))

    def test_failed_audit_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(
            store, "test-cap-failed-audit",
            audit_passed=False, audit_recommended="needs_changes",
        )
        _create_pending_request(store, "test-cap-failed-audit")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-failed-audit",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert any(t in blocking_types for t in ("audit_not_approved", "evaluator_failed", "policy_denied"))

    def test_status_not_quarantined_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-active", status="active")
        _create_pending_request(store, "test-cap-active")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-active",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "bad_status" in blocking_types

    def test_maturity_not_draft_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-stable", maturity="stable")
        _create_pending_request(store, "test-cap-stable")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-stable",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "bad_maturity" in blocking_types

    def test_target_scope_collision_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-collision")
        _create_pending_request(store, "test-cap-collision")

        # Create a directory already in the target scope
        user_dir = store.data_dir / "user" / "test-cap-collision"
        user_dir.mkdir(parents=True, exist_ok=True)

        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-collision",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "target_collision" in blocking_types

    def test_high_risk_requires_approval_flag(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-high-risk", risk_level="high")
        _create_pending_request(store, "test-cap-high-risk", risk_level="high")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-high-risk",
            evaluator=evaluator,
            policy=policy,
        )

        plan = result["plan"]
        if plan["allowed"]:
            assert plan["required_approval"] is True

    def test_invalid_target_scope_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-invalid-scope")
        _create_pending_request(store, "test-cap-invalid-scope")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-invalid-scope",
            target_scope="invalid",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "bad_target_scope" in blocking_types


# ── No-mutation tests ────────────────────────────────────────────────────


class TestPlanActivationNoMutation:
    """Tests that plan_quarantine_activation never mutates capability state."""

    def test_manifest_unchanged_byte_for_byte(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-cap-byte")
        _create_pending_request(store, "test-cap-byte")
        manifest_path = qdir / "manifest.json"
        before = manifest_path.read_bytes()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-byte",
            evaluator=evaluator,
            policy=policy,
        )

        after = manifest_path.read_bytes()
        assert before == after

    def test_capability_md_unchanged_byte_for_byte(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-cap-cmd")
        _create_pending_request(store, "test-cap-cmd")
        cap_md_path = qdir / "CAPABILITY.md"
        before = cap_md_path.read_bytes()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-cmd",
            evaluator=evaluator,
            policy=policy,
        )

        after = cap_md_path.read_bytes()
        assert before == after

    def test_no_active_scope_directory_created(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-no-active")
        _create_pending_request(store, "test-cap-no-active")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-no-active",
            evaluator=evaluator,
            policy=policy,
        )

        active_dir = store.data_dir / "user" / "test-cap-no-active"
        assert not active_dir.exists()

    def test_no_index_file_created_or_modified(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-no-index")
        _create_pending_request(store, "test-cap-no-index")
        index_path = store.data_dir / "capability_index.sqlite"
        index_existed_before = index_path.exists()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-no-index",
            evaluator=evaluator,
            policy=policy,
        )

        if not index_existed_before:
            assert not index_path.exists()

    def test_no_eval_record_created(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-cap-no-eval")
        _create_pending_request(store, "test-cap-no-eval")
        evals_dir = qdir / "evals"
        evals_existed = evals_dir.exists()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-no-eval",
            evaluator=evaluator,
            policy=policy,
        )

        if not evals_existed:
            assert not evals_dir.exists()

    def test_no_version_snapshot_created(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-cap-no-version")
        _create_pending_request(store, "test-cap-no-version")
        versions_dir = qdir / "versions"
        versions_existed = versions_dir.exists()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-no-version",
            evaluator=evaluator,
            policy=policy,
        )

        if not versions_existed:
            assert not versions_dir.exists()

    def test_dry_run_writes_nothing(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-dry")
        _create_pending_request(store, "test-cap-dry")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plans_dir = store.data_dir / "quarantine" / "test-cap-dry" / "quarantine_activation_plans"

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-dry",
            evaluator=evaluator,
            policy=policy,
            dry_run=True,
        )

        assert not plans_dir.exists() or not list(plans_dir.glob("*.json"))

    def test_persist_writes_only_plan_json(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-cap-persist")
        _create_pending_request(store, "test-cap-persist")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-persist",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
            dry_run=False,
        )

        plans_dir = qdir / "quarantine_activation_plans"
        assert plans_dir.is_dir()
        plan_files = list(plans_dir.glob("*.json"))
        assert len(plan_files) >= 1
        # Only plan JSON files, nothing else
        assert all(f.suffix == ".json" for f in plan_files)

    def test_blocked_plan_persisted_for_audit(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-blocked-persist", status="active")
        _create_pending_request(store, "test-cap-blocked-persist")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-blocked-persist",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
            dry_run=False,
        )

        assert result["plan"]["allowed"] is False
        plans_dir = store.data_dir / "quarantine" / "test-cap-blocked-persist" / "quarantine_activation_plans"
        plan_files = list(plans_dir.glob("*.json"))
        assert len(plan_files) >= 1


# ── Path safety tests ────────────────────────────────────────────────────


class TestPlanActivationPathSafety:
    """Path traversal and ID validation."""

    def test_path_traversal_capability_id_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        with pytest.raises(CapabilityError, match="Invalid identifier"):
            plan_quarantine_activation(
                store_data_dir=store.data_dir,
                capability_id="../../../etc/passwd",
                evaluator=evaluator,
                policy=policy,
            )

    def test_slash_in_capability_id_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        with pytest.raises(CapabilityError, match="Invalid identifier"):
            plan_quarantine_activation(
                store_data_dir=store.data_dir,
                capability_id="foo/bar",
                evaluator=evaluator,
                policy=policy,
            )

    def test_backslash_in_capability_id_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        with pytest.raises(CapabilityError, match="Invalid identifier"):
            plan_quarantine_activation(
                store_data_dir=store.data_dir,
                capability_id="foo\\bar",
                evaluator=evaluator,
                policy=policy,
            )


# ── List/view tests ──────────────────────────────────────────────────────


class TestListAndViewActivationPlans:
    """Read-only list and view operations."""

    def test_list_returns_empty_for_empty_dir(self, tmp_path):
        store = _make_store(tmp_path)
        results = list_quarantine_activation_plans(
            store_data_dir=store.data_dir,
        )
        assert results == []

    def test_list_returns_plan_after_persist(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-list")
        _create_pending_request(store, "test-cap-list")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-list",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        results = list_quarantine_activation_plans(
            store_data_dir=store.data_dir,
            capability_id="test-cap-list",
        )
        assert len(results) >= 1
        assert results[0]["capability_id"] == "test-cap-list"

    def test_view_returns_full_plan(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-view")
        _create_pending_request(store, "test-cap-view")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-view",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        plan_id = result["plan"]["plan_id"]
        view = view_quarantine_activation_plan(
            store_data_dir=store.data_dir,
            capability_id="test-cap-view",
            plan_id=plan_id,
        )
        assert view["plan_id"] == plan_id
        assert view["capability_id"] == "test-cap-view"

    def test_view_strips_internal_paths(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-cap-view2")
        _create_pending_request(store, "test-cap-view2")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-view2",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        plan_id = result["plan"]["plan_id"]
        view = view_quarantine_activation_plan(
            store_data_dir=store.data_dir,
            capability_id="test-cap-view2",
            plan_id=plan_id,
        )
        copy_plan = view.get("copy_plan", {})
        assert "_source_quarantine_dir" not in copy_plan
        assert "_target_base_dir" not in copy_plan
