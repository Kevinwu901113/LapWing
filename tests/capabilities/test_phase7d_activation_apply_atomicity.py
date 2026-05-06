"""Phase 7D-B atomicity tests: no partial writes, rollback, quarantine integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.quarantine_activation_apply import (
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


def _make_evaluator() -> CapabilityEvaluator:
    return CapabilityEvaluator()


def _make_policy() -> CapabilityPolicy:
    return CapabilityPolicy()


def _setup_full_quarantine(store: CapabilityStore, cap_id: str, **kw):
    qroot = store.data_dir / "quarantine"
    qroot.mkdir(parents=True, exist_ok=True)
    qdir = qroot / cap_id
    qdir.mkdir(parents=True, exist_ok=True)

    import yaml
    fm = {
        "id": cap_id, "name": f"Test {cap_id}",
        "description": "Test.", "type": "skill", "scope": "user",
        "version": "0.1.0",
        "maturity": kw.get("maturity", "draft"),
        "status": kw.get("status", "quarantined"),
        "risk_level": kw.get("risk_level", "low"),
        "triggers": ["when test"], "tags": ["test"],
        "trust_required": "developer", "required_tools": [], "required_permissions": [],
    }
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nWhen testing.\n\n"
        "## Procedure\n1. Test\n\n"
        "## Verification\nCheck output.\n\n"
        "## Failure handling\nRetry."
    )
    (qdir / "CAPABILITY.md").write_text(md, encoding="utf-8")
    (qdir / "manifest.json").write_text(json.dumps({
        k: v for k, v in fm.items()
    }, indent=2), encoding="utf-8")
    (qdir / "import_report.json").write_text(json.dumps({
        "capability_id": cap_id, "source_type": "local_package",
        "eval_passed": True, "eval_score": 1.0,
        "eval_findings": [], "policy_findings": [],
        "files_summary": {"scripts": [], "tests": [], "examples": []},
        "name": f"Test {cap_id}", "type": "skill", "risk_level": fm["risk_level"],
    }, indent=2), encoding="utf-8")

    rev_dir = qdir / "quarantine_reviews"
    rev_dir.mkdir(exist_ok=True)
    (rev_dir / "review_test001.json").write_text(json.dumps({
        "capability_id": cap_id, "review_id": "review_test001",
        "review_status": kw.get("review_status", "approved_for_testing"),
        "reviewer": "tester", "reason": "OK", "created_at": "2026-05-02T10:00:00Z",
    }, indent=2), encoding="utf-8")

    aud_dir = qdir / "quarantine_audit_reports"
    aud_dir.mkdir(exist_ok=True)
    (aud_dir / "audit_test001.json").write_text(json.dumps({
        "capability_id": cap_id, "audit_id": "audit_test001",
        "created_at": "2026-05-02T09:00:00Z",
        "passed": kw.get("audit_passed", True),
        "risk_level": fm["risk_level"], "findings": [],
        "recommended_review_status": kw.get("audit_recommended", "approved_for_testing"),
    }, indent=2), encoding="utf-8")

    req_dir = qdir / "quarantine_transition_requests"
    req_dir.mkdir(exist_ok=True)
    req_id = f"qtr_{cap_id[:8]}"
    (req_dir / f"{req_id}.json").write_text(json.dumps({
        "request_id": req_id, "capability_id": cap_id,
        "created_at": "2026-05-03T00:00:00Z",
        "requested_target_scope": "user", "requested_target_maturity": "testing",
        "status": "pending", "reason": "Ready", "risk_level": fm["risk_level"],
        "required_approval": False, "findings_summary": {},
        "content_hash_at_request": "",
        "source_review_id": "review_test001", "source_audit_id": "audit_test001",
    }, indent=2), encoding="utf-8")

    plans_dir = qdir / "quarantine_activation_plans"
    plans_dir.mkdir(exist_ok=True)
    plan_id = f"qap_{cap_id[:8]}"
    (plans_dir / f"{plan_id}.json").write_text(json.dumps({
        "plan_id": plan_id, "capability_id": cap_id,
        "request_id": req_id, "created_at": "2026-05-03T08:00:00Z",
        "created_by": "operator",
        "source_review_id": "review_test001", "source_audit_id": "audit_test001",
        "target_scope": "user", "target_status": "active", "target_maturity": "testing",
        "allowed": True, "required_approval": False,
        "blocking_findings": [], "policy_findings": [], "evaluator_findings": [],
        "copy_plan": {"target_scope": "user", "total_files": 3},
        "content_hash": "", "request_content_hash": "",
        "risk_level": fm["risk_level"], "explanation": "OK.",
        "would_activate": False,
    }, indent=2), encoding="utf-8")

    return qdir


# ── Atomicity tests ───────────────────────────────────────────────────


class TestApplyActivationAtomicity:
    """Ensure failed operations leave no partial state."""

    def test_denied_apply_writes_nothing(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_full_quarantine(store, "test-atomic-denied", status="active")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-atomic-denied",
            reason="Test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        target_dir = store.data_dir / "user" / "test-atomic-denied"
        assert not target_dir.exists()

    def test_collision_writes_nothing(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_full_quarantine(store, "test-atomic-collide")

        # Pre-create target dir
        target_dir = store.data_dir / "user" / "test-atomic-collide"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "existing.txt").write_text("preexisting")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-atomic-collide",
            reason="Test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        # Target dir should not have been modified
        assert (target_dir / "existing.txt").read_text() == "preexisting"

    def test_target_dir_not_created_on_failure(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_full_quarantine(store, "test-atomic-nocreate", status="active")

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-atomic-nocreate",
            reason="Test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        target_dir = store.data_dir / "user" / "test-atomic-nocreate"
        assert not target_dir.exists()

    def test_original_quarantine_never_corrupted_on_success(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        qdir = _setup_full_quarantine(store, "test-atomic-quarantine")

        # Capture original state
        original_files = set()
        for f in qdir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(qdir)
                original_files.add((str(rel), f.read_bytes()))

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-atomic-quarantine",
            reason="Test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        # Verify original files unchanged (byte-for-byte).
        # Exclude: quarantine_transition_requests (may be marked superseded on apply)
        # and quarantine_activation_reports (created during apply).
        for rel_path, orig_bytes in original_files:
            current_path = qdir / rel_path
            assert current_path.is_file(), f"Missing: {rel_path}"
            rel_str = str(rel_path)
            if "quarantine_activation_reports" in rel_str:
                continue
            if "quarantine_transition_requests" in rel_str:
                continue  # Request status may change to superseded
            assert current_path.read_bytes() == orig_bytes, f"Modified: {rel_path}"

    def test_original_quarantine_never_corrupted_on_failure(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        qdir = _setup_full_quarantine(store, "test-atomic-qfail", status="active")

        original_files = {}
        for f in qdir.rglob("*"):
            if f.is_file():
                original_files[f.relative_to(qdir)] = f.read_bytes()

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-atomic-qfail",
            reason="Test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        for rel_path, orig_bytes in original_files.items():
            current_path = qdir / rel_path
            assert current_path.is_file(), f"Missing: {rel_path}"
            assert current_path.read_bytes() == orig_bytes, f"Modified: {rel_path}"

    def test_original_manifest_status_unchanged_after_success(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        qdir = _setup_full_quarantine(store, "test-atomic-status")

        manifest_before = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))

        apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-atomic-status",
            reason="Test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        manifest_after = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest_after["status"] == manifest_before["status"]
        assert manifest_after["maturity"] == manifest_before["maturity"]

    def test_repeated_apply_after_success_is_denied(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_full_quarantine(store, "test-repeat")

        r1 = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-repeat",
            reason="First",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )
        assert r1.applied is True

        r2 = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-repeat",
            reason="Second",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )
        assert r2.applied is False
        types2 = [f["type"] for f in r2.blocking_findings]
        assert any(t in types2 for t in ("target_collision", "no_pending_request"))

    def test_blocked_by_gate_leaves_no_target_dir(self, tmp_path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        _setup_full_quarantine(store, "test-blocked-nodir", status="active")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="test-blocked-nodir",
            reason="Test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
        )

        assert result.applied is False
        target_dir = store.data_dir / "user" / "test-blocked-nodir"
        assert not target_dir.exists()
