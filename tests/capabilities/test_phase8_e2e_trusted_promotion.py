"""Phase 8 Post-Audit: end-to-end trusted promotion lifecycle tests.

Five flows covering the complete external-to-stable trusted capability
lifecycle, stable promotion trust gate behavior, and legacy compatibility.

Flow A: low-risk reviewed provenance stable promotion (full happy path)
Flow B: untrusted or mismatched integrity blocks stable
Flow C: high-risk reviewed-only blocks stable
Flow D: flag-off compatibility (old lifecycle behavior unchanged)
Flow E: legacy/manual low-risk missing provenance (warning, not denial)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.import_quarantine import (
    import_capability_package,
    inspect_capability_package,
)
from src.capabilities.index import CapabilityIndex
from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.provenance import (
    INTEGRITY_MISMATCH,
    INTEGRITY_VERIFIED,
    SIGNATURE_NOT_PRESENT,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_UNTRUSTED,
    CapabilityTrustPolicy,
    read_provenance,
    write_provenance,
)
from src.capabilities.quarantine_activation_apply import apply_quarantine_activation
from src.capabilities.quarantine_activation_planner import plan_quarantine_activation
from src.capabilities.quarantine_review import (
    audit_quarantined_capability,
    mark_quarantine_review,
)
from src.capabilities.quarantine_transition import (
    request_quarantine_testing_transition,
)
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    SideEffect,
)
from src.capabilities.store import CapabilityStore


# ── helpers ──────────────────────────────────────────────────────────────────

VALID_BODY = """## When to use

Use this for end-to-end trusted promotion testing.

## Procedure

1. Import from external package.
2. Audit and review in quarantine.
3. Request transition and plan activation.
4. Apply activation to active/testing.
5. Promote testing -> stable via trust gate.

## Verification

Verify maturity=stable, provenance unchanged, no script execution.

## Failure handling

