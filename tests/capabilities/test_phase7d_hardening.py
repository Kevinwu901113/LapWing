"""Phase 7D-A hardening tests: authority-proof, corruption handling, quarantine isolation,
file-level immutability, permission matrix gaps, edge cases."""

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


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _create_quarantine_capability(
    store: CapabilityStore,
    cap_id: str,
    *,
    write_review: bool = True,
    write_audit: bool = True,
    review_status: str = "approved_for_testing",
    audit_passed: bool = True,
    audit_recommended: str = "approved_for_testing",
    status: str = "quarantined",
    maturity: str = "draft",
    risk_level: str = "low",
    with_scripts: bool = False,
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
        "triggers": [],
        "tags": [],
        "trust_required": "developer",
        "required_tools": [],
        "required_permissions": [],
    }
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nTest.\n\n"
        "## Procedure\n1. Test\n\n"
        "## Verification\nPass.\n\n"
        "## Failure handling\nRetry."
    )
    (qdir / "CAPABILITY.md").write_text(md, encoding="utf-8")
    (qdir / "manifest.json").write_text(json.dumps(fm, indent=2), encoding="utf-8")

    import_report = {
        "capability_id": cap_id,
        "source_type": "local_package",
        "imported_at": "2026-05-01T00:00:00+00:00",
        "target_scope": "user",
        "eval_passed": True,
        "eval_score": 1.0,
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
            "findings": [],
            "recommended_review_status": audit_recommended,
            "remediation_suggestions": [],
        }
        (aud_dir / "audit_test001.json").write_text(
            json.dumps(audit, indent=2), encoding="utf-8",
        )

    if with_scripts:
        scripts_dir = qdir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "run.py").write_text("print('hello')")

    req_dir = qdir / "quarantine_transition_requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    req = {
        "request_id": "qtr_test001",
        "capability_id": cap_id,
        "created_at": "2026-05-03T00:00:00+00:00",
        "requested_target_scope": "user",
        "requested_target_maturity": "testing",
        "status": "pending",
        "reason": "Ready",
        "risk_level": risk_level,
        "required_approval": False,
        "source_review_id": "review_test001",
        "source_audit_id": "audit_test001",
    }
    (req_dir / "qtr_test001.json").write_text(
        json.dumps(req, indent=2), encoding="utf-8",
    )

    return qdir


# ── Extended blocking conditions ────────────────────────────────────────


class TestExtendedBlockingConditions:
    """Gate conditions not covered by the base planner test file."""

    def test_superseded_request_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        cap_id = "test-hard-superseded"
        _create_quarantine_capability(store, cap_id)
        qdir = store.data_dir / "quarantine" / cap_id
        req_dir = qdir / "quarantine_transition_requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        req = {
            "request_id": "qtr_superseded",
            "capability_id": cap_id,
            "created_at": "2026-05-03T10:00:00+00:00",
            "requested_target_scope": "user",
            "requested_target_maturity": "testing",
            "status": "superseded",
            "reason": "Was pending",
            "risk_level": "low",
            "required_approval": False,
            "source_review_id": "review_test001",
            "source_audit_id": "audit_test001",
        }
        (req_dir / "qtr_superseded.json").write_text(json.dumps(req, indent=2))
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            request_id="qtr_superseded",
            evaluator=evaluator,
            policy=policy,
        )
        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "request_not_pending" in blocking_types

    def test_request_target_maturity_not_testing_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        cap_id = "test-hard-bad-maturity"
        _create_quarantine_capability(store, cap_id)
        qdir = store.data_dir / "quarantine" / cap_id
        req_dir = qdir / "quarantine_transition_requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        req = {
            "request_id": "qtr_bad_mat",
            "capability_id": cap_id,
            "created_at": "2026-05-03T10:00:00+00:00",
            "requested_target_scope": "user",
            "requested_target_maturity": "stable",
            "status": "pending",
            "reason": "Wrong maturity",
            "risk_level": "low",
            "required_approval": False,
        }
        (req_dir / "qtr_bad_mat.json").write_text(json.dumps(req, indent=2))
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            request_id="qtr_bad_mat",
            evaluator=evaluator,
            policy=policy,
        )
        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "bad_target_maturity" in blocking_types

    def test_review_rejected_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-rev-reject", review_status="rejected")
        qdir = store.data_dir / "quarantine" / "test-hard-rev-reject"
        req_dir = qdir / "quarantine_transition_requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        req = {
            "request_id": "qtr_rev_reject",
            "capability_id": "test-hard-rev-reject",
            "created_at": "2026-05-03T00:00:00+00:00",
            "requested_target_scope": "user",
            "requested_target_maturity": "testing",
            "status": "pending",
            "reason": "Test",
            "risk_level": "low",
            "required_approval": False,
            "source_review_id": "review_test001",
            "source_audit_id": "audit_test001",
        }
        (req_dir / "qtr_rev_reject.json").write_text(json.dumps(req, indent=2))
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-rev-reject",
            request_id="qtr_rev_reject",
            evaluator=evaluator,
            policy=policy,
        )
        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "review_not_approved" in blocking_types

    def test_specified_request_id_not_found_blocked(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-req-not-found")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-req-not-found",
            request_id="nonexistent_request",
            evaluator=evaluator,
            policy=policy,
        )
        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "request_not_found" in blocking_types


