"""Phase 8C-1: Stable Promotion Trust Gate integration tests.

End-to-end tests with LifecycleManager wired through the trust gate.
Tests full lifecycle flows from import through testing to stable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.provenance import (
    INTEGRITY_VERIFIED,
    SIGNATURE_NOT_PRESENT,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_UNTRUSTED,
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

Use this to test stable promotion trust gate integration.

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


def _create_cap(store: CapabilityStore, *, cap_id: str, maturity: str = "draft",
                status: str = "active", risk_level: str = "low"):
    doc = store.create_draft(
        scope=CapabilityScope.WORKSPACE,
        cap_id=cap_id,
        name="Test Capability",
        description="A test capability.",
        body=VALID_BODY,
        risk_level=risk_level,
    )
    doc.manifest = doc.manifest.model_copy(update={
        "do_not_apply_when": ["not for unsafe stable-promotion contexts"],
        "reuse_boundary": "Stable promotion integration test only.",
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


def _make_lifecycle(store: CapabilityStore, *, trust_gate_enabled: bool = False):
    tp = CapabilityTrustPolicy() if trust_gate_enabled else None
    return CapabilityLifecycleManager(
        store=store,
        evaluator=CapabilityEvaluator(),
        policy=CapabilityPolicy(),
        planner=PromotionPlanner(),
        trust_policy=tp,
        trust_gate_enabled=trust_gate_enabled,
    )


# ── Full lifecycle: draft → testing → stable (trust gate enabled) ──────────


class TestFullLifecycleDraftToStable:
    """End-to-end: draft -> testing -> stable with trust gate enabled."""

    def test_full_path_low_risk_reviewed_provenance(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "e2e-low-reviewed"

        # 1. Create draft
        doc = _create_cap(store, cap_id=cap_id, maturity="draft", risk_level="low")

        # 2. Promote to testing
        mgr = _make_lifecycle(store, trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "testing", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "testing"

        # 3. Add provenance with reviewed trust
        doc = store.get(cap_id, CapabilityScope.WORKSPACE)
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
            source_type="local_package",
        )

        # 4. Promote to stable with trust gate enabled
        mgr_gated = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "stable"

        # 5. Verify final state
        doc = store.get(cap_id, CapabilityScope.WORKSPACE)
        assert doc.manifest.maturity == CapabilityMaturity.STABLE
        assert doc.manifest.status == CapabilityStatus.ACTIVE

    def test_full_path_medium_risk_trusted_local(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "e2e-med-trusted"

        # 1. Create draft
        doc = _create_cap(store, cap_id=cap_id, maturity="draft", risk_level="medium")

        # 2. Promote to testing
        mgr = _make_lifecycle(store, trust_gate_enabled=False)
        result = mgr.apply_transition(cap_id, "testing", scope="workspace")
        assert result.applied is True

        # 3. Add trusted_local provenance
        doc = store.get(cap_id, CapabilityScope.WORKSPACE)
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
            source_type="quarantine_activation",
        )

        # 4. Promote to stable
        mgr_gated = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "stable"


# ── Integration: trust gate blocks but doesn't break subsequent attempts ────


class TestTrustGateBlockRetry:
    """Trust gate denial is clean; subsequent fix allows retry."""

    def test_fix_untrusted_provenance_then_retry(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "retry-untrusted"

        # 1. Create testing with untrusted provenance
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_UNTRUSTED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        # 2. Attempt stable promotion — blocked
        mgr_gated = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is False

        # 3. Fix provenance
        doc = store.get(cap_id, CapabilityScope.WORKSPACE)
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
            source_type="local_package",
        )

        # 4. Retry — succeeds
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "stable"

    def test_fix_integrity_mismatch_then_retry(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "retry-mismatch"

        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status="mismatch",
            signature_status=SIGNATURE_NOT_PRESENT,
        )

        # Blocked by mismatch
        mgr_gated = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is False

        # Fix integrity
        doc = store.get(cap_id, CapabilityScope.WORKSPACE)
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
            source_type="local_package",
        )

        # Retry — succeeds
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True


# ── Integration: transition_result has correct shape ────────────────────────


class TestTransitionResultShape:
    """TransitionResult carries correct fields for trust gate decisions."""

    def test_policy_decisions_include_trust_gate_source(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "shape-trust-source"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
        )

        mgr_gated = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True

        # Trust gate decision included
        trust_decisions = [d for d in result.policy_decisions if d.get("source") == "CapabilityTrustPolicy"]
        assert len(trust_decisions) == 1
        td = trust_decisions[0]
        assert "code" in td
        assert "message" in td
        assert "severity" in td
        assert "allowed" in td
        assert "details" in td

    def test_blocking_findings_empty_on_trust_gate_success(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "shape-blocking"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
        )

        mgr_gated = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True
        assert result.blocking_findings == []

    def test_transition_result_has_correct_from_to_fields(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "shape-from-to"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
        )

        mgr_gated = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_gated.apply_transition(cap_id, "stable", scope="workspace")

        assert result.capability_id == cap_id
        assert result.from_maturity == "testing"
        assert result.to_maturity == "stable"
        assert result.from_status == "active"
        assert result.to_status == "active"


# ── Integration: flag transitions ──────────────────────────────────────────


class TestFlagTransitions:
    """Toggling the feature flag does not break anything."""

    def test_enable_disable_flag_does_not_affect_non_stable_transitions(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "flag-toggle"

        # Create at draft
        doc = _create_cap(store, cap_id=cap_id, maturity="draft", risk_level="low")

        # With flag off: promote to testing
        mgr_off = _make_lifecycle(store, trust_gate_enabled=False)
        result = mgr_off.apply_transition(cap_id, "testing", scope="workspace")
        assert result.applied is True

        # With flag on: promote to stable (add provenance first)
        doc = store.get(cap_id, CapabilityScope.WORKSPACE)
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
        )
        mgr_on = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr_on.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True

        # Verify final state
        doc = store.get(cap_id, CapabilityScope.WORKSPACE)
        assert doc.manifest.maturity == CapabilityMaturity.STABLE