Rollback and retry.
"""


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_index(tmp_path: Path) -> CapabilityIndex:
    db_path = tmp_path / "index.sqlite"
    idx = CapabilityIndex(str(db_path))
    idx.init()
    return idx


def _make_evaluator() -> CapabilityEvaluator:
    return CapabilityEvaluator()


def _make_policy() -> CapabilityPolicy:
    return CapabilityPolicy()


def _make_lifecycle(
    store: CapabilityStore,
    *,
    trust_policy: CapabilityTrustPolicy | None = None,
    trust_gate_enabled: bool = False,
) -> CapabilityLifecycleManager:
    return CapabilityLifecycleManager(
        store=store,
        evaluator=CapabilityEvaluator(),
        policy=CapabilityPolicy(),
        planner=PromotionPlanner(),
        trust_policy=trust_policy,
        trust_gate_enabled=trust_gate_enabled,
    )


def _write_external_package(
    dir_path: Path,
    *,
    cap_id: str = "test-e2e-pkg",
    risk_level: str = "low",
    scripts: list[tuple[str, str]] | None = None,
) -> Path:
    """Create a valid external capability package on disk for import testing."""
    dir_path.mkdir(parents=True, exist_ok=True)
    fm = {
        "id": cap_id,
        "name": f"E2E {cap_id}",
        "description": "End-to-end trusted promotion test package.",
        "type": "skill",
        "scope": "user",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "active",
        "risk_level": risk_level,
        "triggers": ["when e2e testing"],
        "tags": ["e2e", "trusted-promotion"],
        "trust_required": "developer",
        "required_tools": [],
        "required_permissions": [],
        "do_not_apply_when": ["not for unsafe trusted-promotion contexts"],
        "reuse_boundary": "Trusted promotion E2E test only.",
        "side_effects": ["none"],
    }
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nFor end-to-end trusted promotion testing.\n\n"
        "## Procedure\n1. Import\n2. Audit\n3. Review\n4. Activate\n5. Promote\n\n"
        "## Verification\nCheck target copy exists with stable maturity.\n\n"
        "## Failure handling\nRollback and retry.\n"
    )
    (dir_path / "CAPABILITY.md").write_text(md, encoding="utf-8")
    (dir_path / "manifest.json").write_text(json.dumps({
        k: v for k, v in fm.items()
    }, indent=2), encoding="utf-8")

    for sub in ("scripts", "tests", "examples", "evals", "traces", "versions"):
        subdir = dir_path / sub
        subdir.mkdir(exist_ok=True)
        (subdir / ".gitkeep").touch()
    (dir_path / "evals" / "positive_cases.jsonl").write_text('{"case":"ok"}\n', encoding="utf-8")
    (dir_path / "evals" / "boundary_cases.jsonl").write_text('{"case":"boundary"}\n', encoding="utf-8")

    if scripts:
        scripts_dir = dir_path / "scripts"
        for name, content in scripts:
            (scripts_dir / name).write_text(content)

    return dir_path


def _set_testing_with_boundary(store: CapabilityStore, doc):
    updated = doc.manifest.model_copy(update={
        "maturity": CapabilityMaturity("testing"),
        "do_not_apply_when": ["not for unsafe trusted-promotion contexts"],
        "reuse_boundary": "Trusted promotion direct test only.",
        "side_effects": [SideEffect.NONE],
    })
    doc.manifest = updated
    store._sync_manifest_json(doc.directory, doc)
    evals_dir = doc.directory / "evals"
    evals_dir.mkdir(exist_ok=True)
    (evals_dir / "positive_cases.jsonl").write_text('{"case":"ok"}\n', encoding="utf-8")
    (evals_dir / "boundary_cases.jsonl").write_text('{"case":"boundary"}\n', encoding="utf-8")
    return store._parser.parse(doc.directory)


def _full_import_flow(store, evaluator, policy, index, pkg_dir, cap_id,
                      reason="E2E trust test import", target="user"):
    """Run import + audit + review + request. Returns (qdir, audit, review, req_dict)."""
    result = import_capability_package(
        path=pkg_dir,
        store=store,
        evaluator=evaluator,
        policy=policy,
        index=index,
        target_scope=target,
        imported_by="e2e-trust-tester",
        reason=reason,
    )
    assert result.applied is True, f"Import failed: {result.errors}"
    assert result.capability_id == cap_id

    qdir = store.data_dir / "quarantine" / cap_id
    assert qdir.is_dir()

    audit = audit_quarantined_capability(
        store_data_dir=store.data_dir,
        capability_id=cap_id,
        evaluator=evaluator,
        policy=policy,
        write_report=True,
    )
    assert audit.passed is True, f"Audit failed: {audit.findings}"

    review = mark_quarantine_review(
        store_data_dir=store.data_dir,
        capability_id=cap_id,
        review_status="approved_for_testing",
        reviewer="e2e-trust-tester",
        reason="E2E trust test approval",
    )
    assert review.review_status == "approved_for_testing"

    req_result = request_quarantine_testing_transition(
        store_data_dir=store.data_dir,
        capability_id=cap_id,
        requested_target_scope=target,
        reason="E2E trust test transition",
        evaluator=evaluator,
        policy=policy,
        created_by="e2e-trust-tester",
        source_review_id=review.review_id,
        source_audit_id=audit.audit_id,
    )
    req_data = req_result["request"]
    assert req_data["status"] == "pending", f"Request failed: {req_result}"

    return qdir, audit, review, req_data


def _activate_to_testing(store, evaluator, policy, index, cap_id, req_data, target="user"):
    """Plan and apply activation from quarantine to active/testing. Returns target_dir."""
    plan_result = plan_quarantine_activation(
        store_data_dir=store.data_dir,
        capability_id=cap_id,
        request_id=req_data["request_id"],
        target_scope=target,
        evaluator=evaluator,
        policy=policy,
        created_by="e2e-operator",
    )
    plan = plan_result["plan"]
    assert plan["allowed"] is True, f"Plan blocked: {plan.get('blocking_findings')}"
    assert plan_result["would_activate"] is False

    act_result = apply_quarantine_activation(
        store_data_dir=store.data_dir,
        capability_id=cap_id,
        reason="E2E trust test apply",
        plan_id=plan["plan_id"],
        request_id=req_data["request_id"],
        target_scope=target,
        applied_by="e2e-operator",
        evaluator=evaluator,
        policy=policy,
        index=index,
    )
    assert act_result.applied is True, f"Apply blocked: {act_result.blocking_findings}"

    target_dir = store.data_dir / target / cap_id
    assert target_dir.is_dir()

    target_manifest = json.loads((target_dir / "manifest.json").read_text())
    assert target_manifest["status"] == "active"
    assert target_manifest["maturity"] == "testing"
    return target_dir


# ── Flow A: low-risk reviewed provenance stable promotion ────────────────────


class TestFlowAFullHappyPath:
    """Full lifecycle: external package → quarantine → testing → stable.

    Verifies the complete trusted promotion path with provenance at each stage.
    """

    def test_full_lifecycle_external_to_stable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-trusted-a"
        pkg_dir = tmp_path / "external_pkg_a"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        # ── Phase 1: Import to quarantine ──
        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )

        # Verify quarantine provenance: source_type=local_package, trust=untrusted, integrity=verified
        q_prov = read_provenance(qdir)
        assert q_prov is not None, "Expected provenance.json in quarantine"
        assert q_prov.source_type == "local_package"
        assert q_prov.trust_level == TRUST_UNTRUSTED
        assert q_prov.integrity_status == INTEGRITY_VERIFIED
        assert q_prov.signature_status == SIGNATURE_NOT_PRESENT
        assert q_prov.capability_id == cap_id
        assert q_prov.source_content_hash
        assert len(q_prov.source_content_hash) == 64

        # ── Phase 2: Activate to testing ──
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Verify target provenance: source_type=quarantine_activation, trust=reviewed, integrity=verified
        t_prov = read_provenance(target_dir)
        assert t_prov is not None, "Expected provenance.json in target"
        assert t_prov.source_type == "quarantine_activation"
        assert t_prov.trust_level == TRUST_REVIEWED
        assert t_prov.integrity_status == INTEGRITY_VERIFIED
        assert t_prov.signature_status == SIGNATURE_NOT_PRESENT
        assert t_prov.origin_capability_id == cap_id
        assert t_prov.origin_scope == "quarantine"
        assert t_prov.parent_provenance_id is not None
        assert t_prov.parent_provenance_id == q_prov.provenance_id
        assert t_prov.activated_by == "e2e-operator"
        assert t_prov.activated_at is not None

        # ── Phase 3: Promote testing → stable with trust gate ──
        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")

        assert result.applied is True, f"Stable promotion blocked: {result.message}"
        assert result.to_maturity == "stable"
        assert result.version_snapshot_id is not None

        # Verify manifest reflects stable
        doc = store.get(cap_id, CapabilityScope.USER)
        assert doc.manifest.maturity.value == "stable"

        # Verify provenance unchanged by promotion (provenance is immutable through maturity transitions)
        post_prov = read_provenance(target_dir)
        assert post_prov is not None
        assert post_prov.provenance_id == t_prov.provenance_id
        assert post_prov.source_type == t_prov.source_type
        assert post_prov.trust_level == t_prov.trust_level
        assert post_prov.integrity_status == t_prov.integrity_status
        assert post_prov.signature_status == t_prov.signature_status

        # Verify trust gate decision included in policy_decisions
        trust_decisions = [
            d for d in result.policy_decisions
            if d.get("source") == "CapabilityTrustPolicy"
        ]
        assert len(trust_decisions) >= 1
        assert trust_decisions[0]["allowed"] is True

        # Verify version snapshot written
        versions_dir = target_dir / "versions"
        assert versions_dir.is_dir()
        snapshots = [d for d in versions_dir.iterdir() if d.is_dir()]
        assert len(snapshots) >= 1

    def test_provenance_immutable_through_promotion(self, tmp_path: Path):
        """Provenance.json content (except computed fields) does not change
        during testing -> stable promotion."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-immutable-prov"
        pkg_dir = tmp_path / "external_pkg_immutable"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Snapshot provenance bytes before promotion
        prov_path = target_dir / "provenance.json"
        prov_bytes_before = prov_path.read_bytes()

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")
        assert result.applied is True

        # Provenance bytes must be identical
        prov_bytes_after = prov_path.read_bytes()
        assert prov_bytes_before == prov_bytes_after, (
            "provenance.json mutated during promotion"
        )

    def test_no_script_execution_during_promotion(self, tmp_path: Path):
        """Promotion to stable must not execute any scripts."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-no-exec"
        pkg_dir = tmp_path / "external_pkg_no_exec"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low", scripts=[
            ("run.py", "# This script should never be executed"),
        ])

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Verify scripts were copied but not executed
        assert (target_dir / "scripts" / "run.py").is_file()
        assert (qdir / "scripts" / "run.py").is_file()

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")
        assert result.applied is True

        # Scripts still present, unchanged
        assert (target_dir / "scripts" / "run.py").read_text() == (
            "# This script should never be executed"
        )

    def test_index_reflects_promoted_capability(self, tmp_path: Path):
        """After promotion to stable, the capability is found in index with
        maturity=stable."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-index-stable"
        pkg_dir = tmp_path / "external_pkg_index"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")
        assert result.applied is True

        doc = store.get(cap_id, CapabilityScope.USER)
        assert doc.manifest.maturity.value == "stable"

        results = idx.search(cap_id, filters={"scope": "user"})
        matching = [r for r in results if r.get("id") == cap_id]
        assert len(matching) >= 1

    def test_quarantine_unchanged_after_testing_to_stable(self, tmp_path: Path):
        """Original quarantine copy remains quarantined/draft even after
        target is promoted to stable."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-q-unchanged"
        pkg_dir = tmp_path / "external_pkg_q"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        q_manifest_before = json.loads((qdir / "manifest.json").read_text())

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")
        assert result.applied is True

        q_manifest_after = json.loads((qdir / "manifest.json").read_text())
        assert q_manifest_after["status"] == q_manifest_before["status"] == "quarantined"
        assert q_manifest_after["maturity"] == q_manifest_before["maturity"] == "draft"


# ── Flow B: untrusted or mismatched integrity blocks stable ──────────────────


class TestFlowBBlockedStablePromotion:
    """Stable promotion blocked when provenance trust is insufficient or
    integrity is mismatched. Verifies atomic denial."""

    def test_untrusted_provenance_blocks_stable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-block-untrusted"
        pkg_dir = tmp_path / "external_pkg_untrusted"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Alter provenance to untrusted
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_UNTRUSTED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        # Snapshot state before attempt
        manifest_bytes_before = (target_dir / "manifest.json").read_bytes()
        prov_bytes_before = (target_dir / "provenance.json").read_bytes()
        maturity_before = json.loads(manifest_bytes_before.decode())["maturity"]

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")

        assert result.applied is False
        assert "trust" in result.message.lower()

        # Verify atomic denial: manifest and provenance unchanged byte-for-byte
        manifest_bytes_after = (target_dir / "manifest.json").read_bytes()
        prov_bytes_after = (target_dir / "provenance.json").read_bytes()
        assert manifest_bytes_before == manifest_bytes_after, (
            "manifest.json mutated during denied promotion"
        )
        assert prov_bytes_before == prov_bytes_after, (
            "provenance.json mutated during denied promotion"
        )
        assert maturity_before == "testing"

    def test_integrity_mismatch_blocks_stable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-block-mismatch"
        pkg_dir = tmp_path / "external_pkg_mismatch"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Alter provenance to integrity mismatch
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_MISMATCH,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        manifest_bytes_before = (target_dir / "manifest.json").read_bytes()
        prov_bytes_before = (target_dir / "provenance.json").read_bytes()

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")

        assert result.applied is False
        assert "integrity" in result.message.lower()

        manifest_bytes_after = (target_dir / "manifest.json").read_bytes()
        prov_bytes_after = (target_dir / "provenance.json").read_bytes()
        assert manifest_bytes_before == manifest_bytes_after
        assert prov_bytes_before == prov_bytes_after

    def test_no_version_snapshot_on_denial(self, tmp_path: Path):
        """Denied promotion must not create a version snapshot."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-no-snap"
        pkg_dir = tmp_path / "external_pkg_no_snap"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Count existing snapshots before
        versions_dir = target_dir / "versions"
        existing = set()
        if versions_dir.exists():
            existing = {d.name for d in versions_dir.iterdir() if d.is_dir()}

        # Alter to untrusted
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_UNTRUSTED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")

        assert result.applied is False
        assert result.version_snapshot_id is None

        # No new snapshots created
        if versions_dir.exists():
            current = {d.name for d in versions_dir.iterdir() if d.is_dir()}
            assert current == existing, "Snapshot created during denied promotion"

    def test_index_unchanged_on_denial(self, tmp_path: Path):
        """Index must not reflect a denied promotion."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-idx-unchanged"
        pkg_dir = tmp_path / "external_pkg_idx_u"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Check maturity in index before
        results_before = idx.search(cap_id, filters={"scope": "user"})
        mat_before = next(
            (r.get("maturity") for r in results_before if r.get("id") == cap_id),
            None,
        )
        assert mat_before == "testing"

        # Alter to untrusted
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_UNTRUSTED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")
        assert result.applied is False

        results_after = idx.search(cap_id, filters={"scope": "user"})
        mat_after = next(
            (r.get("maturity") for r in results_after if r.get("id") == cap_id),
            None,
        )
        assert mat_after == "testing", (
            f"Index maturity changed from {mat_before} to {mat_after} on denial"
        )


# ── Flow C: high-risk reviewed-only blocks stable ────────────────────────────


class TestFlowCHighRiskReviewedOnlyBlocks:
    """High-risk capabilities with reviewed provenance (not trusted_local or
    trusted_signed) are blocked from stable promotion even with approval."""

    def test_high_risk_reviewed_blocks_stable(self, tmp_path: Path):
        """High-risk with reviewed provenance blocks stable promotion.
        High-risk capabilities can't go through quarantine activation,
        so create directly in testing with reviewed provenance."""
        store = _make_store(tmp_path)
        cap_id = "e2e-high-reviewed"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="High Risk Reviewed",
            description="High risk capability with reviewed provenance.",
            body=VALID_BODY,
            risk_level="high",
        )
        doc = _set_testing_with_boundary(store, doc)

        target_dir = doc.directory
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        t_prov = read_provenance(target_dir)
        assert t_prov.trust_level == TRUST_REVIEWED

        manifest_bytes_before = (target_dir / "manifest.json").read_bytes()
        prov_bytes_before = (target_dir / "provenance.json").read_bytes()

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(
            cap_id, "stable", scope="workspace", approval=approval,
        )

        assert result.applied is False
        assert "reviewed" in result.message.lower() or "high" in result.message.lower()

        # No mutation
        manifest_bytes_after = (target_dir / "manifest.json").read_bytes()
        prov_bytes_after = (target_dir / "provenance.json").read_bytes()
        assert manifest_bytes_before == manifest_bytes_after
        assert prov_bytes_before == prov_bytes_after

    def test_high_risk_trusted_local_with_approval_allows(self, tmp_path: Path):
        """High-risk with trusted_local provenance + approval allows stable.
        Create directly in testing since high-risk can't go through activation."""
        store = _make_store(tmp_path)
        cap_id = "e2e-high-trusted"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="High Risk Trusted Local",
            description="High risk capability with trusted_local provenance.",
            body=VALID_BODY,
            risk_level="high",
        )
        doc = _set_testing_with_boundary(store, doc)

        target_dir = doc.directory
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(
            cap_id, "stable", scope="workspace", approval=approval,
        )

        assert result.applied is True
        assert result.to_maturity == "stable"

    def test_high_risk_no_approval_blocks_before_trust_gate(self, tmp_path: Path):
        """High risk without approval is blocked by CapabilityPolicy before
        trust gate is evaluated. Create directly in testing."""
        store = _make_store(tmp_path)
        cap_id = "e2e-high-no-appr"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="High Risk No Approval",
            description="High risk capability without approval.",
            body=VALID_BODY,
            risk_level="high",
        )
        doc = _set_testing_with_boundary(store, doc)

        target_dir = doc.directory
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        # Trust gate never reached — blocked by policy
        trust_decisions = [
            d for d in result.policy_decisions
            if d.get("source") == "CapabilityTrustPolicy"
        ]
        assert len(trust_decisions) == 0