# ── File-level immutability ─────────────────────────────────────────────


class TestFileImmutability:
    """All files besides the plan JSON remain unchanged."""

    def test_import_report_unchanged(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-hard-import-unchanged")
        imp_path = qdir / "import_report.json"
        before = imp_path.read_bytes()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-import-unchanged",
            evaluator=evaluator,
            policy=policy,
        )

        assert imp_path.read_bytes() == before

    def test_review_files_unchanged(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-hard-review-unchanged")
        rev_file = qdir / "quarantine_reviews" / "review_test001.json"
        before = rev_file.read_bytes()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-review-unchanged",
            evaluator=evaluator,
            policy=policy,
        )

        assert rev_file.read_bytes() == before

    def test_audit_files_unchanged(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-hard-audit-unchanged")
        aud_file = qdir / "quarantine_audit_reports" / "audit_test001.json"
        before = aud_file.read_bytes()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-audit-unchanged",
            evaluator=evaluator,
            policy=policy,
        )

        assert aud_file.read_bytes() == before

    def test_request_files_unchanged(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-hard-req-unchanged")
        req_file = qdir / "quarantine_transition_requests" / "qtr_test001.json"
        before = req_file.read_bytes()
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-req-unchanged",
            evaluator=evaluator,
            policy=policy,
        )

        assert req_file.read_bytes() == before

    def test_all_non_plan_files_unchanged_byte_for_byte(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-hard-all-unchanged", with_scripts=True)
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        # Record all files before
        before_hashes = {}
        for f in sorted(qdir.rglob("*")):
            if f.is_file():
                before_hashes[str(f.relative_to(qdir))] = f.read_bytes()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-all-unchanged",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
            dry_run=False,
        )

        # Verify all pre-existing files unchanged
        for rel_path, before_bytes in before_hashes.items():
            current = qdir / rel_path
            assert current.is_file(), f"File deleted: {rel_path}"
            assert current.read_bytes() == before_bytes, f"File modified: {rel_path}"

        # Only new files are plan JSONs
        plans_dir = qdir / "quarantine_activation_plans"
        new_files = set(
            str(f.relative_to(qdir)) for f in qdir.rglob("*") if f.is_file()
        ) - set(before_hashes.keys())
        for nf in new_files:
            assert nf.startswith("quarantine_activation_plans/"), f"Unexpected new file: {nf}"
            assert nf.endswith(".json"), f"Unexpected file type: {nf}"


# ── Plan not authority tests ────────────────────────────────────────────


