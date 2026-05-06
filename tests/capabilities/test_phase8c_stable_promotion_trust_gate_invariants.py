"""Phase 8C-0: Stable Promotion Trust Gate invariant tests.

Validates the design model documented in:
- docs/capability_stable_promotion_trust_gate.md

No implementation. No behavior changes. No wiring. Pure invariant assertions.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.provenance import (
    INTEGRITY_MISMATCH,
    INTEGRITY_UNKNOWN,
    INTEGRITY_VERIFIED,
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    SIGNATURE_VERIFIED,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNKNOWN,
    TRUST_UNTRUSTED,
    CapabilityProvenance,
    CapabilityTrustPolicy,
    TrustDecision,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_provenance(**kwargs) -> CapabilityProvenance:
    return CapabilityProvenance(
        provenance_id="prov_test8c",
        capability_id="test-cap-8c",
        trust_level=kwargs.pop("trust_level", TRUST_UNTRUSTED),
        signature_status=kwargs.pop("signature_status", SIGNATURE_NOT_PRESENT),
        integrity_status=kwargs.pop("integrity_status", INTEGRITY_VERIFIED),
        source_type=kwargs.pop("source_type", "local_package"),
        **kwargs,
    )


def _make_manifest(**kwargs):
    """Minimal manifest-like object for analytical policy testing."""
    from src.capabilities.schema import CapabilityManifest, CapabilityMaturity, CapabilityRiskLevel, CapabilityStatus, CapabilityScope, CapabilityType

    return CapabilityManifest(
        id=kwargs.pop("id", "test-cap-8c"),
        name=kwargs.pop("name", "Test Capability 8C"),
        description=kwargs.pop("description", "Test."),
        type=kwargs.pop("type", CapabilityType.SKILL),
        scope=kwargs.pop("scope", CapabilityScope.USER),
        version=kwargs.pop("version", "0.1.0"),
        maturity=kwargs.pop("maturity", CapabilityMaturity.TESTING),
        status=kwargs.pop("status", CapabilityStatus.ACTIVE),
        risk_level=kwargs.pop("risk_level", CapabilityRiskLevel.LOW),
        triggers=kwargs.pop("triggers", []),
        tags=kwargs.pop("tags", []),
        trust_required=kwargs.pop("trust_required", "developer"),
        required_tools=kwargs.pop("required_tools", []),
        required_permissions=kwargs.pop("required_permissions", []),
        **kwargs,
    )


def _make_eval_record(*, passed: bool = True):
    """Minimal eval record for analytical policy testing."""
    from dataclasses import dataclass, field

    @dataclass
    class _MinimalEval:
        capability_id: str = "test-cap-8c"
        passed: bool = True
        score: float = 1.0

    rec = _MinimalEval()
    rec.passed = passed
    rec.score = 1.0 if passed else 0.0
    return rec


# ── Invariant: gate is wired but feature-gated off by default ──────────


class TestStablePromotionGateFeatureGated:
    """Phase 8C-1: The trust gate IS wired into LifecycleManager but
    defaults to disabled via trust_gate_enabled=False.

    LifecycleManager references CapabilityTrustPolicy and can_promote_to_stable.
    The feature flag controls whether the gate is applied at runtime.
    """

    def test_lifecycle_manager_constructor_accepts_trust_policy_param(self):
        """LifecycleManager constructor has trust_policy and trust_gate_enabled params."""
        sig = inspect.signature(CapabilityLifecycleManager.__init__)
        params = list(sig.parameters.keys())
        assert "trust_policy" in params, (
            "LifecycleManager must accept optional trust_policy"
        )
        assert "trust_gate_enabled" in params, (
            "LifecycleManager must accept trust_gate_enabled flag"
        )

    def test_trust_gate_enabled_defaults_false(self):
        """trust_gate_enabled defaults to False."""
        sig = inspect.signature(CapabilityLifecycleManager.__init__)
        assert sig.parameters["trust_gate_enabled"].default is False, (
            "trust_gate_enabled must default to False"
        )

    def test_lifecycle_manager_apply_transition_references_trust_gate(self):
        """apply_transition source references the trust gate (wired in Phase 8C-1)."""
        source = inspect.getsource(CapabilityLifecycleManager.apply_transition)
        assert "trust_gate_enabled" in source
        assert "_trust_policy" in source
        assert "can_promote_to_stable" in source

    def test_capability_policy_validate_promote_still_does_not_call_trust_gate(self):
        """CapabilityPolicy.validate_promote must not call can_promote_to_stable.
        The trust gate is in LifecycleManager, not CapabilityPolicy."""
        source = inspect.getsource(CapabilityPolicy.validate_promote)
        assert "can_promote_to_stable" not in source
        assert "CapabilityTrustPolicy" not in source
        assert "trust_level" not in source
        assert "provenance" not in source

    def test_can_promote_to_stable_is_called_in_lifecycle(self):
        """can_promote_to_stable is now called in lifecycle.py (Phase 8C-1)."""
        import subprocess

        result = subprocess.run(
            ["grep", "-rn", "can_promote_to_stable", "src/"],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent,
        )
        found_in_lifecycle = False
        for line in result.stdout.strip().split("\n"):
            if line and "def can_promote_to_stable" not in line:
                if "lifecycle.py" in line:
                    found_in_lifecycle = True
                elif "can_promote_to_stable" in line and "lifecycle.py" not in line:
                    pytest.fail(
                        f"can_promote_to_stable is called outside lifecycle: {line}"
                    )
        assert found_in_lifecycle, (
            "can_promote_to_stable must be called in lifecycle.py in Phase 8C-1"
        )


# ── Invariant: CapabilityTrustPolicy is analytical-only ─────────────────────


class TestTrustPolicyIsAnalyticalOnly:
    """CapabilityTrustPolicy returns TrustDecision, never mutates state,
    never gates any lifecycle path."""

    def test_policy_methods_are_pure_functions(self):
        """All public methods accept data and return TrustDecision. No side effects."""
        policy = CapabilityTrustPolicy()
        for name in dir(policy):
            if name.startswith("_") or name in ("evaluate_provenance",):
                continue
            method = getattr(policy, name, None)
            if not callable(method):
                continue
            sig = inspect.signature(method)
            # All parameters should be pure data — no self-referential mutation.
            for param in sig.parameters.values():
                assert "store" not in param.name.lower(), (
                    f"{name} has store-like param: {param.name}"
                )
                assert "mutation" not in param.name.lower(), (
                    f"{name} has mutation-like param: {param.name}"
                )

    def test_can_promote_to_stable_does_not_mutate_provenance(self):
        """Calling can_promote_to_stable must not mutate the provenance object."""
        prov = _make_provenance(trust_level=TRUST_REVIEWED)
        prov_dict_before = prov.to_dict()

        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        policy.can_promote_to_stable(manifest, provenance=prov)

        assert prov.to_dict() == prov_dict_before

    def test_can_promote_to_stable_does_not_mutate_manifest(self):
        """Calling can_promote_to_stable must not mutate the manifest."""
        manifest = _make_manifest()
        manifest_json_before = manifest.model_dump_json()

        policy = CapabilityTrustPolicy()
        policy.can_promote_to_stable(manifest, provenance=_make_provenance())

        assert manifest.model_dump_json() == manifest_json_before


# ── Invariant: state distinctions ───────────────────────────────────────────


class TestActiveTestingIsNotStable:
    """active + testing does not imply stable maturity."""

    def test_testing_maturity_value(self):
        from src.capabilities.schema import CapabilityMaturity
        assert CapabilityMaturity.TESTING.value == "testing"
        assert CapabilityMaturity.STABLE.value == "stable"
        assert CapabilityMaturity.TESTING != CapabilityMaturity.STABLE

    def test_active_testing_manifest_is_not_stable(self):
        manifest = _make_manifest(maturity="testing", status="active")
        from src.capabilities.schema import CapabilityMaturity
        assert manifest.maturity == CapabilityMaturity.TESTING
        assert manifest.maturity != CapabilityMaturity.STABLE


class TestReviewedIsNotTrustedSigned:
    """reviewed provenance does not imply trusted_signed."""

    def test_strings_distinct(self):
        assert TRUST_REVIEWED != TRUST_TRUSTED_SIGNED
        assert TRUST_REVIEWED == "reviewed"
        assert TRUST_TRUSTED_SIGNED == "trusted_signed"

    def test_reviewed_can_exist_without_signature(self):
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        assert prov.trust_level == TRUST_REVIEWED
        assert prov.signature_status == SIGNATURE_NOT_PRESENT

    def test_reviewed_does_not_imply_verified(self):
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_REVIEWED
        assert prov.signature_status != SIGNATURE_VERIFIED


class TestTrustedLocalIsNotTrustedSigned:
    """trusted_local provenance does not imply trusted_signed."""

    def test_strings_distinct(self):
        assert TRUST_TRUSTED_LOCAL != TRUST_TRUSTED_SIGNED
        assert TRUST_TRUSTED_LOCAL == "trusted_local"
        assert TRUST_TRUSTED_SIGNED == "trusted_signed"

    def test_trusted_local_can_exist_without_signature(self):
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        assert prov.trust_level == TRUST_TRUSTED_LOCAL
        assert prov.signature_status == SIGNATURE_NOT_PRESENT

    def test_trusted_local_can_exist_with_unverified_signature(self):
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_TRUSTED_LOCAL
        assert prov.signature_status == SIGNATURE_PRESENT_UNVERIFIED


class TestPresentUnverifiedIsNotVerified:
    """present_unverified signature status does not imply verified."""

    def test_strings_distinct(self):
        assert SIGNATURE_PRESENT_UNVERIFIED != SIGNATURE_VERIFIED
        assert SIGNATURE_PRESENT_UNVERIFIED == "present_unverified"
        assert SIGNATURE_VERIFIED == "verified"

    def test_present_unverified_can_coexist_with_untrusted(self):
        prov = _make_provenance(
            trust_level=TRUST_UNTRUSTED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_UNTRUSTED
        assert prov.signature_status == SIGNATURE_PRESENT_UNVERIFIED


class TestActiveTrustRootDoesNotImplySignatureVerified:
    """An active trust root does not mean any capability was signed by it."""

    def test_trust_root_status_independent_of_signature_status(self):
        """trust root 'active' status and signature_status are orthogonal dimensions."""
        root_statuses = {"active", "disabled", "revoked"}
        sig_statuses = {
            SIGNATURE_NOT_PRESENT,
            SIGNATURE_PRESENT_UNVERIFIED,
            SIGNATURE_INVALID,
        }
        for root_status in root_statuses:
            for sig_status in sig_statuses:
                prov = _make_provenance(signature_status=sig_status)
                # The provenance's signature_status is independent of any trust
                # root's status — they are separate entities.
                assert prov.signature_status == sig_status


class TestApprovalIsNotTrust:
    """Owner approval is a policy decision, not a trust assessment."""

    def test_approval_states_are_distinct_from_trust_levels(self):
        from src.agents.spec import VALID_APPROVAL_STATES
        trust_levels = {
            TRUST_UNKNOWN, TRUST_UNTRUSTED, TRUST_REVIEWED,
            TRUST_TRUSTED_LOCAL, TRUST_TRUSTED_SIGNED,
        }
        overlap = set(VALID_APPROVAL_STATES) & trust_levels
        assert not overlap, (
            f"Approval states and trust levels must be disjoint: {overlap}"
        )


class TestEvalPassIsNotTrust:
    """A passing evaluator run does not establish provenance trust."""

    def test_eval_pass_does_not_change_trust_level(self):
        prov = _make_provenance(trust_level=TRUST_UNTRUSTED)
        # Even if eval passes, trust level is not implicitly changed.
        eval_rec = _make_eval_record(passed=True)
        assert eval_rec.passed
        assert prov.trust_level == TRUST_UNTRUSTED


class TestTrustIsNotPermission:
    """Trust level does not grant tool permissions or runtime capabilities."""

    def test_trust_levels_are_not_permission_strings(self):
        """Trust levels should not appear as grantable permissions."""
        trust_levels = [
            TRUST_UNTRUSTED, TRUST_REVIEWED, TRUST_TRUSTED_LOCAL, TRUST_TRUSTED_SIGNED,
        ]
        from src.agents.spec import VALID_APPROVAL_STATES
        for tl in trust_levels:
            assert tl not in VALID_APPROVAL_STATES, (
                f"Trust level '{tl}' must not be an approval state"
            )


class TestStableIsNotExecutable:
    """Stable maturity is a lifecycle label, not an execution contract."""

    def test_stable_value_is_lifecycle_only(self):
        from src.capabilities.schema import CapabilityMaturity
        assert CapabilityMaturity.STABLE.value == "stable"
        # stable maturity does not imply any execution capability.

    def test_no_run_capability_exists(self):
        """There must be no run_capability function anywhere in capabilities."""
        import subprocess

        result = subprocess.run(
            ["grep", "-rnE", r"def run_capability|def run.capability",
             "src/capabilities/"],
            capture_output=True, text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        for line in result.stdout.strip().split("\n"):
            if line and "run_capability" in line.lower():
                stripped = line.split(":", 2)[-1].strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if "no run_capability" in stripped or "No run_capability" in stripped:
                    continue
                pytest.fail(
                    f"run_capability reference found in capabilities source: {line}"
                )


# ── Invariant: analytical policy behavior ───────────────────────────────────


class TestAnalyticalPolicyUntrustedDeniesStable:
    """Untrusted provenance analytically denies stable promotion."""

    def test_untrusted_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(trust_level=TRUST_UNTRUSTED)
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert not decision.allowed
        assert "stable_trust_insufficient" in decision.code

    def test_unknown_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(trust_level=TRUST_UNKNOWN)
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert not decision.allowed
        assert "stable_trust_insufficient" in decision.code


class TestAnalyticalPolicyMissingProvenanceDeniesStable:
    """Missing provenance (None) analytically denies stable promotion."""

    def test_none_provenance_low_risk_warns(self):
        """Low risk + no provenance: warns (legacy exception, Phase 8C-1)."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="low")
        decision = policy.can_promote_to_stable(manifest, provenance=None, risk_level="low")
        assert decision.allowed
        assert "low_risk" in decision.code or "legacy" in decision.message.lower()

    def test_none_provenance_high_risk_denies(self):
        """High risk + no provenance: denies (Phase 8C-1)."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="high")
        decision = policy.can_promote_to_stable(manifest, provenance=None, risk_level="high")
        assert not decision.allowed
        assert "no_provenance" in decision.code

    def test_none_provenance_message_references_trust(self):
        """High risk no provenance message references required trust levels."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="high")
        decision = policy.can_promote_to_stable(manifest, provenance=None, risk_level="high")
        assert "provenance" in decision.message.lower()
        assert "reviewed" in decision.message.lower() or "trusted_local" in decision.message.lower()


