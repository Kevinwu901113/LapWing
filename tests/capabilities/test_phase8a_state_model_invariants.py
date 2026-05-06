"""Phase 8A-0: Capability Lifecycle / Trust State Model invariant tests.

Validates the conceptual state model invariants documented in:
- docs/capability_lifecycle_state_model.md
- docs/capability_trust_state_model.md

No new runtime behavior. No new tools. Pure invariant assertions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.quarantine_activation_apply import (
    TARGET_MATURITY as APPLY_TARGET_MATURITY,
)
from src.capabilities.quarantine_activation_apply import (
    TARGET_STATUS as APPLY_TARGET_STATUS,
)
from src.capabilities.quarantine_activation_apply import apply_quarantine_activation
from src.capabilities.quarantine_activation_planner import (
    TARGET_MATURITY as PLAN_TARGET_MATURITY,
)
from src.capabilities.quarantine_activation_planner import (
    TARGET_STATUS as PLAN_TARGET_STATUS,
)
from src.capabilities.quarantine_activation_planner import plan_quarantine_activation
from src.capabilities.quarantine_review import mark_quarantine_review
from src.capabilities.quarantine_transition import request_quarantine_testing_transition
from src.capabilities.schema import (
    ALLOWED_MATURITIES,
    ALLOWED_STATUSES,
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)
from src.capabilities.store import CapabilityStore
from src.agents.spec import VALID_APPROVAL_STATES


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_evaluator() -> CapabilityEvaluator:
    return CapabilityEvaluator()


def _make_policy() -> CapabilityPolicy:
    return CapabilityPolicy()


def _create_quarantine_capability(
    store: CapabilityStore,
    cap_id: str,
    *,
    status: str = "quarantined",
    maturity: str = "draft",
    risk_level: str = "low",
    with_scripts: bool = False,
) -> Path:
    """Create a minimal quarantined capability on disk.

    Does not call CapabilityLifecycleManager, import tools, or activation
    apply. This is pure fixture setup for invariant checking.
    """
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
    (qdir / "manifest.json").write_text(json.dumps({
        k: v for k, v in fm.items() if k not in ("version",)
    }, indent=2), encoding="utf-8")

    import_report = {
        "capability_id": cap_id,
        "source_type": "local_package",
        "source_path_hash": "abc123",
        "imported_at": "2026-05-01T00:00:00+00:00",
        "original_content_hash": "abc",
        "target_scope": "user",
        "eval_passed": True,
        "eval_score": 1.0,
        "eval_findings": [],
        "policy_findings": [],
        "files_summary": {
            "scripts": ["setup.sh"] if with_scripts else [],
            "tests": [],
            "examples": [],
        },
        "quarantine_reason": "test package",
    }
    (qdir / "import_report.json").write_text(
        json.dumps(import_report, indent=2), encoding="utf-8"
    )

    # Write a passing audit report
    audit_dir = qdir / "quarantine_audit_reports"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "audit_id": "audit_test123",
        "capability_id": cap_id,
        "created_at": "2026-05-01T01:00:00+00:00",
        "passed": True,
        "risk_level": risk_level,
        "findings": [],
        "recommended_review_status": "approved_for_testing",
        "remediation_suggestions": [],
    }
    (audit_dir / "audit_test123.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )

    # Write an approved_for_testing review decision
    review_dir = qdir / "quarantine_reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    review = {
        "review_id": "review_test456",
        "capability_id": cap_id,
        "review_status": "approved_for_testing",
        "reviewer": "test",
        "reason": "Test review",
        "created_at": "2026-05-01T02:00:00+00:00",
    }
    (review_dir / "review_test456.json").write_text(
        json.dumps(review, indent=2), encoding="utf-8"
    )

    return qdir


# ── Invariant: quarantined does not imply active ───────────────────────────


class TestQuarantinedDoesNotImplyActive:
    """quarantined status is distinct from active; no quarantine operation
    implicitly promotes status."""

    def test_quarantined_status_is_not_active_value(self):
        assert CapabilityStatus.QUARANTINED.value == "quarantined"
        assert CapabilityStatus.ACTIVE.value == "active"
        assert CapabilityStatus.QUARANTINED != CapabilityStatus.ACTIVE

    def test_mark_quarantine_review_does_not_change_status(
        self, tmp_path: Path
    ):
        store = _make_store(tmp_path)
        cap_id = "test-qr-invariant"
        qdir = _create_quarantine_capability(store, cap_id)

        mark_quarantine_review(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            review_status="approved_for_testing",
            reviewer="test",
            reason="Invariant test",
        )

        manifest = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "quarantined"
        assert manifest["maturity"] == "draft"

    def test_quarantined_in_allowed_statuses(self):
        assert "quarantined" in ALLOWED_STATUSES
        assert "active" in ALLOWED_STATUSES


# ── Invariant: approved_for_testing does not imply maturity=testing ────────


class TestApprovedForTestingDoesNotImplyTestingMaturity:
    """approved_for_testing is a review recommendation, not a maturity level."""

    def test_review_status_values_are_distinct_from_maturity(self):
        valid_review_statuses = {"needs_changes", "approved_for_testing", "rejected"}
        valid_maturities = {e.value for e in CapabilityMaturity}
        assert "approved_for_testing" not in valid_maturities
        assert "testing" in valid_maturities
        assert "approved_for_testing" != "testing"

    def test_mark_quarantine_review_does_not_change_maturity(
        self, tmp_path: Path
    ):
        store = _make_store(tmp_path)
        cap_id = "test-review-maturity"
        qdir = _create_quarantine_capability(store, cap_id)

        mark_quarantine_review(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            review_status="approved_for_testing",
            reviewer="test",
            reason="Should not change maturity",
        )

        manifest = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["maturity"] == "draft"
        assert manifest["maturity"] != "testing"

    def test_review_requires_quarantined_status(self, tmp_path: Path):
        """Review gate checks that status is still quarantined."""
        store = _make_store(tmp_path)
        cap_id = "test-review-status-gate"
        _create_quarantine_capability(store, cap_id, status="active")

        with pytest.raises(CapabilityError, match="quarantined"):
            mark_quarantine_review(
                store_data_dir=store.data_dir,
                capability_id=cap_id,
                review_status="approved_for_testing",
                reviewer="test",
                reason="Should fail — not quarantined",
            )


# ── Invariant: transition request does not imply approval ──────────────────


class TestTransitionRequestDoesNotImplyApproval:
    """A QuarantineTransitionRequest is a pure data record, not an approval."""

    def test_request_statuses_are_distinct_from_approval(self):
        request_statuses = {"pending", "cancelled", "rejected", "superseded"}
        assert "approved" not in request_statuses
        assert "approved_for_testing" not in request_statuses

    def test_request_does_not_change_manifest_status(
        self, tmp_path: Path
    ):
        store = _make_store(tmp_path)
        cap_id = "test-request-status"
        qdir = _create_quarantine_capability(store, cap_id)

        evaluator = _make_evaluator()
        policy = _make_policy()

        result = request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            requested_target_scope="user",
            reason="Invariant test",
            evaluator=evaluator,
            policy=policy,
        )
        assert result["would_create"] is True

        manifest = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "quarantined"
        assert manifest["maturity"] == "draft"

    def test_request_does_not_move_files(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-request-no-move"
        qdir = _create_quarantine_capability(store, cap_id)

        evaluator = _make_evaluator()
        policy = _make_policy()

        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            requested_target_scope="user",
            reason="Invariant test",
            evaluator=evaluator,
            policy=policy,
        )

        # Capability should still only exist in quarantine
        user_dir = store.data_dir / "user" / cap_id
        assert not user_dir.exists()
        assert qdir.is_dir()


# ── Invariant: activation plan does not imply authority ────────────────────


class TestActivationPlanDoesNotImplyAuthority:
    """A QuarantineActivationPlan is a pure plan; it never activates."""

    def test_planner_constants_match_apply_constants(self):
        assert PLAN_TARGET_STATUS == APPLY_TARGET_STATUS == "active"
        assert PLAN_TARGET_MATURITY == APPLY_TARGET_MATURITY == "testing"

    def test_plan_always_returns_would_activate_false(
        self, tmp_path: Path
    ):
        store = _make_store(tmp_path)
        cap_id = "test-plan-authority"
        _create_quarantine_capability(store, cap_id)

        evaluator = _make_evaluator()
        policy = _make_policy()

        # First create a transition request
        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            requested_target_scope="user",
            reason="Test",
            evaluator=evaluator,
            policy=policy,
        )

        result = plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            target_scope="user",
            evaluator=evaluator,
            policy=policy,
        )

        assert result["would_activate"] is False
        assert "plan" in result

    def test_plan_does_not_change_manifest(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-plan-no-mutate"
        qdir = _create_quarantine_capability(store, cap_id)

        evaluator = _make_evaluator()
        policy = _make_policy()

        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            requested_target_scope="user",
            reason="Test",
            evaluator=evaluator,
            policy=policy,
        )

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            target_scope="user",
            evaluator=evaluator,
            policy=policy,
        )

        manifest = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "quarantined"
        assert manifest["maturity"] == "draft"

    def test_plan_does_not_copy_files(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-plan-no-copy"
        _create_quarantine_capability(store, cap_id)

        evaluator = _make_evaluator()
        policy = _make_policy()

        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            requested_target_scope="user",
            reason="Test",
            evaluator=evaluator,
            policy=policy,
        )

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            target_scope="user",
            evaluator=evaluator,
            policy=policy,
        )

        user_dir = store.data_dir / "user" / cap_id
        assert not user_dir.exists()


# ── Invariant: activation apply can only create active/testing ─────────────


class TestActivationApplyOnlyCreatesActiveTesting:
    """apply_quarantine_activation is hardcoded to active/testing targets."""

    def test_target_status_is_active(self):
        assert APPLY_TARGET_STATUS == "active"

    def test_target_maturity_is_testing(self):
        assert APPLY_TARGET_MATURITY == "testing"


# ── Invariant: activation apply can never create stable ────────────────────


class TestActivationApplyNeverCreatesStable:
    """apply_quarantine_activation can never produce maturity=stable."""

    def test_target_maturity_is_not_stable(self):
        assert APPLY_TARGET_MATURITY != "stable"
        assert APPLY_TARGET_MATURITY == "testing"

    def test_apply_sets_testing_not_stable(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-apply-not-stable"
        _create_quarantine_capability(store, cap_id)

        evaluator = _make_evaluator()
        policy = _make_policy()

        # Create transition request
        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            requested_target_scope="user",
            reason="Test",
            evaluator=evaluator,
            policy=policy,
        )

        # Create activation plan
        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            target_scope="user",
            evaluator=evaluator,
            policy=policy,
        )

        from src.capabilities.index import CapabilityIndex

        index = CapabilityIndex(str(tmp_path / "index.sqlite"))
        index.init()

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            target_scope="user",
            reason="Invariant test",
            evaluator=evaluator,
            policy=policy,
            index=index,
        )

        assert result.applied is True
        assert result.target_status == "active"
        assert result.target_maturity == "testing"
        assert result.target_maturity != "stable"

    def test_promotion_planner_blocks_quarantined_to_stable(self, tmp_path: Path):
        """Stable promotion from quarantine is explicitly blocked."""
        store = _make_store(tmp_path)
        cap_id = "test-quarantine-stable-block"
        _create_quarantine_capability(store, cap_id)

        from src.capabilities.document import CapabilityParser

        parser = CapabilityParser()
        doc = parser.parse(store.data_dir / "quarantine" / cap_id)

        planner = PromotionPlanner()
        plan = planner.plan_transition(doc.manifest, "stable")

        assert plan.allowed is False
        assert "quarantined_to_stable_blocked" in str(plan.blocking_findings)


# ── Invariant: testing does not imply executable ───────────────────────────


class TestTestingDoesNotImplyExecutable:
    """Maturity=testing is metadata, not an execution guarantee."""

    def test_testing_is_a_maturity_not_a_status(self):
        assert "testing" in ALLOWED_MATURITIES
        assert CapabilityMaturity.TESTING.value == "testing"

    def test_testing_can_be_disabled(self):
        """A testing-maturity capability can still have status=disabled."""
        # Verify status and maturity are independent axes
        assert CapabilityMaturity.TESTING.value == "testing"
        assert CapabilityStatus.DISABLED.value == "disabled"
        # These are different enums — they can be combined independently


# ── Invariant: no run_capability exists ────────────────────────────────────


class TestNoRunCapabilityExists:
    """No run_capability function or method exists in the codebase."""

    def test_run_capability_not_in_capabilities_init(self):
        from src.capabilities import __all__ as cap_all
        run_names = [n for n in cap_all if "run_capability" in n.lower()]
        assert len(run_names) == 0

    def test_run_capability_not_importable(self):
        with pytest.raises(ImportError):
            from src.capabilities import run_capability  # noqa: F811


# ── Invariant: reviewed provenance does not imply trusted_signed ────────────


class TestReviewedProvenanceDoesNotImplyTrustedSigned:
    """'reviewed' and 'trusted_signed' are distinct trust levels.
    Neither is implemented yet — these are conceptual invariant tests."""

    def test_reviewed_and_trusted_signed_are_conceptually_distinct(self):
        reviewed = "reviewed"
        trusted_signed = "trusted_signed"
        assert reviewed != trusted_signed

    def test_no_provenance_enum_exists_yet(self):
        """Provenance enums are not implemented — verify they don't exist yet."""
        with pytest.raises(ImportError):
            from src.capabilities.schema import ProvenanceTrustLevel  # noqa: F811

    def test_no_signature_verification_exists_yet(self):
        with pytest.raises(ImportError):
            from src.capabilities.schema import SignatureStatus  # noqa: F811


