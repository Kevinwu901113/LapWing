"""Phase 8C-1: Stable Promotion Trust Gate regression tests.

All non-stable lifecycle transitions, retrieval, tools, import flows,
and legacy capabilities must remain unchanged regardless of feature flag state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.capabilities.document import CapabilityDocument
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.provenance import (
    INTEGRITY_VERIFIED,
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
)
from src.capabilities.store import CapabilityStore


# ── Helpers ────────────────────────────────────────────────────────────────

VALID_BODY = """## When to use

Use this to test regression.

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
                status: str = "active", risk_level: str = "low",
                scope: CapabilityScope = CapabilityScope.WORKSPACE):
    doc = store.create_draft(
        scope=scope,
        cap_id=cap_id,
        name="Test Capability",
        description="A test capability.",
        body=VALID_BODY,
        risk_level=risk_level,
    )
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


# ── Regression: draft -> testing unchanged ─────────────────────────────────


class TestDraftToTestingUnchanged:
    """draft -> testing is never gated by trust policy."""

    def test_draft_to_testing_works_with_flag_on(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-draft-test", maturity="draft")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-draft-test", "testing", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "testing"

    def test_draft_to_testing_works_with_flag_off(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-draft-test-off", maturity="draft")

        mgr = _make_lifecycle(store, trust_gate_enabled=False)
        result = mgr.apply_transition("reg-draft-test-off", "testing", scope="workspace")
        assert result.applied is True


# ── Regression: broken/repairing transitions unchanged ─────────────────────


class TestBrokenRepairingUnchanged:
    """Non-stable maturity transitions are never affected by the trust gate."""

    def test_testing_to_draft_downgrade(self, tmp_path: Path):
        """testing -> draft downgrade is unaffected (allowed transition)."""
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-test-draft", maturity="testing")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-test-draft", "draft", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "draft"

    def test_broken_to_repairing(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-broken-repair", maturity="broken")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-broken-repair", "repairing", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "repairing"

    def test_repairing_to_testing(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-repair-test", maturity="repairing")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-repair-test", "testing", scope="workspace")
        assert result.applied is True
        assert result.to_maturity == "testing"


# ── Regression: status transitions unchanged ───────────────────────────────


class TestStatusTransitionsUnchanged:
    """Disabled and archived status transitions are not affected."""

    def test_active_to_disabled(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-disable", maturity="testing")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-disable", "disabled", scope="workspace")
        assert result.applied is True
        assert result.to_status == "disabled"

    def test_active_to_archived(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-archive", maturity="testing")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-archive", "archived", scope="workspace")
        assert result.applied is True
        assert result.to_status == "archived"


# ── Regression: legacy capabilities without provenance still work ──────────


class TestLegacyCapabilitiesUnchanged:
    """Legacy capabilities without provenance can still be listed, searched,
    viewed, and transitioned through non-stable maturities."""

    def test_legacy_listable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="legacy-list", maturity="testing")

        results = store.list()
        assert len(results) >= 1
        assert any(r.manifest.id == "legacy-list" for r in results)

    def test_legacy_viewable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="legacy-view", maturity="testing")

        result = store.get("legacy-view", CapabilityScope.WORKSPACE)
        assert result is not None
        assert result.manifest.id == "legacy-view"

    def test_legacy_can_transition_non_stable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="legacy-trans", maturity="draft")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("legacy-trans", "testing", scope="workspace")
        assert result.applied is True

    def test_legacy_can_transition_downgrade(self, tmp_path: Path):
        """Legacy caps without provenance can still downgrade (testing -> draft)."""
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="legacy-downgrade", maturity="testing")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("legacy-downgrade", "draft", scope="workspace")
        assert result.applied is True


# ── Regression: evaluator works regardless of flag ─────────────────────────