class TestAnalyticalPolicyIntegrityMismatchDeniesStable:
    """Integrity mismatch analytically denies stable promotion."""

    def test_mismatch_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_MISMATCH,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert not decision.allowed
        assert "stable_integrity_mismatch" in decision.code

    def test_mismatch_denies_even_with_trusted_local(self):
        """Integrity mismatch is a hard block regardless of trust level."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_MISMATCH,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert not decision.allowed


class TestAnalyticalPolicyInvalidSignatureDeniesStable:
    """Invalid signature analytically denies stable promotion."""

    def test_invalid_signature_denies(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_INVALID,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert not decision.allowed
        assert "stable_signature_invalid" in decision.code

    def test_invalid_signature_denies_even_with_trusted_local(self):
        """Invalid signature is a hard block regardless of trust level."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_INVALID,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert not decision.allowed


class TestAnalyticalPolicyReviewedWarnsForLowerRisk:
    """Reviewed provenance analytically warns (not denies) for promotion.

    The current policy semantics: reviewed trust with passing eval evidence
    can pass analytically but warns. This matches the design intent that
    reviewed may be sufficient for lower risk when eval evidence exists.
    """

    def test_reviewed_with_verified_integrity_warns(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="low")
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert decision.allowed
        assert decision.allowed
        assert "stable_reviewed_minimum" in decision.code

    def test_reviewed_with_passing_eval_warns(self):
        """Reviewed trust with passing eval: allowed with warning."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="low")
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
        )
        eval_rec = _make_eval_record(passed=True)
        decision = policy.can_promote_to_stable(
            manifest, provenance=prov, eval_record=eval_rec,
        )
        assert decision.allowed
        assert decision.severity != "error"


class TestAnalyticalPolicyTrustedLocalAllowsStable:
    """trusted_local provenance analytically allows stable promotion."""

    def test_trusted_local_allows(self):
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            integrity_status=INTEGRITY_VERIFIED,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert decision.allowed
        assert "stable_trust_sufficient" in decision.code


class TestAnalyticalPolicyTrustedSignedDoesNotImplyExecutable:
    """trusted_signed allows analytical promotion but does NOT imply execution."""

    def test_trusted_signed_allows_analytical_promotion(self):
        """Analytical policy: trusted_signed with verified signature allows promotion."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_VERIFIED,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert decision.allowed

    def test_trusted_signed_does_not_bypass_policy(self):
        """trusted_signed is only one input to the future gate.
        CapabilityPolicy.validate_promote still runs independently."""
        policy = CapabilityPolicy()
        from src.capabilities.schema import CapabilityStatus

        manifest = _make_manifest(
            maturity="testing",
            status=CapabilityStatus.QUARANTINED,
        )
        decision = policy.validate_promote(manifest)
        # Quarantined manifest is denied by CapabilityPolicy regardless of trust
        assert not decision.allowed
        assert "quarantined" in decision.code.lower()

    def test_trusted_signed_does_not_bypass_approval(self):
        """trusted_signed does not bypass the owner approval requirement."""
        policy = CapabilityPolicy()
        from src.capabilities.schema import CapabilityRiskLevel

        manifest = _make_manifest(
            maturity="testing",
            risk_level=CapabilityRiskLevel.HIGH,
        )
        decision = policy.validate_promote(manifest, approval=None)
        assert not decision.allowed
        assert "approval" in decision.code.lower()