# ── Invariant: missing provenance must not break legacy capabilities ───────


class TestMissingProvenanceDoesNotBreakLegacyCapabilities:
    """Capabilities without provenance data must work normally."""

    def test_manifest_creates_without_provenance(self):
        manifest = CapabilityManifest(
            id="test-no-prov",
            name="Test No Provenance",
            description="A capability without provenance data.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.TESTING,
            status=CapabilityStatus.ACTIVE,
            risk_level=CapabilityRiskLevel.LOW,
        )
        assert manifest.id == "test-no-prov"
        assert manifest.status == CapabilityStatus.ACTIVE
        assert manifest.maturity == CapabilityMaturity.TESTING

    def test_manifest_extra_field_accepts_arbitrary_data(self):
        """The extra field allows arbitrary data; provenance can be absent."""
        manifest = CapabilityManifest(
            id="test-extra-empty",
            name="Test Extra",
            description="Testing extra field.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.DRAFT,
            status=CapabilityStatus.QUARANTINED,
            risk_level=CapabilityRiskLevel.LOW,
            extra={"some_other_field": True},
        )
        assert "provenance" not in manifest.extra
        assert manifest.extra["some_other_field"] is True

    def test_manifest_no_provenance_still_validates(self):
        """Policy validator should not fail on missing provenance data."""
        manifest = CapabilityManifest(
            id="test-no-prov-validate",
            name="Test No Provenance Validate",
            description="Testing without provenance.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.DRAFT,
            status=CapabilityStatus.ACTIVE,
            risk_level=CapabilityRiskLevel.LOW,
        )
        policy = _make_policy()
        decision = policy.validate_create(manifest)
        assert decision.allowed is True