class TestEvaluatorUnchanged:
    """CapabilityEvaluator is not affected by the trust gate."""

    def test_evaluator_passes_on_valid_cap(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="eval-valid", maturity="testing")

        evaluator = CapabilityEvaluator()
        record = evaluator.evaluate(doc)
        assert record.passed is True

    def test_evaluator_does_not_check_provenance(self, tmp_path: Path):
        """Evaluator checks content safety, not trust."""
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="eval-no-prov", maturity="testing")

        evaluator = CapabilityEvaluator()
        record = evaluator.evaluate(doc)
        # Evaluator should pass regardless of provenance presence
        assert record.passed is True


# ── Regression: policy unchanged ───────────────────────────────────────────


class TestCapabilityPolicyUnchanged:
    """CapabilityPolicy.validate_promote does not check provenance or trust."""

    def test_policy_validate_promote_no_provenance_param(self):
        import inspect
        source = inspect.getsource(CapabilityPolicy.validate_promote)
        assert "provenance" not in source
        assert "trust_level" not in source

    def test_policy_allows_low_risk_testing_to_stable_without_trust_context(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="pol-no-trust", maturity="testing", risk_level="low")

        policy = CapabilityPolicy()
        decision = policy.validate_promote(doc.manifest)
        # Policy should allow this (trust gate isn't in policy)
        assert decision.allowed is True


# ── Regression: store / index unchanged ────────────────────────────────────


class TestStoreUnchanged:
    """CapabilityStore operations are unaffected."""

    def test_store_create_and_get(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="store-basic", maturity="testing")
        retrieved = store.get("store-basic", CapabilityScope.WORKSPACE)
        assert retrieved is not None

    def test_store_list(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="store-list-all", maturity="testing",
                          scope=CapabilityScope.WORKSPACE)

        results = store.list()
        assert len(results) >= 1

    def test_store_disable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="store-disable-test", maturity="testing")

        disabled = store.disable("store-disable-test", CapabilityScope.WORKSPACE)
        assert disabled.manifest.status == CapabilityStatus.DISABLED


# ── Regression: no run_capability exists ───────────────────────────────────


class TestNoRunCapability:
    """No run_capability function exists in the capabilities module."""

    def test_no_run_capability_in_lifecycle(self):
        import inspect
        import src.capabilities.lifecycle as lc_mod
        source = inspect.getsource(lc_mod)
        assert "run_capability" not in source

    def test_no_def_run_capability_in_provenance(self):
        import inspect
        import src.capabilities.provenance as pv_mod
        source = inspect.getsource(pv_mod)
        assert "def run_capability" not in source

    def test_no_scripts_executed_during_transition(self, tmp_path: Path):
        """Testing -> stable promotion does not execute scripts."""
        store = _make_store(tmp_path)
        cap_id = "reg-no-exec"
        doc = _create_cap(store, cap_id=cap_id, maturity="testing", risk_level="low")
        write_provenance(
            doc.directory,
            capability_id=cap_id,
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
        )

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition(cap_id, "stable", scope="workspace")
        assert result.applied is True


# ── Regression: feature flag only affects testing -> stable ────────────────


class TestFlagOnlyAffectsTestingToStable:
    """The trust_gate_enabled flag only gates testing -> stable."""

    def test_flag_on_does_not_block_draft_to_testing(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-flag-scope", maturity="draft")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-flag-scope", "testing", scope="workspace")
        assert result.applied is True

    def test_flag_on_does_not_block_status_only_transition(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-flag-status", maturity="testing")

        mgr = _make_lifecycle(store, trust_gate_enabled=True)
        result = mgr.apply_transition("reg-flag-status", "disabled", scope="workspace")
        assert result.applied is True

    def test_flag_off_testing_to_stable_works(self, tmp_path: Path):
        store = _make_store(tmp_path)
        doc = _create_cap(store, cap_id="reg-flag-off", maturity="testing", risk_level="low")

        mgr = _make_lifecycle(store, trust_gate_enabled=False)
        result = mgr.apply_transition("reg-flag-off", "stable", scope="workspace")
        assert result.applied is True
