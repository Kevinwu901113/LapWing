"""Phase 8A-1: CapabilityTrustPolicy tests — analytical, non-gating decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.capabilities.provenance import (
    INTEGRITY_MISMATCH,
    INTEGRITY_UNKNOWN,
    INTEGRITY_VERIFIED,
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_VERIFIED,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNTRUSTED,
    CapabilityProvenance,
    CapabilityTrustPolicy,
    TrustDecision,
)
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)


def _make_manifest(**overrides) -> CapabilityManifest:
    kwargs = dict(
        id="test-cap",
        name="Test Cap",
        description="Test capability.",
        type=CapabilityType.SKILL,
        scope=CapabilityScope.USER,
        version="1.0.0",
        maturity=CapabilityMaturity.TESTING,
        status=CapabilityStatus.ACTIVE,
        risk_level=CapabilityRiskLevel.LOW,
    )
    kwargs.update(overrides)
    return CapabilityManifest(**kwargs)


def _make_provenance(**overrides) -> CapabilityProvenance:
    kwargs = dict(
        provenance_id="prov_test",
        capability_id="test-cap",
        trust_level=TRUST_UNTRUSTED,
        integrity_status=INTEGRITY_VERIFIED,
        signature_status=SIGNATURE_NOT_PRESENT,
    )
    kwargs.update(overrides)
    return CapabilityProvenance(**kwargs)


class TestEvaluateProvenance:
    """evaluate_provenance() — trust analysis from provenance data."""

    def test_missing_provenance_warns(self):
        policy = CapabilityTrustPolicy()
        d = policy.evaluate_provenance(None)
        assert d.allowed is True
        assert d.severity == "warning"
        assert d.code == "provenance_missing"

    def test_verified_provenance_allows(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(trust_level=TRUST_REVIEWED, integrity_status=INTEGRITY_VERIFIED)
        d = policy.evaluate_provenance(p)
        assert d.allowed is True
        assert d.code == "provenance_evaluated"

    def test_trusted_signed_without_verified_signature_warns(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        d = policy.evaluate_provenance(p)
        assert d.allowed is True
        assert d.severity == "warning"
        assert "trusted_signed" in d.code

    def test_trusted_signed_with_verified_signature_allows(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        d = policy.evaluate_provenance(p)
        assert d.allowed is True
        assert d.code == "provenance_evaluated"

    def test_deterministic_same_input_same_output(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance()
        d1 = policy.evaluate_provenance(p)
        d2 = policy.evaluate_provenance(p)
        assert d1.code == d2.code
        assert d1.allowed == d2.allowed
        assert d1.severity == d2.severity

    def test_non_mutating(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(integrity_status=INTEGRITY_VERIFIED)
        orig_status = p.integrity_status
        policy.evaluate_provenance(p)
        assert p.integrity_status == orig_status


class TestCanActivateFromQuarantine:
    """can_activate_from_quarantine() — trust gates for activation."""

    def test_missing_provenance_warns(self):
        policy = CapabilityTrustPolicy()
        d = policy.can_activate_from_quarantine(None)
        assert d.allowed is True
        assert d.severity == "warning"

    def test_integrity_mismatch_denies(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(integrity_status=INTEGRITY_MISMATCH)
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is False
        assert "integrity" in d.code.lower()

    def test_signature_invalid_denies(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(signature_status=SIGNATURE_INVALID)
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is False
        assert "signature" in d.code.lower()

    def test_untrusted_without_review_audit_warns(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(trust_level=TRUST_UNTRUSTED)
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is True
        assert d.severity == "warning"

    def test_untrusted_with_review_audit_allows(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(trust_level=TRUST_UNTRUSTED)
        audit = {"passed": True}
        review = {"review_status": "approved_for_testing"}
        d = policy.can_activate_from_quarantine(p, audit_result=audit, review=review)
        assert d.allowed is True
        assert d.severity == "info"

    def test_reviewed_allows(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(trust_level=TRUST_REVIEWED, integrity_status=INTEGRITY_VERIFIED)
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is True

    def test_trusted_local_allows(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(trust_level=TRUST_TRUSTED_LOCAL)
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is True

    def test_trusted_signed_allows(self):
        policy = CapabilityTrustPolicy()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        d = policy.can_activate_from_quarantine(p)
        assert d.allowed is True


class TestCanRetrieve:
    """can_retrieve() — always allowed in Phase 8A-1."""

    def test_missing_provenance_warns_but_allows(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        d = policy.can_retrieve(manifest, None)
        assert d.allowed is True
        assert d.severity == "warning"

    def test_present_provenance_allows(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(trust_level=TRUST_REVIEWED, integrity_status=INTEGRITY_VERIFIED)
        d = policy.can_retrieve(manifest, p)
        assert d.allowed is True
        assert d.severity == "info"

    def test_integrity_mismatch_warns_but_allows(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(integrity_status=INTEGRITY_MISMATCH)
        d = policy.can_retrieve(manifest, p)
        assert d.allowed is True
        assert d.severity == "warning"

    def test_never_denies_retrieval(self):
        """Phase 8A-1: can_retrieve never blocks retrieval."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        for trust in [TRUST_UNTRUSTED, TRUST_REVIEWED, TRUST_TRUSTED_LOCAL, TRUST_TRUSTED_SIGNED]:
            for integrity in [INTEGRITY_UNKNOWN, INTEGRITY_VERIFIED, INTEGRITY_MISMATCH]:
                p = _make_provenance(trust_level=trust, integrity_status=integrity)
                d = policy.can_retrieve(manifest, p)
                assert d.allowed is True, f"Blocked: trust={trust}, integrity={integrity}"


