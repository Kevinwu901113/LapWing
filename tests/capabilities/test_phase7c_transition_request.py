"""Phase 7C tests: transition request model, serialization, path safety, storage."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import yaml

from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.quarantine_transition import (
    ALLOWED_TARGET_SCOPES,
    QuarantineTransitionRequest,
    cancel_quarantine_transition_request,
    list_quarantine_transition_requests,
    request_quarantine_testing_transition,
    view_quarantine_transition_request,
)
from src.capabilities.store import CapabilityStore


# ── helpers ────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _create_quarantine_dir(
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
        "do_not_apply_when": ["not for unsafe transition contexts"],
        "reuse_boundary": "Quarantine transition request test only.",
        "side_effects": ["none"],
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


# ── Model tests ────────────────────────────────────────────────────────


class TestQuarantineTransitionRequestModel:
    """Serialization, deserialization, and data integrity."""

    def test_serialize_deserialize_round_trip(self):
        req = QuarantineTransitionRequest(
            request_id="qtr_abc123",
            capability_id="my-cap",
            created_at="2026-05-03T00:00:00Z",
            requested_target_scope="user",
            requested_target_maturity="testing",
            status="pending",
            reason="Ready for testing",
            risk_level="low",
            required_approval=False,
        )
        d = req.to_dict()
        rt = QuarantineTransitionRequest.from_dict(d)
        assert rt.request_id == req.request_id
        assert rt.capability_id == req.capability_id
        assert rt.status == "pending"
        assert rt.requested_target_scope == "user"
        assert rt.required_approval is False

    def test_compact_summary_excludes_internal_fields(self):
        req = QuarantineTransitionRequest(
            request_id="qtr_xyz",
            capability_id="cap-x",
            created_at="2026-05-03T00:00:00Z",
            requested_target_scope="workspace",
            requested_target_maturity="testing",
            status="pending",
            reason="Test",
            risk_level="medium",
            required_approval=False,
            findings_summary={"passed": True},
            content_hash_at_request="hash-1",
            metadata={"extra": "data"},
        )
        summary = req.compact_summary()
        assert "findings_summary" not in summary
        assert "content_hash_at_request" not in summary
        assert "metadata" not in summary
        assert summary["request_id"] == "qtr_xyz"
        assert summary["capability_id"] == "cap-x"

    def test_to_dict_includes_all_fields(self):
        req = QuarantineTransitionRequest(
            request_id="r1",
            capability_id="c1",
            created_at="t",
            source_review_id="rev1",
            source_audit_id="aud1",
            requested_target_scope="user",
            requested_target_maturity="testing",
            status="pending",
            reason="r",
            risk_level="low",
            required_approval=False,
            findings_summary={"p": True},
            content_hash_at_request="h",
            created_by="op",
            metadata={"k": "v"},
        )
        d = req.to_dict()
        assert d["source_review_id"] == "rev1"
        assert d["source_audit_id"] == "aud1"
        assert d["findings_summary"] == {"p": True}
        assert d["metadata"] == {"k": "v"}


# ── Path safety tests ──────────────────────────────────────────────────


class TestPathSafety:
    """request_id and capability_id path traversal rejection."""

    def test_capability_id_path_traversal_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(CapabilityError, match="Invalid identifier"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="../../../etc/passwd",
                reason="test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_capability_id_slash_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(CapabilityError, match="Invalid identifier"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="foo/bar",
                reason="test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_request_id_path_traversal_rejected_in_view(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(CapabilityError, match="Invalid identifier"):
            view_quarantine_transition_request(
                store_data_dir=store.data_dir,
                capability_id="test-cap",
                request_id="../../../etc/passwd",
            )

    def test_request_id_path_traversal_rejected_in_cancel(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(CapabilityError, match="Invalid identifier"):
            cancel_quarantine_transition_request(
                store_data_dir=store.data_dir,
                capability_id="test-cap",
                request_id="../../../etc/passwd",
                reason="test",
            )


# ── Storage tests ──────────────────────────────────────────────────────


class TestRequestStorage:
    """File writes only under quarantine/<id>/quarantine_transition_requests/."""

    def test_writes_under_requests_dir(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "valid-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="valid-cap",
            reason="Ready",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        assert result["would_create"] is True

        req_data = result["request"]
        req_id = req_data["request_id"]

        rpath = (
            store.data_dir / "quarantine" / "valid-cap"
            / "quarantine_transition_requests" / f"{req_id}.json"
        )
        assert rpath.is_file()

        # Verify content
        written = json.loads(rpath.read_text())
        assert written["capability_id"] == "valid-cap"
        assert written["status"] == "pending"

    def test_duplicate_pending_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "dup-cap")

        result1 = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="dup-cap",
            reason="First request",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        assert result1["would_create"] is True

        # Second request for same cap + same scope should fail
        with pytest.raises(CapabilityError, match="already exists"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="dup-cap",
                reason="Second request",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_different_scope_allows_new_request(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "multi-scope-cap")

        result1 = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="multi-scope-cap",
            requested_target_scope="user",
            reason="User scope",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        assert result1["would_create"] is True

        # Different scope should be allowed (different pending check)
        result2 = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="multi-scope-cap",
            requested_target_scope="workspace",
            reason="Workspace scope",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        assert result2["would_create"] is True

    def test_cancelled_allows_new_request(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "cancel-cap")

        result1 = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="cancel-cap",
            reason="First request",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result1["request"]["request_id"]

        cancel_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="cancel-cap",
            request_id=req_id,
            reason="No longer needed",
        )

        # New request after cancellation should be allowed
        result2 = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="cancel-cap",
            reason="Second request",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        assert result2["would_create"] is True

    def test_dry_run_writes_nothing(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "dry-cap")

        rdir = store.data_dir / "quarantine" / "dry-cap" / "quarantine_transition_requests"

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="dry-cap",
            reason="Dry test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
            dry_run=True,
        )
        assert result["would_create"] is True
        assert "request_preview" in result
        assert not rdir.exists() or not any(
            f.suffix == ".json" for f in rdir.iterdir()
        )

    def test_serialized_request_is_valid_json(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "json-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="json-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req = result["request"]
        # Verify all required keys present
        for key in ("request_id", "capability_id", "status", "reason", "risk_level",
                     "required_approval", "requested_target_scope", "requested_target_maturity"):
            assert key in req

    def test_created_at_is_iso_format(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "iso-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="iso-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        created_at = result["request"]["created_at"]
        assert "T" in created_at  # ISO 8601 includes T separator


# ── Gate tests ─────────────────────────────────────────────────────────


class TestRequestGates:
    """All required gates block appropriately."""

    def test_missing_quarantine_capability_denied(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(CapabilityError, match="not found"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="nonexistent",
                reason="Test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_status_not_quarantined_denied(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "active-cap", status="active")
        with pytest.raises(CapabilityError, match="status.*quarantined"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="active-cap",
                reason="Test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_maturity_not_draft_denied(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "stable-cap", maturity="stable")
        with pytest.raises(CapabilityError, match="maturity.*draft"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="stable-cap",
                reason="Test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_no_review_denied(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "no-review-cap", write_review=False)
        with pytest.raises(CapabilityError, match="No review decision"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="no-review-cap",
                reason="Test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_rejected_review_denied(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "rejected-cap", review_status="rejected")
        with pytest.raises(CapabilityError, match="review status.*rejected"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="rejected-cap",
                reason="Test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_needs_changes_review_denied(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "needs-cap", review_status="needs_changes")
        with pytest.raises(CapabilityError, match="review status.*needs_changes"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="needs-cap",
                reason="Test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_no_audit_report_denied(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "no-audit-cap", write_audit=False)
        with pytest.raises(CapabilityError, match="No audit report"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="no-audit-cap",
                reason="Test",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_high_risk_sets_required_approval_true(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "high-risk-cap", risk_level="high")
        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="high-risk-cap",
            reason="High risk test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        assert result["request"]["required_approval"] is True

    def test_specified_review_not_found_denied(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "spec-cap")
        with pytest.raises(CapabilityError, match="Specified review not found"):
            request_quarantine_testing_transition(
                store_data_dir=store.data_dir,
                capability_id="spec-cap",
                reason="Test",
                source_review_id="nonexistent_review",
                evaluator=CapabilityEvaluator(),
                policy=CapabilityPolicy(),
            )

    def test_dry_run_reports_blocking_not_raises(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "blocked-cap", review_status="rejected")
        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="blocked-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
            dry_run=True,
        )
        assert result["would_create"] is False
        assert len(result["blocking_reasons"]) > 0


# ── List/View/Cancel tests ─────────────────────────────────────────────


class TestListTransitionRequests:
    def test_empty_for_nonexistent_capability(self, tmp_path):
        store = _make_store(tmp_path)
        results = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
            capability_id="nonexistent",
        )
        assert results == []

    def test_empty_when_no_requests(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "no-req-cap")
        results = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
        )
        assert results == []

    def test_lists_requests_for_capability(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "list-cap")

        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="list-cap",
            reason="Test 1",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )

        results = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
        )
        assert len(results) >= 1
        assert results[0]["capability_id"] == "list-cap"

    def test_filters_by_status(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "status-cap")

        r1 = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="status-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )

        cancel_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="status-cap",
            request_id=r1["request"]["request_id"],
            reason="Cancel test",
        )

        pending = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
            status="pending",
        )
        cancelled = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
            status="cancelled",
        )
        assert len(pending) == 0
        assert len(cancelled) >= 1

    def test_filters_by_target_scope(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "scope-cap")

        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="scope-cap",
            requested_target_scope="workspace",
            reason="Workspace only",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )

        user_results = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
            target_scope="user",
        )
        ws_results = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
            target_scope="workspace",
        )
        assert len(user_results) == 0
        assert len(ws_results) >= 1

    def test_compact_summaries_no_raw_paths(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "compact-cap")

        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="compact-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )

        results = list_quarantine_transition_requests(
            store_data_dir=store.data_dir,
        )
        for r in results:
            # Must not expose raw paths or script contents
            str_repr = json.dumps(r)
            assert "scripts/" not in str_repr or "file_count" in r
            assert "/quarantine/" not in str_repr


class TestViewTransitionRequest:
    def test_views_existing_request(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "view-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="view-cap",
            reason="Test view",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result["request"]["request_id"]

        view = view_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="view-cap",
            request_id=req_id,
        )
        assert view["request_id"] == req_id
        assert view["capability_id"] == "view-cap"
        assert view["status"] == "pending"

    def test_nonexistent_request_raises(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "noview-cap")
        with pytest.raises(CapabilityError, match="not found"):
            view_quarantine_transition_request(
                store_data_dir=store.data_dir,
                capability_id="noview-cap",
                request_id="nonexistent",
            )

    def test_no_script_contents_in_view(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "clean-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="clean-cap",
            reason="Clean view",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result["request"]["request_id"]

        view = view_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="clean-cap",
            request_id=req_id,
        )
        str_repr = json.dumps(view)
        assert "#!/" not in str_repr
        assert "os.system" not in str_repr


class TestCancelTransitionRequest:
    def test_cancel_changes_status_only(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "cancel-test-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="cancel-test-cap",
            reason="To be cancelled",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result["request"]["request_id"]

        cancelled = cancel_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="cancel-test-cap",
            request_id=req_id,
            reason="No longer needed",
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["metadata"]["cancellation_reason"] == "No longer needed"

        # Verify file on disk
        rpath = (
            store.data_dir / "quarantine" / "cancel-test-cap"
            / "quarantine_transition_requests" / f"{req_id}.json"
        )
        on_disk = json.loads(rpath.read_text())
        assert on_disk["status"] == "cancelled"

    def test_cancel_non_pending_raises(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "twice-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="twice-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result["request"]["request_id"]

        cancel_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="twice-cap",
            request_id=req_id,
            reason="First cancel",
        )

        with pytest.raises(CapabilityError, match="Only 'pending'"):
            cancel_quarantine_transition_request(
                store_data_dir=store.data_dir,
                capability_id="twice-cap",
                request_id=req_id,
                reason="Second cancel",
            )

    def test_cancel_does_not_delete_request_file(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "keep-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="keep-cap",
            reason="Keep",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result["request"]["request_id"]

        rpath = (
            store.data_dir / "quarantine" / "keep-cap"
            / "quarantine_transition_requests" / f"{req_id}.json"
        )

        cancel_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="keep-cap",
            request_id=req_id,
            reason="Cancel but keep",
        )
        assert rpath.is_file()

    def test_cancel_does_not_alter_capability(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "intact-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="intact-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result["request"]["request_id"]

        manifest_before = json.loads(
            (store.data_dir / "quarantine" / "intact-cap" / "manifest.json").read_text()
        )

        cancel_quarantine_transition_request(
            store_data_dir=store.data_dir,
            capability_id="intact-cap",
            request_id=req_id,
            reason="Cancel",
        )

        manifest_after = json.loads(
            (store.data_dir / "quarantine" / "intact-cap" / "manifest.json").read_text()
        )
        assert manifest_before == manifest_after

    def test_cancel_requires_reason(self, tmp_path):
        store = _make_store(tmp_path)
        _create_quarantine_dir(store, "reason-cap")

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id="reason-cap",
            reason="Test",
            evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(),
        )
        req_id = result["request"]["request_id"]

        with pytest.raises(CapabilityError, match="reason is required"):
            cancel_quarantine_transition_request(
                store_data_dir=store.data_dir,
                capability_id="reason-cap",
                request_id=req_id,
                reason="",
            )