class TestPlanNotAuthority:
    """Allowed plan must not confer approval, permissions, or bypass lifecycle."""

    def test_allowed_plan_does_not_set_any_mutation_fields(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-not-auth")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-not-auth",
            evaluator=evaluator,
            policy=policy,
        )

        plan = result["plan"]
        assert plan["allowed"] is True
        assert result["would_activate"] is False
        # Plan fields that would indicate mutation/authority
        assert "activated_at" not in plan
        assert plan.get("target_status") == "active"  # informational only
        # Plan must not contain review/approval-like fields
        for key in plan:
            assert "_approved" not in key, f"Plan has approval field: {key}"
            assert "_activated" not in key, f"Plan has activation field: {key}"

    def test_plan_id_cannot_be_used_as_lifecycle_authority(self, tmp_path):
        """Verify plan_id is not accepted by lifecycle manager."""
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-plan-id-not-auth")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-plan-id-not-auth",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        plan_id = result["plan"]["plan_id"]
        # Plan ID is a string with "qap_" prefix — not a capability state change
        assert plan_id.startswith("qap_")
        # Verify no lifecycle transition was triggered
        manifest = json.loads(
            (store.data_dir / "quarantine" / "test-hard-plan-id-not-auth" / "manifest.json")
            .read_text(encoding="utf-8")
        )
        assert manifest["status"] == "quarantined"
        assert manifest["maturity"] == "draft"

    def test_required_approval_does_not_equal_approval(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-high-risk", risk_level="high")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-high-risk",
            evaluator=evaluator,
            policy=policy,
        )

        plan = result["plan"]
        if plan["allowed"]:
            assert plan["required_approval"] is True
            # But no capability state change
            assert result["would_activate"] is False
            manifest = json.loads(
                (store.data_dir / "quarantine" / "test-hard-high-risk" / "manifest.json")
                .read_text(encoding="utf-8")
            )
            assert manifest["status"] == "quarantined"

    def test_allowed_plan_does_not_modify_request_status(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-req-status")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-req-status",
            evaluator=evaluator,
            policy=policy,
        )

        # Request must still be pending
        req = json.loads(
            (store.data_dir / "quarantine" / "test-hard-req-status"
             / "quarantine_transition_requests" / "qtr_test001.json")
            .read_text(encoding="utf-8")
        )
        assert req["status"] == "pending"


# ── Corrupt data handling ───────────────────────────────────────────────


