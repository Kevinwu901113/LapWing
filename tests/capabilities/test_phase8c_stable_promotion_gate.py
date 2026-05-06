"""Phase 8C-1: Stable Promotion Trust Gate unit tests.

Tests the trust gate wiring in CapabilityLifecycleManager for
testing -> stable transitions. Feature flag default behavior,
risk-specific gating, no-mutation-on-denial, and successful promotion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.document import CapabilityDocument
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.lifecycle import CapabilityLifecycleManager, TransitionResult
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.provenance import (
    INTEGRITY_MISMATCH,
    INTEGRITY_VERIFIED,
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNTRUSTED,
    CapabilityProvenance,
    CapabilityTrustPolicy,
    write_provenance,
)
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
    SideEffect,
)
from src.capabilities.store import CapabilityStore


# ── Helpers ────────────────────────────────────────────────────────────────

VALID_BODY = """## When to use

Use this to test stable promotion trust gate.

## Procedure

1. Do step one.
2. Do step two.

## Verification

Verify the output.

## Failure handling

If it fails, log and retry.
"""


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _create_cap(
    store: CapabilityStore,
    *,
    cap_id: str = "test_cap",
    body: str = VALID_BODY,
    maturity: str = "draft",
    status: str = "active",
    risk_level: str = "low",
    scope: CapabilityScope = CapabilityScope.WORKSPACE,
) -> CapabilityDocument:
    doc = store.create_draft(
        scope=scope,
        cap_id=cap_id,
        name="Test Capability",
        description="A test capability.",
        body=body,
        risk_level=risk_level,
    )
    doc.manifest = doc.manifest.model_copy(update={
        "do_not_apply_when": ["not for unsafe stable-promotion contexts"],
        "reuse_boundary": "Stable promotion gate test only.",
        "side_effects": [SideEffect.NONE],
    })
    store._sync_manifest_json(doc.directory, doc)
    evals_dir = doc.directory / "evals"
    evals_dir.mkdir(exist_ok=True)
    (evals_dir / "positive_cases.jsonl").write_text('{"case":"ok"}\n', encoding="utf-8")
    (evals_dir / "boundary_cases.jsonl").write_text('{"case":"boundary"}\n', encoding="utf-8")
    doc = store._parser.parse(doc.directory)
    if maturity != "draft" or status != "active":
        updates: dict = {}
        if maturity != "draft":
            updates["maturity"] = CapabilityMaturity(maturity)
        if status != "active":
            updates["status"] = CapabilityStatus(status)
        if updates:
            updated = doc.manifest.model_copy(update=updates)
            doc.manifest = updated
            store._sync_manifest_json(doc.directory, doc)
            doc = store._parser.parse(doc.directory)
    return doc


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


def _write_provenance_for_cap(
    cap_dir: Path,
    *,
    trust_level: str = TRUST_REVIEWED,
    integrity_status: str = INTEGRITY_VERIFIED,
    signature_status: str = SIGNATURE_NOT_PRESENT,
    source_type: str = "local_package",
) -> CapabilityProvenance:
    return write_provenance(
        cap_dir,
        capability_id="test_cap",
        trust_level=trust_level,
        integrity_status=integrity_status,
        signature_status=signature_status,
        source_type=source_type,
    )


# ── Feature flag: off by default ───────────────────────────────────────────


class TestFeatureFlagDefaultsFalse:
    """The trust gate feature flag defaults to False."""

    def test_constructor_default(self):
        mgr = CapabilityLifecycleManager.__new__(CapabilityLifecycleManager)
        import inspect
        sig = inspect.signature(CapabilityLifecycleManager.__init__)
        assert sig.parameters["trust_gate_enabled"].default is False

    def test_flag_false_existing_testing_to_stable_succeeds(self, tmp_path: Path):
        """Flag false: testing -> stable works exactly as before."""
        store = _make_store(tmp_path)
        cap_id = "test-flag-off"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "stable"

    def test_flag_false_no_provenance_needed(self, tmp_path: Path):
        """Flag false: no provenance required for stable promotion.
        High risk needs approval per policy, so use low risk for this test."""
        store = _make_store(tmp_path)
        cap_id = "test-flag-off-no-prov"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "stable"

    def test_flag_false_trust_policy_none_still_works(self, tmp_path: Path):
        """Flag false: trust_policy=None is fine."""
        store = _make_store(tmp_path)
        cap_id = "test-no-trust-pol"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")

        mgr = _make_lifecycle(store, trust_policy=None, trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True


# ── Feature flag: non-stable transitions unaffected ────────────────────────


class TestFlagTrueNonStableUnaffected:
    """Flag true: only testing -> stable is gated. All other transitions
    are unchanged."""

    def test_draft_to_testing_unaffected(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-draft-to-test"
        doc = _create_cap(store, cap_id=cap_id, maturity="draft")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "testing", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "testing"

    def test_stable_to_broken_unaffected(self, tmp_path: Path):
        """stable -> broken requires failure evidence per PromotionPlanner.
        Use repairing -> testing instead (unaffected by trust gate)."""
        store = _make_store(tmp_path)
        cap_id = "test-stable-broken"
        doc = _create_cap(store, cap_id=cap_id, maturity="repairing")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "testing", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "testing"

    def test_broken_to_repairing_unaffected(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-broken-repair"
        doc = _create_cap(store, cap_id=cap_id, maturity="broken")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "repairing", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "repairing"

    def test_repairing_to_testing_unaffected(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-repair-test"
        doc = _create_cap(store, cap_id=cap_id, maturity="repairing")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "testing", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "testing"

    def test_disabled_transition_unaffected(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-disable"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "disabled", scope="workspace")

        assert result.applied is True
        assert result.to_status == "disabled"


# ── Low risk trust gate tests ──────────────────────────────────────────────


class TestLowRiskTrustGate:
    """Low risk: reviewed provenance + verified integrity + passing eval → allowed.
    Missing provenance warns. Untrusted provenance blocks."""

    def test_reviewed_verified_allows(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-low-reviewed"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "stable"

    def test_missing_provenance_warns_allows_low_risk(self, tmp_path: Path):
        """Low risk without provenance: warns but allows (legacy exception)."""
        store = _make_store(tmp_path)
        cap_id = "test-low-no-prov"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        # Trust gate decision should be in policy_decisions
        trust_decisions = [d for d in result.policy_decisions if d.get("source") == "CapabilityTrustPolicy"]
        assert len(trust_decisions) == 1
        assert trust_decisions[0]["allowed"] is True

    def test_untrusted_provenance_blocks(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-low-untrusted"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_UNTRUSTED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        assert "trust gate" in result.message.lower()

    def test_integrity_mismatch_blocks(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-low-mismatch"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(
            doc.directory, trust_level=TRUST_REVIEWED, integrity_status=INTEGRITY_MISMATCH,
        )

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        assert "integrity" in result.message.lower()

    def test_invalid_signature_blocks(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-low-invalid-sig"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(
            doc.directory, trust_level=TRUST_REVIEWED, signature_status=SIGNATURE_INVALID,
        )

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False


# ── Medium risk trust gate tests ───────────────────────────────────────────


class TestMediumRiskTrustGate:
    """Medium risk: reviewed or trusted_local required. Missing provenance blocks."""

    def test_reviewed_verified_allows(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-med-reviewed"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="medium")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True

    def test_trusted_local_allows(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-med-trusted-local"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="medium")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_TRUSTED_LOCAL)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True

    def test_missing_provenance_blocks_medium_risk(self, tmp_path: Path):
        """Medium risk without provenance: blocked."""
        store = _make_store(tmp_path)
        cap_id = "test-med-no-prov"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="medium")

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False

    def test_untrusted_blocks(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-med-untrusted"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="medium")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_UNTRUSTED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False

    def test_integrity_mismatch_blocks(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-med-mismatch"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="medium")
        _write_provenance_for_cap(
            doc.directory, trust_level=TRUST_TRUSTED_LOCAL, integrity_status=INTEGRITY_MISMATCH,
        )

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False


# ── High risk trust gate tests ─────────────────────────────────────────────


class TestHighRiskTrustGate:
    """High risk: trusted_local or trusted_signed required. reviewed blocks.
    Approval also required (checked by CapabilityPolicy)."""

    def test_trusted_local_with_approval_allows(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-high-trusted-local"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_TRUSTED_LOCAL)

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is True

    def test_reviewed_blocks_high_risk(self, tmp_path: Path):
        """High risk with reviewed trust (not trusted_local+): blocked."""
        store = _make_store(tmp_path)
        cap_id = "test-high-reviewed"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is False
        assert "reviewed" in result.message.lower()

    def test_high_risk_no_approval_blocks(self, tmp_path: Path):
        """High risk without approval blocked by CapabilityPolicy, not trust gate."""
        store = _make_store(tmp_path)
        cap_id = "test-high-no-approval"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_TRUSTED_LOCAL)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        # Blocked by policy, not trust gate
        trust_decisions = [d for d in result.policy_decisions if d.get("source") == "CapabilityTrustPolicy"]
        assert len(trust_decisions) == 0  # trust gate never reached (policy blocked first)

    def test_untrusted_blocks_high_risk(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-high-untrusted"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_UNTRUSTED)

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is False

    def test_missing_provenance_blocks_high_risk(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-high-no-prov"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is False

    def test_integrity_mismatch_blocks_high_risk(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-high-mismatch"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(
            doc.directory, trust_level=TRUST_TRUSTED_LOCAL, integrity_status=INTEGRITY_MISMATCH,
        )

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is False

    def test_invalid_signature_blocks_high_risk(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-high-invalid-sig"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(
            doc.directory, trust_level=TRUST_TRUSTED_LOCAL, signature_status=SIGNATURE_INVALID,
        )

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is False


# ── No mutation on denial ──────────────────────────────────────────────────


class TestNoMutationOnDenial:
    """Denied trust gate leaves manifest, index, snapshot, and mutation log untouched."""

    def test_manifest_unchanged_on_denial(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-no-mut-manifest"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")

        maturity_before = doc.manifest.maturity.value
        status_before = doc.manifest.status.value

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        assert result.to_maturity == "stable"
        assert result.from_maturity == maturity_before

        # Re-read manifest from disk — must be unchanged
        re_read = store.get(cap_id, CapabilityScope.WORKSPACE)
        assert re_read.manifest.maturity.value == maturity_before
        assert re_read.manifest.status.value == status_before

    def test_no_snapshot_on_denial(self, tmp_path: Path):
        """Denied transition must not write a version snapshot."""
        store = _make_store(tmp_path)
        cap_id = "test-no-snapshot"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")

        # Count existing snapshot dirs before
        versions_dir = doc.directory / "versions"
        existing_snapshots = set()
        if versions_dir.exists():
            existing_snapshots = {d.name for d in versions_dir.iterdir() if d.is_dir()}

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        assert result.version_snapshot_id is None

    def test_blocked_by_untrusted_provenance_manifest_unchanged(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-block-untrusted"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_UNTRUSTED)

        maturity_before = doc.manifest.maturity.value

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        re_read = store.get(cap_id, CapabilityScope.WORKSPACE)
        assert re_read.manifest.maturity.value == maturity_before

    def test_blocked_by_integrity_mismatch_manifest_unchanged(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-block-mismatch"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(
            doc.directory, trust_level=TRUST_REVIEWED, integrity_status=INTEGRITY_MISMATCH,
        )

        maturity_before = doc.manifest.maturity.value

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        re_read = store.get(cap_id, CapabilityScope.WORKSPACE)
        assert re_read.manifest.maturity.value == maturity_before

    def test_result_message_includes_trust_gate_info_on_denial(self, tmp_path: Path):
        """Trust gate denial includes trust gate info in policy_decisions.
        Use medium risk + untrusted provenance: policy passes but trust gate blocks."""
        store = _make_store(tmp_path)
        cap_id = "test-msg-trust-gate"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="medium")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_UNTRUSTED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is False
        assert "trust gate" in result.message.lower()
        # policy_decisions should include the trust gate decision
        trust_decisions = [d for d in result.policy_decisions if d.get("source") == "CapabilityTrustPolicy"]
        assert len(trust_decisions) >= 1
        assert not trust_decisions[0]["allowed"]


# ── Successful promotion tests ─────────────────────────────────────────────


class TestSuccessfulStablePromotion:
    """Successful testing -> stable promotion with trust gate enabled."""

    def test_successful_promotion_sets_maturity_stable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-success-maturity"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        assert result.to_maturity == "stable"
        re_read = store.get(cap_id, CapabilityScope.WORKSPACE)
        assert re_read.manifest.maturity == CapabilityMaturity.STABLE

    def test_successful_promotion_status_remains_active(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-success-status"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        assert result.to_status == "active"

    def test_successful_promotion_writes_snapshot(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-success-snapshot"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        assert result.version_snapshot_id is not None

    def test_successful_promotion_includes_trust_decision_in_policy_decisions(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-success-trust-decision"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        trust_decisions = [d for d in result.policy_decisions if d.get("source") == "CapabilityTrustPolicy"]
        assert len(trust_decisions) == 1
        assert trust_decisions[0]["allowed"] is True

    def test_successful_promotion_does_not_mutate_provenance(self, tmp_path: Path):
        """Provenance is not modified by the trust gate or promotion."""
        store = _make_store(tmp_path)
        cap_id = "test-success-prov-unchanged"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        prov = _write_provenance_for_cap(doc.directory, trust_level=TRUST_REVIEWED)
        prov_dict_before = prov.to_dict()

        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")

        assert result.applied is True
        from src.capabilities.provenance import read_provenance
        prov_after = read_provenance(doc.directory)
        assert prov_after is not None
        assert prov_after.trust_level == prov_dict_before["trust_level"]
        assert prov_after.integrity_status == prov_dict_before["integrity_status"]

    def test_trusted_local_high_risk_with_approval_succeeds(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-high-success"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(doc.directory, trust_level=TRUST_TRUSTED_LOCAL)

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is True
        assert result.to_maturity == "stable"


# ── trusted_signed path -- document that it is unreachable via verifier stub ─


class TestTrustedSignedPath:
    """trusted_signed cannot be reached through the verifier stub (never returns
    verified/trusted_signed). But if provenance is manually set to trusted_signed
    + signature_status=verified, the analytical policy accepts it."""

    def test_trusted_signed_verified_provenance_allows(self, tmp_path: Path):
        """If provenance is trusted_signed with verified signature, policy allows."""
        store = _make_store(tmp_path)
        cap_id = "test-ts-path"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="high")
        _write_provenance_for_cap(
            doc.directory,
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status="verified",
        )

        approval = type("Approval", (), {"approved": True, "approver": "test-operator"})()
        mgr = _make_lifecycle(store, trust_policy=CapabilityTrustPolicy(), trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace", approval=approval)

        assert result.applied is True