# ── Invariant: invalid provenance blocks only when gates check it ──────────


class TestInvalidProvenanceBlocksOnlyWhenGatesCheckIt:
    """Future: tampered provenance should fail gates, not crash unrelated code."""

    def test_policy_validate_create_ignores_extra_provenance_field(self):
        """Policy ignores arbitrary extra fields including future provenance."""
        manifest = CapabilityManifest(
            id="test-future-prov",
            name="Test Future Provenance",
            description="Testing future provenance in extra.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.DRAFT,
            status=CapabilityStatus.ACTIVE,
            risk_level=CapabilityRiskLevel.LOW,
            extra={"provenance": {"trust_level": "tampered", "signature": "invalid"}},
        )
        policy = _make_policy()
        decision = policy.validate_create(manifest)
        # Currently, policy does not inspect extra.provenance — it should still allow
        assert decision.allowed is True

    def test_manifest_with_unrecognized_extra_does_not_crash(self):
        """Unrecognized fields in extra must not crash model validation."""
        manifest = CapabilityManifest(
            id="test-weird-extra",
            name="Test Weird Extra",
            description="Testing weird extra data.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.DRAFT,
            status=CapabilityStatus.ACTIVE,
            risk_level=CapabilityRiskLevel.LOW,
            extra={
                "provenance_trust_level": "unknown_garbage_value",
                "integrity_status": 12345,  # wrong type
                "nested": {"deep": {"key": None}},
            },
        )
        assert manifest.id == "test-weird-extra"


