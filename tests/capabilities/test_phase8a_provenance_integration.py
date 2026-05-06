"""Phase 8A-1: Provenance integration tests — import and activation workflows."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.provenance import (
    read_provenance,
    write_provenance,
)
from src.capabilities.import_quarantine import import_capability_package
from src.capabilities.quarantine_activation_apply import (
    apply_quarantine_activation,
)
from src.capabilities.store import CapabilityStore


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_evaluator() -> CapabilityEvaluator:
    return CapabilityEvaluator()


def _make_policy() -> CapabilityPolicy:
    return CapabilityPolicy()


def _make_index(tmp_path: Path) -> CapabilityIndex:
    db_path = tmp_path / "index.sqlite"
    idx = CapabilityIndex(str(db_path))
    idx.init()
    return idx


def _write_package(dir_path: Path, *, cap_id: str = "test-pkg-01", **overrides) -> Path:
    """Create a valid external capability package for import testing."""
    dir_path.mkdir(parents=True, exist_ok=True)
    fm = {
        "id": cap_id,
        "name": f"Test {cap_id}",
        "description": "A test capability package for import testing.",
        "type": "skill",
        "scope": "user",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "active",
        "risk_level": "low",
        "triggers": ["test trigger"],
        "tags": ["test"],
    }
    fm.update(overrides)

    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nTest.\n\n"
        "## Procedure\n1. Test\n\n"
        "## Verification\nPass.\n\n"
        "## Failure handling\nRetry."
    )
    (dir_path / "CAPABILITY.md").write_text(md, encoding="utf-8")

    manifest = {
        "id": fm["id"], "name": fm["name"], "description": fm["description"],
        "type": fm["type"], "scope": fm["scope"], "version": fm.get("version", "0.1.0"),
        "maturity": fm.get("maturity", "draft"), "status": fm.get("status", "active"),
        "risk_level": fm.get("risk_level", "low"),
        "triggers": fm.get("triggers", []), "tags": fm.get("tags", []),
    }
    (dir_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )

    for sub in ("scripts", "tests", "examples"):
        (dir_path / sub).mkdir(exist_ok=True)

    return dir_path


def _create_quarantine_capability(
    store: CapabilityStore,
    cap_id: str,
    *,
    with_provenance: bool = True,
    write_review: bool = True,
    write_audit: bool = True,
    review_status: str = "approved_for_testing",
    audit_passed: bool = True,
    provenance_trust: str = "untrusted",
) -> Path:
    """Create a quarantined capability with review, audit, and provenance."""
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
        "maturity": "draft",
        "status": "quarantined",
        "risk_level": "low",
        "triggers": ["when test"],
        "tags": ["test"],
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
    (qdir / "manifest.json").write_text(json.dumps({
        k: v for k, v in fm.items() if k not in ("version",)
    }, indent=2), encoding="utf-8")

    # import_report.json
    import_report = {
        "capability_id": cap_id,
        "source_type": "local_package",
        "source_path_hash": "abc123",
        "imported_at": "2026-05-01T00:00:00+00:00",
        "original_content_hash": "hash-abc",
        "target_scope": "user",
        "eval_passed": True, "eval_score": 1.0,
        "eval_findings": [], "policy_findings": [],
        "files_summary": {"scripts": [], "tests": [], "examples": []},
    }
    (qdir / "import_report.json").write_text(
        json.dumps(import_report, indent=2), encoding="utf-8",
    )

    # provenance.json
    if with_provenance:
        write_provenance(
            qdir,
            capability_id=cap_id,
            source_type="local_package",
            source_path_hash="abc123",
            source_content_hash="src-hash-001",
            imported_at="2026-05-01T00:00:00+00:00",
            imported_by="test-user",
            trust_level=provenance_trust,
            integrity_status="verified",
            signature_status="not_present",
            metadata={"import_report_id": "import_report.json"},
        )

    # review
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

    # audit
    if write_audit:
        aud_dir = qdir / "quarantine_audit_reports"
        aud_dir.mkdir(parents=True, exist_ok=True)
        audit = {
            "capability_id": cap_id,
            "audit_id": "audit_test001",
            "created_at": "2026-05-02T09:00:00+00:00",
            "passed": audit_passed,
            "risk_level": "low",
            "findings": [
                {"severity": "info", "code": "test_finding", "message": "Test finding"},
            ],
            "recommended_review_status": "approved_for_testing" if audit_passed else "needs_review",
            "remediation_suggestions": [],
        }
        (aud_dir / "audit_test001.json").write_text(
            json.dumps(audit, indent=2), encoding="utf-8",
        )

    return qdir


def _create_pending_request(store: CapabilityStore, cap_id: str) -> dict:
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
    (req_dir / f"{req_id}.json").write_text(
        json.dumps(req, indent=2), encoding="utf-8",
    )
    return req


def _create_allowed_plan(store: CapabilityStore, cap_id: str) -> dict:
    qdir = store.data_dir / "quarantine" / cap_id
    plans_dir = qdir / "quarantine_activation_plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_id = f"qap_{cap_id[:8]}"
    plan = {
        "plan_id": plan_id,
        "capability_id": cap_id,
        "request_id": f"qtr_{cap_id[:8]}",
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
    (plans_dir / f"{plan_id}.json").write_text(
        json.dumps(plan, indent=2), encoding="utf-8",
    )
    return plan


# ── Import provenance tests ────────────────────────────────────────────────────


class TestImportWritesProvenance:
    """Phase 7A import writes provenance.json to quarantine directory."""

    def test_import_writes_provenance_json(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-01")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )

        assert result.applied is True
        qdir = Path(result.quarantine_path)
        assert (qdir / "provenance.json").is_file()

    def test_import_provenance_source_type_local_package(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-02")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.source_type == "local_package"

    def test_import_provenance_trust_level_untrusted(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-03")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.trust_level == "untrusted"

    def test_import_provenance_integrity_status_verified(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-04")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.integrity_status == "verified"

    def test_import_provenance_signature_not_present(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-05")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.signature_status == "not_present"

    def test_import_provenance_source_path_hash_present(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-06")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.source_path_hash is not None
        assert len(prov.source_path_hash) == 64  # SHA256 hex

    def test_import_provenance_raw_source_path_absent(self, tmp_path: Path):
        """Provenance must NOT contain the raw source path — only hash."""
        store = _make_store(tmp_path)
        pkg_dir = tmp_path / "valid_pkg"
        pkg = _write_package(pkg_dir, cap_id="import-prov-07")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        prov_dict = prov.to_dict()
        raw_path = str(pkg_dir.resolve())

        # Check the provenance file on disk too
        raw_json = (Path(result.quarantine_path) / "provenance.json").read_text()
        assert raw_path not in raw_json

    def test_import_provenance_source_content_hash_present(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-08")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.source_content_hash
        assert len(prov.source_content_hash) == 64

    def test_import_provenance_has_imported_at(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-09")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.imported_at is not None
        assert "T" in prov.imported_at  # ISO format

    def test_import_provenance_has_imported_by(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-10")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
            imported_by="alice",
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.imported_by == "alice"

    def test_import_provenance_has_provenance_id(self, tmp_path: Path):
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-prov-11")

        result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        prov = read_provenance(Path(result.quarantine_path))
        assert prov is not None
        assert prov.provenance_id.startswith("prov_")
        assert len(prov.provenance_id) == 17  # prov_ + 12 hex

    def test_import_provenance_fail_closed_cleans_up(self, tmp_path: Path):
        """If provenance write fails, quarantine dir is removed and import fails."""
        store = _make_store(tmp_path)
        pkg = _write_package(tmp_path / "valid_pkg", cap_id="import-fc-01")

        with patch(
            "src.capabilities.provenance.write_provenance",
            side_effect=OSError("disk full"),
        ):
            result = import_capability_package(
                path=pkg, store=store,
                evaluator=_make_evaluator(), policy=_make_policy(),
            )

        assert result.applied is False
        assert any("provenance" in e.lower() for e in result.errors)
        assert not store.data_dir.joinpath("quarantine", "import-fc-01").exists()


# ── Activation provenance tests ────────────────────────────────────────────────


class TestActivationWritesProvenance:
    """Phase 7D-B apply writes derived provenance.json to target directory."""

    def test_apply_writes_provenance_json(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-01")
        _create_pending_request(store, "act-prov-01")
        _create_allowed_plan(store, "act-prov-01")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-01",
            reason="Testing activation provenance",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )

        assert result.applied is True
        target_dir = store.data_dir / "user" / "act-prov-01"
        assert (target_dir / "provenance.json").is_file()

    def test_apply_provenance_source_type_quarantine_activation(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-02")
        _create_pending_request(store, "act-prov-02")
        _create_allowed_plan(store, "act-prov-02")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-02",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )

        target_dir = store.data_dir / "user" / "act-prov-02"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert prov.source_type == "quarantine_activation"

    def test_apply_provenance_parent_links_quarantine(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-03")
        _create_pending_request(store, "act-prov-03")
        _create_allowed_plan(store, "act-prov-03")

        # Read quarantine provenance before activation
        qdir = store.data_dir / "quarantine" / "act-prov-03"
        q_prov = read_provenance(qdir)
        assert q_prov is not None

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-03",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-03"
        t_prov = read_provenance(target_dir)
        assert t_prov is not None
        assert t_prov.parent_provenance_id == q_prov.provenance_id

    def test_apply_provenance_trust_reviewed_when_review_and_audit_pass(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(
            store, "act-prov-04",
            review_status="approved_for_testing", audit_passed=True,
        )
        _create_pending_request(store, "act-prov-04")
        _create_allowed_plan(store, "act-prov-04")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-04",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-04"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert prov.trust_level == "reviewed"

    def test_apply_provenance_trust_untrusted_when_review_not_approved(self, tmp_path: Path):
        """Activation is denied when review status is not approved_for_testing."""
        store = _make_store(tmp_path)
        _create_quarantine_capability(
            store, "act-prov-05",
            review_status="needs_changes", audit_passed=True,
        )
        _create_pending_request(store, "act-prov-05")
        _create_allowed_plan(store, "act-prov-05")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-05",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is False
        assert any("review" in f["type"].lower() for f in result.blocking_findings)

    def test_apply_provenance_trust_untrusted_when_audit_not_passed(self, tmp_path: Path):
        """Activation is denied when audit has not passed."""
        store = _make_store(tmp_path)
        _create_quarantine_capability(
            store, "act-prov-06",
            review_status="approved_for_testing", audit_passed=False,
        )
        _create_pending_request(store, "act-prov-06")
        _create_allowed_plan(store, "act-prov-06")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-06",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is False
        assert any("audit" in f["type"].lower() for f in result.blocking_findings)

    def test_apply_provenance_origin_capability_id(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-07")
        _create_pending_request(store, "act-prov-07")
        _create_allowed_plan(store, "act-prov-07")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-07",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-07"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert prov.origin_capability_id == "act-prov-07"

    def test_apply_provenance_origin_scope_quarantine(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-08")
        _create_pending_request(store, "act-prov-08")
        _create_allowed_plan(store, "act-prov-08")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-08",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-08"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert prov.origin_scope == "quarantine"

    def test_apply_provenance_integrity_verified(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-09")
        _create_pending_request(store, "act-prov-09")
        _create_allowed_plan(store, "act-prov-09")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-09",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-09"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert prov.integrity_status == "verified"

    def test_apply_provenance_signature_inherited_from_quarantine(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(
            store, "act-prov-10", provenance_trust="untrusted",
        )
        _create_pending_request(store, "act-prov-10")
        _create_allowed_plan(store, "act-prov-10")

        qdir = store.data_dir / "quarantine" / "act-prov-10"
        q_prov = read_provenance(qdir)
        assert q_prov is not None

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-10",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-10"
        t_prov = read_provenance(target_dir)
        assert t_prov is not None
        assert t_prov.signature_status == q_prov.signature_status

    def test_apply_provenance_activated_at_set(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-11")
        _create_pending_request(store, "act-prov-11")
        _create_allowed_plan(store, "act-prov-11")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-11",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-11"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert prov.activated_at is not None
        assert "T" in prov.activated_at

    def test_apply_provenance_activated_by_set(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-12")
        _create_pending_request(store, "act-prov-12")
        _create_allowed_plan(store, "act-prov-12")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-12",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="operator-1",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-12"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert prov.activated_by == "operator-1"

    def test_apply_provenance_metadata_contains_plan_and_request(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "act-prov-13")
        _create_pending_request(store, "act-prov-13")
        _create_allowed_plan(store, "act-prov-13")

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="act-prov-13",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        target_dir = store.data_dir / "user" / "act-prov-13"
        prov = read_provenance(target_dir)
        assert prov is not None
        assert "activation_plan_id" in prov.metadata
        assert "transition_request_id" in prov.metadata


class TestQuarantineProvenancePreserved:
    """Quarantine provenance is never modified by activation."""

    def test_quarantine_provenance_unchanged_after_activation(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "qpp-01")
        _create_pending_request(store, "qpp-01")
        _create_allowed_plan(store, "qpp-01")

        qdir = store.data_dir / "quarantine" / "qpp-01"
        prov_before = (qdir / "provenance.json").read_bytes()

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="qpp-01",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        assert result.applied is True

        prov_after = (qdir / "provenance.json").read_bytes()
        assert prov_before == prov_after, "Quarantine provenance was modified by activation"

    def test_quarantine_provenance_unchanged_even_when_activation_fails(self, tmp_path: Path):
        """If activation fails for an unrelated reason, quarantine provenance is untouched."""
        store = _make_store(tmp_path)
        qdir = _create_quarantine_capability(store, "qpp-02")
        _create_pending_request(store, "qpp-02")
        _create_allowed_plan(store, "qpp-02")

        prov_before = (qdir / "provenance.json").read_bytes()

        # Cause activation to fail by removing the CAPABILITY.md file
        (qdir / "CAPABILITY.md").unlink()

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="qpp-02",
            reason="Testing",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            applied_by="test",
        )
        # Should fail because missing CAPABILITY.md
        assert result.applied is False

        prov_after = (qdir / "provenance.json").read_bytes()
        assert prov_before == prov_after, "Quarantine provenance was modified by failed activation"


class TestActivationFailClosed:
    """If provenance write fails during activation, target directory is removed."""

    def test_provenance_write_failure_rolls_back_target_dir(self, tmp_path: Path):
        store = _make_store(tmp_path)
        _create_quarantine_capability(store, "afc-01")
        _create_pending_request(store, "afc-01")
        _create_allowed_plan(store, "afc-01")

        target_dir = store.data_dir / "user" / "afc-01"

        with patch(
            "src.capabilities.provenance.write_provenance",
            side_effect=OSError("disk full"),
        ):
            result = apply_quarantine_activation(
                store_data_dir=store.data_dir,
                capability_id="afc-01",
                reason="Testing fail-closed",
                evaluator=_make_evaluator(),
                policy=_make_policy(),
                applied_by="test",
            )

        assert result.applied is False
        assert any("provenance" in f.get("detail", "").lower() for f in result.blocking_findings)
        assert not target_dir.exists()


class TestProvenanceIntegrationRoundTrip:
    """Full round-trip: import → provenance → activate → derived provenance."""

    def test_full_import_to_activation_provenance_chain(self, tmp_path: Path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)

        # 1. Import a package
        pkg = _write_package(tmp_path / "roundtrip_pkg", cap_id="rt-chain-01")
        import_result = import_capability_package(
            path=pkg, store=store,
            evaluator=_make_evaluator(), policy=_make_policy(),
        )
        assert import_result.applied is True
        qdir = Path(import_result.quarantine_path)

        # Read quarantine provenance
        q_prov = read_provenance(qdir)
        assert q_prov is not None
        assert q_prov.source_type == "local_package"
        assert q_prov.trust_level == "untrusted"

        # 2. Add review and audit
        rev_dir = qdir / "quarantine_reviews"
        rev_dir.mkdir(exist_ok=True)
        (rev_dir / "review_rt001.json").write_text(json.dumps({
            "capability_id": "rt-chain-01",
            "review_id": "review_rt001",
            "review_status": "approved_for_testing",
            "reviewer": "tester",
            "reason": "OK",
            "created_at": "2026-05-02T10:00:00+00:00",
        }), encoding="utf-8")

        aud_dir = qdir / "quarantine_audit_reports"
        aud_dir.mkdir(exist_ok=True)
        (aud_dir / "audit_rt001.json").write_text(json.dumps({
            "capability_id": "rt-chain-01",
            "audit_id": "audit_rt001",
            "created_at": "2026-05-02T09:00:00+00:00",
            "passed": True,
            "risk_level": "low",
            "findings": [],
            "recommended_review_status": "approved_for_testing",
            "remediation_suggestions": [],
        }), encoding="utf-8")

        # 3. Create request and plan
        req_dir = qdir / "quarantine_transition_requests"
        req_dir.mkdir(exist_ok=True)
        (req_dir / "qtr_rt-chain.json").write_text(json.dumps({
            "request_id": "qtr_rt-chain",
            "capability_id": "rt-chain-01",
            "created_at": "2026-05-03T00:00:00+00:00",
            "requested_target_scope": "user",
            "requested_target_maturity": "testing",
            "status": "pending",
            "reason": "Ready",
            "risk_level": "low",
            "required_approval": False,
            "findings_summary": {},
            "content_hash_at_request": "",
            "source_review_id": "review_rt001",
            "source_audit_id": "audit_rt001",
        }), encoding="utf-8")

        plans_dir = qdir / "quarantine_activation_plans"
        plans_dir.mkdir(exist_ok=True)
        (plans_dir / "qap_rt-chain.json").write_text(json.dumps({
            "plan_id": "qap_rt-chain",
            "capability_id": "rt-chain-01",
            "request_id": "qtr_rt-chain",
            "created_at": "2026-05-03T08:00:00+00:00",
            "created_by": "operator",
            "source_review_id": "review_rt001",
            "source_audit_id": "audit_rt001",
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
        }), encoding="utf-8")

        # 4. Activate
        q_prov_before = (qdir / "provenance.json").read_bytes()

        act_result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id="rt-chain-01",
            reason="Round-trip test",
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            index=idx,
            applied_by="test",
        )
        assert act_result.applied is True

        # 5. Verify target provenance chain
        target_dir = store.data_dir / "user" / "rt-chain-01"
        t_prov = read_provenance(target_dir)
        assert t_prov is not None
        assert t_prov.source_type == "quarantine_activation"
        assert t_prov.parent_provenance_id == read_provenance(qdir).provenance_id
        assert t_prov.origin_capability_id == "rt-chain-01"
        assert t_prov.origin_scope == "quarantine"
        assert t_prov.trust_level == "reviewed"
        assert t_prov.integrity_status == "verified"

        # 6. Quarantine provenance unchanged
        q_prov_after = (qdir / "provenance.json").read_bytes()
        assert q_prov_before == q_prov_after