# ── Flow D: flag-off compatibility ───────────────────────────────────────────


class TestFlowDFlagOffCompatibility:
    """When stable_promotion_trust_gate_enabled=false, old lifecycle behavior
    is unchanged — promotions succeed regardless of provenance state."""

    def test_flag_off_untrusted_provenance_promotes(self, tmp_path: Path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-flag-off-untrust"
        pkg_dir = tmp_path / "external_pkg_fo_u"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Set untrusted provenance
        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_UNTRUSTED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        # Flag off: trust_policy=None, trust_gate_enabled=False
        mgr = _make_lifecycle(store, trust_policy=None, trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "stable", scope="user")

        assert result.applied is True
        assert result.to_maturity == "stable"

    def test_flag_off_missing_provenance_promotes(self, tmp_path: Path):
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-flag-off-no-prov"
        pkg_dir = tmp_path / "external_pkg_fo_np"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        # Delete provenance
        (target_dir / "provenance.json").unlink()
        assert not (target_dir / "provenance.json").exists()

        # Flag off
        mgr = _make_lifecycle(store, trust_policy=None, trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "stable", scope="user")

        assert result.applied is True
        assert result.to_maturity == "stable"

    def test_flag_off_integrity_mismatch_promotes(self, tmp_path: Path):
        """When flag is off, even integrity mismatch doesn't block promotion
        (old behavior — trust gate not evaluated)."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-flag-off-mismatch"
        pkg_dir = tmp_path / "external_pkg_fo_m"
        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")

        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        write_provenance(
            target_dir,
            capability_id=cap_id,
            source_type="quarantine_activation",
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_MISMATCH,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        mgr = _make_lifecycle(store, trust_policy=None, trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "stable", scope="user")

        assert result.applied is True
        assert result.to_maturity == "stable"


# ── Flow E: legacy/manual low-risk missing provenance ────────────────────────


class TestFlowELegacyMissingProvenance:
    """Low-risk testing capabilities without provenance (legacy/manual
    non-imported). Trust gate enabled: warns but allows stable promotion."""

    def test_legacy_manual_low_risk_no_provenance_warns_allows(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "e2e-legacy-no-prov"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="Legacy Manual Cap",
            description="A manually created capability without provenance.",
            body=VALID_BODY,
            risk_level="low",
        )

        # Set to testing maturity without provenance
        doc = _set_testing_with_boundary(store, doc)

        assert not (doc.directory / "provenance.json").exists()

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        # Low risk + missing provenance: warns but allows (legacy exception)
        assert result.applied is True
        assert result.to_maturity == "stable"

        # Trust gate decision must be a warning (allowed=True, severity=warning)
        trust_decisions = [
            d for d in result.policy_decisions
            if d.get("source") == "CapabilityTrustPolicy"
        ]
        assert len(trust_decisions) >= 1
        assert trust_decisions[0]["allowed"] is True
        assert trust_decisions[0]["severity"] == "warning"

    def test_legacy_low_risk_no_provenance_stable_maturity_set(self, tmp_path: Path):
        """Verify the capability actually reaches stable maturity."""
        store = _make_store(tmp_path)
        cap_id = "e2e-legacy-stable"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="Legacy Stable Cap",
            description="Legacy capability reaching stable.",
            body=VALID_BODY,
            risk_level="low",
        )

        doc = _set_testing_with_boundary(store, doc)

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        re_read = store.get(cap_id, CapabilityScope.WORKSPACE)
        assert re_read.manifest.maturity.value == "stable"

    def test_legacy_medium_risk_no_provenance_blocks(self, tmp_path: Path):
        """Medium risk without provenance blocks stable promotion
        when trust gate is enabled."""
        store = _make_store(tmp_path)
        cap_id = "e2e-med-no-prov"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="Medium Risk No Prov",
            description="Medium risk capability without provenance.",
            body=VALID_BODY,
            risk_level="medium",
        )

        doc = _set_testing_with_boundary(store, doc)

        manifest_bytes_before = (doc.directory / "manifest.json").read_bytes()

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        manifest_bytes_after = (doc.directory / "manifest.json").read_bytes()
        assert manifest_bytes_before == manifest_bytes_after

    def test_legacy_high_risk_no_provenance_blocks(self, tmp_path: Path):
        """High risk without provenance blocks stable promotion
        when trust gate is enabled (even with approval)."""
        store = _make_store(tmp_path)
        cap_id = "e2e-high-no-prov"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="High Risk No Prov",
            description="High risk capability without provenance.",
            body=VALID_BODY,
            risk_level="high",
        )

        doc = _set_testing_with_boundary(store, doc)

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(
            cap_id, "stable", scope="workspace", approval=approval,
        )

        assert result.applied is False


# ── Cross-flow invariants ────────────────────────────────────────────────────


class TestCrossFlowInvariants:
    """Invariants that hold across all flows."""

    def test_provenance_never_contains_raw_system_paths(self, tmp_path: Path):
        """Even after full lifecycle, provenance must not leak raw filesystem paths."""
        store = _make_store(tmp_path)
        idx = _make_index(tmp_path)
        evaluator = _make_evaluator()
        policy = _make_policy()

        cap_id = "e2e-no-raw-path"
        pkg_dir = tmp_path / "external_pkg_path_leak"
        raw_path_str = str(pkg_dir.resolve())

        _write_external_package(pkg_dir, cap_id=cap_id, risk_level="low")
        qdir, audit, review, req = _full_import_flow(
            store, evaluator, policy, idx, pkg_dir, cap_id,
        )
        target_dir = _activate_to_testing(
            store, evaluator, policy, idx, cap_id, req,
        )

        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "stable", scope="user")
        assert result.applied is True

        # Check both provenance files
        for prov_path in [qdir / "provenance.json", target_dir / "provenance.json"]:
            raw = prov_path.read_text()
            assert raw_path_str not in raw, (
                f"Raw path leak in {prov_path}"
            )
            assert "/home/" not in raw, f"Filesystem path leak in {prov_path}"
            assert "/tmp/" not in raw, f"tmp path leak in {prov_path}"

    def test_no_runtime_behavior_changes(self, tmp_path: Path):
        """Verifies that CapabilityLifecycleManager with trust gate does not
        change behavior for non-stable transitions."""
        store = _make_store(tmp_path)
        cap_id = "e2e-no-runtime-change"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="Runtime No Change",
            description="Verify no runtime behavior change.",
            body=VALID_BODY,
            risk_level="low",
        )

        # draft -> testing (with trust gate enabled)
        mgr = _make_lifecycle(
            store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True,
        )
        result = mgr.apply_transition(cap_id, "testing", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "testing"

        # Trust gate should NOT be in policy_decisions for non-testing->stable
        trust_decisions = [
            d for d in result.policy_decisions
            if d.get("source") == "CapabilityTrustPolicy"
        ]
        assert len(trust_decisions) == 0, (
            "Trust gate evaluated for non-testing->stable transition"
        )

    def test_trust_policy_none_with_flag_true_no_effect(self, tmp_path: Path):
        """When trust_policy is None but flag is True, the trust gate check
        is skipped (no policy to evaluate)."""
        store = _make_store(tmp_path)
        cap_id = "e2e-tp-none"

        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            cap_id=cap_id,
            name="Trust Policy None",
            description="Testing with trust_policy=None.",
            body=VALID_BODY,
            risk_level="low",
        )

        doc = _set_testing_with_boundary(store, doc)

        mgr = _make_lifecycle(store, trust_policy=None, trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