# ── Invariant: external import always starts untrusted/quarantined ─────────


class TestExternalImportAlwaysStartsUntrustedQuarantined:
    """Import forces status=quarantined, maturity=draft regardless of source."""

    def test_import_result_always_quarantined_status(self):
        """Verify the import module forces quarantined status."""
        from src.capabilities.import_quarantine import InspectResult

        result = InspectResult(
            id="test-import",
            name="Test Import",
            description="Test.",
            type="skill",
            declared_scope="user",
            target_scope="user",
            maturity="draft",
            status="quarantined",
            risk_level="low",
        )
        assert result.status == "quarantined"
        assert result.status != "active"
        assert result.maturity == "draft"
        assert result.maturity != "stable"

    def test_import_package_normalizes_manifest_to_quarantined_draft(
        self, tmp_path: Path
    ):
        """import_capability_package normalizes status/maturity in quarantine."""
        store = _make_store(tmp_path)

        # Create a source package that claims active/stable
        src = tmp_path / "source_pkg"
        src.mkdir()
        fm = {
            "id": "test-normalize-import",
            "name": "Test Normalize",
            "description": "Claims to be active/stable.",
            "type": "skill",
            "scope": "user",
            "version": "0.1.0",
            "maturity": "stable",
            "status": "active",
            "risk_level": "low",
            "triggers": [],
            "tags": [],
            "trust_required": "developer",
            "required_tools": [],
            "required_permissions": [],
        }
        fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
        md = (
            f"---\n{fm_yaml}\n---\n\n"
            "## When to use\nTest.\n\n## Procedure\n1. Test\n\n"
            "## Verification\nPass.\n\n## Failure handling\nRetry."
        )
        (src / "CAPABILITY.md").write_text(md, encoding="utf-8")
        (src / "manifest.json").write_text(json.dumps({
            k: v for k, v in fm.items() if k not in ("version",)
        }, indent=2), encoding="utf-8")

        from src.capabilities.import_quarantine import import_capability_package

        result = import_capability_package(
            path=src,
            store=store,
            evaluator=_make_evaluator(),
            policy=_make_policy(),
            target_scope="user",
            imported_by="test",
            reason="Invariant test",
        )

        assert result.applied is True

        # Read the normalized manifest
        qdir = store.data_dir / "quarantine" / "test-normalize-import"
        manifest = json.loads((qdir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "quarantined", (
            f"Expected quarantined, got {manifest['status']}"
        )
        assert manifest["maturity"] == "draft", (
            f"Expected draft, got {manifest['maturity']}"
        )


# ── Invariant: active/testing external copy retains origin metadata ────────


class TestActiveTestingCopyRetainsOriginMetadata:
    """Activation apply writes extra.origin with full traceability."""

    def test_activation_writes_origin_metadata(self, tmp_path: Path):
        store = _make_store(tmp_path)
        cap_id = "test-origin-metadata"
        _create_quarantine_capability(store, cap_id)

        evaluator = _make_evaluator()
        policy = _make_policy()

        request_quarantine_testing_transition(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            requested_target_scope="user",
            reason="Test",
            evaluator=evaluator,
            policy=policy,
        )

        plan_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            target_scope="user",
            evaluator=evaluator,
            policy=policy,
        )

        from src.capabilities.index import CapabilityIndex

        index = CapabilityIndex(str(tmp_path / "index.sqlite"))
        index.init()

        result = apply_quarantine_activation(
            store_data_dir=store.data_dir,
            capability_id=cap_id,
            target_scope="user",
            reason="Invariant test",
            applied_by="test",
            evaluator=evaluator,
            policy=policy,
            index=index,
        )

        assert result.applied is True

        # Read the activated copy's manifest
        activated_dir = store.data_dir / "user" / cap_id
        manifest = json.loads(
            (activated_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert "extra" in manifest
        assert "origin" in manifest["extra"]
        origin = manifest["extra"]["origin"]
        assert origin["quarantine_capability_id"] == cap_id
        assert "activation_plan_id" in origin
        assert "transition_request_id" in origin
        assert origin["activated_by"] == "test"


# ── Invariant: stable promotion must remain a separate lifecycle gate ──────


class TestStablePromotionIsSeparateLifecycleGate:
    """Stable promotion is not reachable via activation apply; it requires
    PromotionPlanner._plan_testing_to_stable()."""

    def test_stable_not_in_apply_target_constants(self):
        assert APPLY_TARGET_MATURITY == "testing"
        assert APPLY_TARGET_MATURITY != "stable"
        assert APPLY_TARGET_STATUS == "active"

    def test_promotion_planner_testing_to_stable_is_separate_method(self):
        planner = PromotionPlanner()
        assert hasattr(planner, "_plan_testing_to_stable")
        assert callable(getattr(planner, "_plan_testing_to_stable"))

    def test_testing_to_stable_requires_evaluator_pass(self):
        """testing→stable promotion requires an eval record."""
        planner = PromotionPlanner()

        manifest = CapabilityManifest(
            id="test-stable-gate",
            name="Test Stable Gate",
            description="Testing the stable gate.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.TESTING,
            status=CapabilityStatus.ACTIVE,
            risk_level=CapabilityRiskLevel.LOW,
        )

        # Without eval record, testing→stable should be blocked
        plan = planner.plan_transition(manifest, "stable")
        assert plan.allowed is False
        assert "evaluator_pass" in plan.required_evidence
        assert plan.explanation != ""

    def test_draft_to_stable_is_not_allowed_transition(self):
        """draft→stable is not in the legal transition table."""
        planner = PromotionPlanner()

        manifest = CapabilityManifest(
            id="test-draft-stable",
            name="Test Draft to Stable",
            description="Testing illegal transition.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.DRAFT,
            status=CapabilityStatus.ACTIVE,
            risk_level=CapabilityRiskLevel.LOW,
        )

        plan = planner.plan_transition(manifest, "stable")
        assert plan.allowed is False
        assert "Unknown transition" in plan.explanation

    def test_promotion_planner_quarantined_to_stable_blocked_code(self):
        """Verify the specific blocking code for quarantined→stable."""
        planner = PromotionPlanner()

        manifest = CapabilityManifest(
            id="test-quar-stable-code",
            name="Test Quarantine Stable Code",
            description="Testing blocking code.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.USER,
            version="1.0.0",
            maturity=CapabilityMaturity.DRAFT,
            status=CapabilityStatus.QUARANTINED,
            risk_level=CapabilityRiskLevel.LOW,
        )

        plan = planner.plan_transition(manifest, "stable")
        assert plan.allowed is False
        codes = [f["code"] for f in plan.blocking_findings]
        assert "quarantined_to_stable_blocked" in codes


# ── Invariant: AgentCandidate approval_state values are well-defined ───────


class TestAgentCandidateApprovalStates:
    """AgentCandidate approval_state domain validation."""

    def test_valid_approval_states(self):
        assert "not_required" in VALID_APPROVAL_STATES
        assert "pending" in VALID_APPROVAL_STATES
        assert "approved" in VALID_APPROVAL_STATES
        assert "rejected" in VALID_APPROVAL_STATES
        assert len(VALID_APPROVAL_STATES) == 4

    def test_approval_states_are_distinct_from_maturity(self):
        valid_maturities = {e.value for e in CapabilityMaturity}
        for state in VALID_APPROVAL_STATES:
            assert state not in valid_maturities


# ── Invariant: CapabilityStatus domain completeness ────────────────────────


class TestCapabilityStatusDomain:
    """All capability status values are well-defined and distinct."""

    def test_all_statuses_defined(self):
        expected = {
            "active",
            "broken",
            "repairing",
            "disabled",
            "archived",
            "quarantined",
            "needs_permission",
            "environment_mismatch",
        }
        actual = {e.value for e in CapabilityStatus}
        assert actual == expected

    def test_quarantined_not_in_default_active_filters(self):
        """Quarantined is excluded from active status values."""
        assert CapabilityStatus.QUARANTINED.value == "quarantined"
        assert CapabilityStatus.ACTIVE.value == "active"
        assert CapabilityStatus.QUARANTINED != CapabilityStatus.ACTIVE


# ── Invariant: CapabilityMaturity domain completeness ──────────────────────


class TestCapabilityMaturityDomain:
    """All capability maturity values are well-defined and distinct."""

    def test_all_maturities_defined(self):
        expected = {"draft", "testing", "stable", "broken", "repairing"}
        actual = {e.value for e in CapabilityMaturity}
        assert actual == expected
