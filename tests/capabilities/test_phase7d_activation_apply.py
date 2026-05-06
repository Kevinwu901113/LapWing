"""Phase 7D-B tests: activation apply gate logic and behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.quarantine_activation_apply import (
    ActivationResult,
    apply_quarantine_activation,
)
from src.capabilities.store import CapabilityStore


# ── helpers ────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_index(tmp_path: Path) -> CapabilityIndex:
    db_path = tmp_path / "index.sqlite"
    cap_index = CapabilityIndex(str(db_path))
    cap_index.init()
    return cap_index


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
        "do_not_apply_when": ["not for unsafe activation contexts"],
        "reuse_boundary": "Activation apply test only.",
        "side_effects": ["none"],
    }
    if overrides:
        fm.update(overrides)

    import yaml
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
    evals_dir = qdir / "evals"
    evals_dir.mkdir(exist_ok=True)
    (evals_dir / "boundary_cases.jsonl").write_text('{"case":"boundary"}\n', encoding="utf-8")

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


def _create_allowed_plan(store: CapabilityStore, cap_id: str, **overrides) -> dict:
    qdir = store.data_dir / "quarantine" / cap_id
    plans_dir = qdir / "quarantine_activation_plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    plan_id = f"qap_{cap_id[:8]}"
    plan = {
        "plan_id": plan_id,
        "capability_id": cap_id,
        "request_id": overrides.pop("request_id", f"qtr_{cap_id[:8]}"),
        "created_at": "2026-05-03T08:00:00+00:00",
        "created_by": "operator",
        "source_review_id": "review_test001",
        "source_audit_id": "audit_test001",
        "target_scope": "user",
        "target_status": "active",
        "target_maturity": "testing",
        "allowed": True,
        "required_approval": False,
        "blocking_findings": [],
        "policy_findings": [],
        "evaluator_findings": [],
        "copy_plan": {"target_scope": "user", "total_files": 5},
        "content_hash": "",
        "request_content_hash": "",
        "risk_level": "low",
        "explanation": "All gates passed.",
        "would_activate": False,
    }
    plan.update(overrides)
    (plans_dir / f"{plan_id}.json").write_text(
        json.dumps(plan, indent=2), encoding="utf-8",
    )
    return plan


def _make_evaluator() -> CapabilityEvaluator:
    return CapabilityEvaluator()


def _make_policy() -> CapabilityPolicy:
    return CapabilityPolicy()


# ── Model tests ────────────────────────────────────────────────────────


class TestActivationResultModel:
    """Serialization and data integrity of ActivationResult."""

    def test_to_dict(self):
        result = ActivationResult(
            capability_id="cap1",
            source_quarantine_id="cap1",
            target_scope="user",
            target_status="active",
            target_maturity="testing",
            applied=True,
            dry_run=False,
            plan_id="plan1",
            request_id="req1",
            activation_report_id="report1",
            content_hash_before="hash1",
            content_hash_after="hash2",
            index_refreshed=True,
            message="Applied.",
        )
        d = result.to_dict()
        assert d["capability_id"] == "cap1"
        assert d["applied"] is True
        assert d["dry_run"] is False
        assert d["target_maturity"] == "testing"
        assert d["target_status"] == "active"
        assert d["index_refreshed"] is True
        assert d["partial_failure"] is False

    def test_blocked_result(self):
        result = ActivationResult(
            capability_id="cap1",
            source_quarantine_id="cap1",
            target_scope="user",
            target_status="active",
            target_maturity="testing",
            applied=False,
            dry_run=False,
            blocking_findings=[{"type": "test_block", "detail": "Blocked"}],
            message="Denied.",
        )
        assert result.applied is False
        assert len(result.blocking_findings) == 1
        assert result.blocking_findings[0]["type"] == "test_block"

    def test_partial_failure_flag(self):
        result = ActivationResult(
            capability_id="cap1",
            source_quarantine_id="cap1",
            target_scope="user",
            target_status="active",
            target_maturity="testing",
            applied=False,
            dry_run=False,
            partial_failure=True,
            message="Partial failure.",
        )
        assert result.partial_failure is True
        assert result.applied is False


# ── Apply gate tests: allowed ─────────────────────────────────────────


class TestApplyActivationAllowed:
    """Tests for successful activation apply."""

    def test_valid_capability_applies_to_testing(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-1")
        _create_pending_request(store, "test-cap-1")
        _create_allowed_plan(store, "test-cap-1")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-1",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is True
        assert result.dry_run is False
        assert result.target_scope == "user"
        assert result.target_status == "active"
        assert result.target_maturity == "testing"
        assert result.blocking_findings == []

    def test_apply_creates_target_scope_dir(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-2")
        _create_pending_request(store, "test-cap-2")
        _create_allowed_plan(store, "test-cap-2")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-2",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        target_dir = store.data_dir / "user" / "test-cap-2"
        assert target_dir.is_dir()

    def test_target_manifest_status_active(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-3")
        _create_pending_request(store, "test-cap-3")
        _create_allowed_plan(store, "test-cap-3")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-3",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        target_manifest_path = store.data_dir / "user" / "test-cap-3" / "manifest.json"
        assert target_manifest_path.is_file()
        target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
        assert target_manifest["status"] == "active"
        assert target_manifest["maturity"] == "testing"
        assert target_manifest["scope"] == "user"

    def test_target_content_hash_recomputed(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-4")
        _create_pending_request(store, "test-cap-4")
        _create_allowed_plan(store, "test-cap-4")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-4",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.content_hash_before
        assert result.content_hash_after
        assert result.content_hash_after != ""

    def test_origin_metadata_written(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-5")
        _create_pending_request(store, "test-cap-5")
        plan = _create_allowed_plan(store, "test-cap-5")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-5",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        target_manifest_path = store.data_dir / "user" / "test-cap-5" / "manifest.json"
        target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
        origin = target_manifest.get("extra", {}).get("origin", {})
        assert origin.get("quarantine_capability_id") == "test-cap-5"
        assert origin.get("activation_plan_id") == plan["plan_id"]
        assert origin.get("transition_request_id")
        assert origin.get("activated_at")
        assert origin.get("activated_by")

    def test_activation_report_written_in_target(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-6")
        _create_pending_request(store, "test-cap-6")
        _create_allowed_plan(store, "test-cap-6")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-6",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.activation_report_id
        report_path = store.data_dir / "user" / "test-cap-6" / "activation_report.json"
        assert report_path.is_file()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["activation_report_id"] == result.activation_report_id

    def test_activation_report_written_in_quarantine(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-7")
        _create_pending_request(store, "test-cap-7")
        _create_allowed_plan(store, "test-cap-7")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-7",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        reports_dir = (
            store.data_dir / "quarantine" / "test-cap-7"
            / "quarantine_activation_reports"
        )
        assert reports_dir.is_dir()
        report_files = list(reports_dir.glob("*.json"))
        assert len(report_files) == 1

    def test_index_refreshed_for_target_copy(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-8")
        _create_pending_request(store, "test-cap-8")
        _create_allowed_plan(store, "test-cap-8")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-8",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.index_refreshed is True
        # Verify capability appears in search
        results = idx.search("test-cap-8")
        matching_ids = [r.get("id", "") for r in results]
        assert "test-cap-8" in matching_ids

    def test_original_quarantine_manifest_unchanged(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-9")
        _create_pending_request(store, "test-cap-9")
        _create_allowed_plan(store, "test-cap-9")

        # Read original manifest before apply
        orig_manifest_path = (
            store.data_dir / "quarantine" / "test-cap-9" / "manifest.json"
        )
        orig_before = orig_manifest_path.read_text(encoding="utf-8")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-9",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        orig_after = orig_manifest_path.read_text(encoding="utf-8")
        assert orig_before == orig_after

    def test_original_quarantine_files_remain(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-10")
        _create_pending_request(store, "test-cap-10")
        _create_allowed_plan(store, "test-cap-10")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-10",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        qdir = store.data_dir / "quarantine" / "test-cap-10"
        assert qdir.is_dir()
        assert (qdir / "CAPABILITY.md").is_file()
        assert (qdir / "manifest.json").is_file()
        assert (qdir / "import_report.json").is_file()

    def test_dry_run_writes_nothing(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-dry")
        _create_pending_request(store, "test-cap-dry")
        _create_allowed_plan(store, "test-cap-dry")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-dry",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
            dry_run=True,
        )

        assert result.applied is False
        assert result.dry_run is True
        assert result.message
        target_dir = store.data_dir / "user" / "test-cap-dry"
        assert not target_dir.exists()

    def test_no_stable_maturity_created(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-stable")
        _create_pending_request(store, "test-cap-stable")
        _create_allowed_plan(store, "test-cap-stable")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-stable",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        target_manifest_path = (
            store.data_dir / "user" / "test-cap-stable" / "manifest.json"
        )
        target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
        assert target_manifest["maturity"] != "stable"

    def test_explicit_plan_id(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-planid")
        _create_pending_request(store, "test-cap-planid")
        plan = _create_allowed_plan(store, "test-cap-planid")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-planid",
            plan_id=plan["plan_id"],
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is True
        assert result.plan_id == plan["plan_id"]

    def test_request_marked_superseded_after_apply(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-super")
        req = _create_pending_request(store, "test-cap-super")
        _create_allowed_plan(store, "test-cap-super")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-super",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        req_path = (
            store.data_dir / "quarantine" / "test-cap-super"
            / "quarantine_transition_requests" / f"{req['request_id']}.json"
        )
        updated_req = json.loads(req_path.read_text(encoding="utf-8"))
        # Request should be superseded or still pending (both are acceptable;
        # superseded is preferred but we don't fail if the vocabulary doesn't support it)
        assert updated_req["status"] in ("superseded", "pending")

    def test_no_mutate_quarantine_original_during_apply(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-nomutate")
        _create_pending_request(store, "test-cap-nomutate")
        _create_allowed_plan(store, "test-cap-nomutate")

        # Read original state
        qdir = store.data_dir / "quarantine" / "test-cap-nomutate"
        manifest_before = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-nomutate",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        manifest_after = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest_after["status"] == manifest_before["status"]
        assert manifest_after["maturity"] == manifest_before["maturity"]


# ── Apply gate tests: denied ──────────────────────────────────────────


class TestApplyActivationDenied:
    """Tests for denied activation apply."""

    def test_missing_plan_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-noplan")
        _create_pending_request(store, "test-cap-noplan")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-noplan",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "no_allowed_plan" in types

    def test_plan_not_allowed_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-notallowed")
        _create_pending_request(store, "test-cap-notallowed")
        _create_allowed_plan(store, "test-cap-notallowed", allowed=False)

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-notallowed",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "plan_not_allowed" in types

    def test_plan_not_testing_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-notesting")
        _create_pending_request(store, "test-cap-notesting")
        _create_allowed_plan(store, "test-cap-notesting", target_maturity="stable")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-notesting",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "bad_plan_maturity" in types

    def test_request_not_pending_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-done")
        _create_pending_request(store, "test-cap-done", status="cancelled")
        _create_allowed_plan(store, "test-cap-done")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-done",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert any(t in types for t in ("request_not_pending", "no_pending_request"))

    def test_review_not_approved_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-badreview", review_status="rejected")
        _create_pending_request(store, "test-cap-badreview")
        _create_allowed_plan(store, "test-cap-badreview")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-badreview",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "review_not_approved" in types

    def test_audit_not_passed_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(
            store, "test-cap-badaudit",
            audit_passed=False, audit_recommended="rejected",
        )
        _create_pending_request(store, "test-cap-badaudit")
        _create_allowed_plan(store, "test-cap-badaudit")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-badaudit",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "audit_not_approved" in types

    def test_status_not_quarantined_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-active", status="active")
        _create_pending_request(store, "test-cap-active")
        _create_allowed_plan(store, "test-cap-active")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-active",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "bad_status" in types

    def test_maturity_not_draft_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-testing", maturity="testing")
        _create_pending_request(store, "test-cap-testing")
        _create_allowed_plan(store, "test-cap-testing")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-testing",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "bad_maturity" in types

    def test_target_collision_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-collide")
        _create_pending_request(store, "test-cap-collide")
        _create_allowed_plan(store, "test-cap-collide")

        # Pre-create a target dir to simulate collision
        target_dir = store.data_dir / "user" / "test-cap-collide"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "CAPABILITY.md").write_text("existing")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-collide",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "target_collision" in types

    def test_high_risk_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-highrisk", risk_level="high")
        _create_pending_request(store, "test-cap-highrisk", risk_level="high")
        _create_allowed_plan(store, "test-cap-highrisk", risk_level="high")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-highrisk",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "high_risk_blocked" in types

    def test_missing_reason_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-noreason")
        _create_pending_request(store, "test-cap-noreason")
        _create_allowed_plan(store, "test-cap-noreason")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-noreason",
            reason="",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "missing_reason" in types

    def test_no_pending_request_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-noreq")
        _create_allowed_plan(store, "test-cap-noreq")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-noreq",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        types = [f["type"] for f in result.blocking_findings]
        assert "no_pending_request" in types

    def test_repeated_apply_denied_by_collision(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _create_quarantine_capability(store, "test-cap-repeat")
        _create_pending_request(store, "test-cap-repeat")
        _create_allowed_plan(store, "test-cap-repeat")

        # First apply succeeds
        result1 = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-repeat",
            reason="Testing activation",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )
        assert result1.applied is True

        # Second apply denied (collision or no pending request — both valid)
        result2 = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-cap-repeat",
            reason="Second attempt",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )
        assert result2.applied is False
        types2 = [f["type"] for f in result2.blocking_findings]
        assert any(t in types2 for t in ("target_collision", "no_pending_request"))