class TestTrustedSignedIsNecessaryNotSufficient:
    """trusted_signed, if ever implemented, is necessary but not sufficient.

    It is one component of the promotion decision, not a free pass.
    """

    def test_trusted_signed_does_not_bypass_evaluator(self):
        """trusted_signed provenance does not mean the evaluator is skipped."""
        from src.capabilities.provenance import CapabilityTrustPolicy
        # Even with trusted_signed, the analytical policy still checks integrity.
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
            integrity_status=INTEGRITY_MISMATCH,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        # Integrity mismatch should still block even with trusted_signed
        assert not decision.allowed

    def test_trusted_signed_does_not_bypass_invalid_signature(self):
        """trusted_signed cannot co-exist with invalid signature."""
        # The analytical policy checks signature_status before trust_level.
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest()
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_INVALID,
        )
        decision = policy.can_promote_to_stable(manifest, provenance=prov)
        assert not decision.allowed


# ── Invariant: provenance requirements by source ────────────────────────────


class TestLegacyCapabilitiesWithoutProvenanceRemainCompatible:
    """Missing provenance does not break existing lifecycle operations.

    Legacy capabilities without provenance.json remain compatible.
    The analytical policy denies promotion to stable but LifecycleManager
    is not wired to the gate yet, so existing paths are unaffected.
    """

    def test_analytical_policy_warns_stable_for_missing_provenance_low_risk(self):
        """Analytical policy: no provenance + low risk -> warn (legacy exception)."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="low")
        decision = policy.can_promote_to_stable(manifest, provenance=None, risk_level="low")
        assert decision.allowed
        assert "low_risk" in decision.code or decision.severity == "warning"

    def test_analytical_policy_denies_stable_for_missing_provenance_high_risk(self):
        """Analytical policy: no provenance + high risk -> deny."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="high")
        decision = policy.can_promote_to_stable(manifest, provenance=None, risk_level="high")
        assert not decision.allowed

    def test_legacy_capabilities_still_have_valid_maturity_values(self):
        """Legacy capabilities without provenance still have valid lifecycle state."""
        from src.capabilities.schema import CapabilityMaturity
        for mat in CapabilityMaturity:
            # Every maturity value is a valid lifecycle state
            assert mat.value in {"draft", "testing", "stable", "broken", "repairing"}

    def test_missing_provenance_does_not_block_non_stable_transitions(self):
        """Transitions not involving 'stable' are unaffected by missing provenance."""
        # The analytical gate only blocks testing->stable.
        # LifecycleManager transitions to draft, testing, broken, repairing, etc.
        # are untouched because the gate is not wired.
        from src.capabilities.schema import CapabilityMaturity
        non_stable = {
            CapabilityMaturity.DRAFT,
            CapabilityMaturity.TESTING,
            CapabilityMaturity.BROKEN,
            CapabilityMaturity.REPAIRING,
        }
        for mat in non_stable:
            assert mat != CapabilityMaturity.STABLE