class TestCorruptDataHandling:
    """Graceful handling of corrupt JSON data."""

    def test_corrupt_manifest_handled(self, tmp_path):
        store = _make_store(tmp_path)
        cap_id = "test-hard-corrupt-manifest"
        qdir = store.data_dir / "quarantine" / cap_id
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / "manifest.json").write_text("not valid json{{{")
        (qdir / "CAPABILITY.md").write_text("---\nid: test\n---\ntest\n")
        # Need a real manifest for the CapabilityParser, so just skip the file
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            evaluator=evaluator,
            policy=policy,
        )
        # Should fail gracefully, not crash
        assert "plan" in result
        assert result["plan"]["allowed"] is False
        blocking_types = [f["type"] for f in result["plan"]["blocking_findings"]]
        assert "no_manifest" in blocking_types

    def test_corrupt_request_file_handled(self, tmp_path):
        store = _make_store(tmp_path)
        cap_id = "test-hard-corrupt-req"
        _create_quarantine_capability(store, cap_id)
        req_dir = store.data_dir / "quarantine" / cap_id / "quarantine_transition_requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        (req_dir / "qtr_corrupt.json").write_text("garbage{{{[")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            request_id="qtr_corrupt",
            evaluator=evaluator,
            policy=policy,
        )
        assert "plan" in result

    def test_corrupt_review_file_gate_handled(self, tmp_path):
        store = _make_store(tmp_path)
        cap_id = "test-hard-corrupt-review"
        _create_quarantine_capability(store, cap_id)
        rev_dir = store.data_dir / "quarantine" / cap_id / "quarantine_reviews"
        rev_dir.mkdir(parents=True, exist_ok=True)
        (rev_dir / "review_test001.json").write_text("corrupt{{{[")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        # Should not crash
        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            evaluator=evaluator,
            policy=policy,
        )
        assert "plan" in result

    def test_corrupt_audit_file_gate_handled(self, tmp_path):
        store = _make_store(tmp_path)
        cap_id = "test-hard-corrupt-audit"
        _create_quarantine_capability(store, cap_id)
        aud_dir = store.data_dir / "quarantine" / cap_id / "quarantine_audit_reports"
        aud_dir.mkdir(parents=True, exist_ok=True)
        (aud_dir / "audit_test001.json").write_text("corrupt{{{[")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            evaluator=evaluator,
            policy=policy,
        )
        assert "plan" in result

    def test_plan_id_is_path_safe(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-plan-id-safe")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-plan-id-safe",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        plan_id = result["plan"]["plan_id"]
        assert "/" not in plan_id
        assert "\\" not in plan_id
        assert ".." not in plan_id
        assert plan_id.startswith("qap_")

    def test_duplicate_plan_ids_unique(self, tmp_path):
        """Verify each call produces a unique plan_id."""
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-unique-id")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        ids = set()
        for _ in range(5):
            result = plan_quarantine_activation(
                store_data_dir=store.data_dir,
                capability_id="test-hard-unique-id",
                evaluator=evaluator,
                policy=policy,
                persist_plan=True,
            )
            ids.add(result["plan"]["plan_id"])

        assert len(ids) == 5


# ── Quarantine isolation ────────────────────────────────────────────────


class TestQuarantineIsolation:
    """Quarantined capability remains excluded from active retrieval after planning."""

    def test_capability_not_in_active_store_list_after_plan(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-isolate-list")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-isolate-list",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        active = store.list()
        active_ids = [d.get("id") or d.get("capability_id") for d in active]
        assert "test-hard-isolate-list" not in active_ids

    def test_capability_not_in_active_index_after_plan(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-isolate-index")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-isolate-index",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        # Verify no active scope directories were created
        for scope in ("user", "workspace", "global", "session"):
            scope_dir = store.data_dir / scope / "test-hard-isolate-index"
            assert not scope_dir.is_dir(), f"Capability leaked into {scope} scope"

    def test_capability_stays_in_quarantine_directory(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-hard-stay-quarantine")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-stay-quarantine",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        # Only plan under quarantine/<id>, no copies elsewhere
        quarantine_dir = store.data_dir / "quarantine" / "test-hard-stay-quarantine"
        assert quarantine_dir.is_dir()
        for scope in ("user", "workspace", "global", "session"):
            scope_dir = store.data_dir / scope / "test-hard-stay-quarantine"
            assert not scope_dir.is_dir(), f"Files leaked into {scope} scope"

    def test_allowed_plan_does_not_make_capability_retrievable(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-not-retrievable")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-not-retrievable",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
        )

        assert result["plan"]["allowed"] is True
        # Verify active store doesn't have it
        active = store.list(include_disabled=True, include_archived=True)
        active_ids = [
            d.get("id") or d.get("capability_id")
            for d in active
        ]
        assert "test-hard-not-retrievable" not in active_ids


# ── Writes stay under quarantine ────────────────────────────────────────


class TestWritesStayUnderQuarantine:
    """Plan writes are strictly confined to quarantine/<id>/quarantine_activation_plans/."""

    def test_only_plan_dir_created_under_quarantine(self, tmp_path):
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "test-hard-plan-dir")
        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        # Record directories before
        before_dirs = set(
            str(d.relative_to(store.data_dir))
            for d in store.data_dir.rglob("*") if d.is_dir()
        )

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-plan-dir",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
            dry_run=False,
        )

        after_dirs = set(
            str(d.relative_to(store.data_dir))
            for d in store.data_dir.rglob("*") if d.is_dir()
        )
        new_dirs = after_dirs - before_dirs
        for nd in new_dirs:
            assert "quarantine_activation_plans" in nd or nd.startswith(
                "quarantine/test-hard-plan-dir/quarantine_activation_plans"
            ), f"Unexpected new directory: {nd}"

    def test_no_files_written_outside_quarantine(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "test-hard-no-outside")

        # Record files before
        before_files = set()
        for f in store.data_dir.rglob("*"):
            if f.is_file():
                before_files.add(str(f.relative_to(store.data_dir)))

        evaluator = CapabilityEvaluator()
        policy = CapabilityPolicy()

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-hard-no-outside",
            evaluator=evaluator,
            policy=policy,
            persist_plan=True,
            dry_run=False,
        )

        after_files = set()
        for f in store.data_dir.rglob("*"):
            if f.is_file():
                after_files.add(str(f.relative_to(store.data_dir)))

        new_files = after_files - before_files
        for nf in new_files:
            assert "quarantine/" in nf, f"File written outside quarantine: {nf}"
            assert "quarantine_activation_plans" in nf, f"Unexpected file: {nf}"