class TestCanPromoteToStable:
    """can_promote_to_stable() — trust gates for stable promotion."""

    def test_missing_provenance_denies(self):
        """Missing provenance denies for medium+ risk (Phase 8C-1 risk-gating)."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="medium")
        d = policy.can_promote_to_stable(manifest, None, risk_level="medium")
        assert d.allowed is False
        assert "no_provenance" in d.code

    def test_missing_provenance_low_risk_warns(self):
        """Missing provenance warns for low risk legacy/manual (Phase 8C-1)."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="low")
        d = policy.can_promote_to_stable(manifest, None, risk_level="low")
        assert d.allowed is True
        assert "low_risk" in d.code

    def test_untrusted_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(trust_level=TRUST_UNTRUSTED, integrity_status=INTEGRITY_VERIFIED)
        d = policy.can_promote_to_stable(manifest, p)
        assert d.allowed is False
        assert "trust" in d.code.lower()

    def test_unknown_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(trust_level=TRUST_UNTRUSTED)
        d = policy.can_promote_to_stable(manifest, p)
        assert d.allowed is False

    def test_integrity_mismatch_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(trust_level=TRUST_TRUSTED_LOCAL, integrity_status=INTEGRITY_MISMATCH)
        d = policy.can_promote_to_stable(manifest, p)
        assert d.allowed is False
        assert "integrity" in d.code.lower()

    def test_signature_invalid_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_INVALID,
        )
        d = policy.can_promote_to_stable(manifest, p)
        assert d.allowed is False
        assert "signature" in d.code.lower()

    def test_reviewed_warns(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(trust_level=TRUST_REVIEWED, integrity_status=INTEGRITY_VERIFIED)
        d = policy.can_promote_to_stable(manifest, p)
        assert d.allowed is True
        assert d.severity == "warning"

    def test_trusted_local_allows(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(trust_level=TRUST_TRUSTED_LOCAL, integrity_status=INTEGRITY_VERIFIED)
        d = policy.can_promote_to_stable(manifest, p)
        assert d.allowed is True
        assert d.severity == "info"

    def test_trusted_signed_with_verified_signature_allows(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_VERIFIED,
        )
        d = policy.can_promote_to_stable(manifest, p)
        assert d.allowed is True

    def test_deterministic_same_input_same_output(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        p = _make_provenance(trust_level=TRUST_TRUSTED_LOCAL, integrity_status=INTEGRITY_VERIFIED)
        d1 = policy.can_promote_to_stable(manifest, p)
        d2 = policy.can_promote_to_stable(manifest, p)
        assert d1.code == d2.code
        assert d1.allowed == d2.allowed