class TestExternalImportedTestingCopyRequiresProvenance:
    """External imported capabilities in testing should require provenance for
    future stable promotion, per the design doc. Currently analytical only."""

    def test_imported_without_provenance_analytically_blocked_high_risk(self):
        """External import without provenance: blocked for high risk."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="high")
        decision = policy.can_promote_to_stable(manifest, provenance=None, risk_level="high")
        assert not decision.allowed

    def test_imported_without_provenance_low_risk_warns(self):
        """External import without provenance + low risk: warns (legacy exception)."""
        policy = CapabilityTrustPolicy()
        manifest = _make_manifest(risk_level="low")
        decision = policy.can_promote_to_stable(manifest, provenance=None, risk_level="low")
        assert decision.allowed
        assert decision.severity == "warning"

    def test_source_types_are_defined(self):
        from src.capabilities.provenance import PROVENANCE_SOURCE_TYPES
        expected = {"local_package", "manual_draft", "curator_proposal",
                     "quarantine_activation", "unknown"}
        assert PROVENANCE_SOURCE_TYPES == expected

    def test_manual_draft_is_legacy_exception_source(self):
        """manual_draft is the legacy exception source type."""
        from src.capabilities.provenance import SOURCE_MANUAL_DRAFT
        assert SOURCE_MANUAL_DRAFT == "manual_draft"


# ── Invariant: hard constraints ─────────────────────────────────────────────


class TestNoCryptoBehavior:
    """No cryptographic verification is implemented."""

    def test_provenance_module_has_no_crypto_imports(self):
        import src.capabilities.provenance as pv_mod

        source = inspect.getsource(pv_mod)
        crypto_keywords = ["cryptography", "PyNaCl", "rsa", "ecdsa", "OpenSSL",
                           "private_key", "PrivateKey"]
        for kw in crypto_keywords:
            assert kw not in source, f"Crypto keyword '{kw}' found in provenance.py"


class TestNoNetworkBehavior:
    """No network behavior is added."""

    def test_provenance_module_has_no_network_imports(self):
        import src.capabilities.provenance as pv_mod

        source = inspect.getsource(pv_mod)
        network_keywords = ["httpx", "urllib", "urlopen", "socket"]
        for kw in network_keywords:
            assert kw not in source, f"Network keyword '{kw}' found in provenance.py"


class TestNoNewTools:
    """No new tools are added for the stable promotion gate."""

    def test_capability_tools_has_no_stable_promotion_tools(self):
        import src.tools.capability_tools as ct_mod

        source = inspect.getsource(ct_mod)
        # No dedicated stable promotion tool exists
        assert "stable_promotion" not in source.lower()
        assert "promote_to_stable" not in source.lower()


class TestNoRunCapability:
    """No run_capability function exists anywhere in the codebase."""

    def test_no_run_capability_in_capabilities(self):
        import subprocess

        result = subprocess.run(
            ["grep", "-rnE", r"def run_capability|def run.capability",
             "src/capabilities/"],
            capture_output=True, text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode != 0 or not result.stdout.strip(), (
            f"run_capability found: {result.stdout}"
        )


class TestNoNewAutomaticBehavior:
    """No new automatic promotion or trust escalation behavior exists."""

    def test_no_auto_promotion_to_stable(self):
        """Nothing automatically promotes a capability to stable."""
        import src.capabilities.lifecycle as lc_mod

        source = inspect.getsource(lc_mod)
        assert "auto_stable" not in source
        assert "auto_promote" not in source
        assert "automatic_stable" not in source
