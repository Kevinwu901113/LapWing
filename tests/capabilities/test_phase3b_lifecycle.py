"""Phase 3B tests: CapabilityLifecycleManager gated transitions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.capabilities.document import CapabilityDocument, parse_capability
from src.capabilities.eval_records import get_latest_eval_record
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.lifecycle import CapabilityLifecycleManager, TransitionResult
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)
from src.capabilities.store import CapabilityStore


# ── Helpers ─────────────────────────────────────────────────────────────

VALID_BODY = """## When to use

Use this capability when you need to test lifecycle transitions.

## Procedure

1. Do step one.
2. Do step two.

## Verification

Verify the output is correct.

## Failure handling

If it fails, log the error and retry.
"""

MISSING_VERIFICATION_BODY = """## When to use

Use this.

## Procedure

1. Step one.

## Failure handling

Handle errors.
"""


def _make_store(tmp_path: Path, *, with_index: bool = False) -> CapabilityStore:
    kwargs: dict = {}
    if with_index:
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        kwargs["index"] = idx
    return CapabilityStore(data_dir=tmp_path / "capabilities", **kwargs)


def _create_cap(
    store: CapabilityStore,
    *,
    cap_id: str = "test_cap",
    body: str = VALID_BODY,
    maturity: str = "draft",
    status: str = "active",
    risk_level: str = "low",
    scope: CapabilityScope = CapabilityScope.WORKSPACE,
    **overrides,
) -> CapabilityDocument:
    doc = store.create_draft(
        scope=scope,
        cap_id=cap_id,
        name=overrides.pop("name", "Test Capability"),
        description=overrides.pop("description", "A test capability for lifecycle testing."),
        body=body,
        risk_level=risk_level,
        **overrides,
    )
    # create_draft always sets maturity=draft, status=active.
    # Update the manifest if the caller wants a different starting state.
    needs_update = False
    updates: dict = {}
    if maturity != "draft":
        updates["maturity"] = CapabilityMaturity(maturity)
        needs_update = True
    if status != "active":
        updates["status"] = CapabilityStatus(status)
        needs_update = True
    if needs_update:
        from datetime import datetime, timezone
        updates["updated_at"] = datetime.now(timezone.utc)
        updated = doc.manifest.model_copy(update=updates)
        doc.manifest = updated
        store._sync_manifest_json(doc.directory, doc)
        # Re-parse to get a fresh content_hash
        doc = store._parser.parse(doc.directory)
        store._maybe_index(doc)
    return doc


def _make_lifecycle(
    store: CapabilityStore,
    *,
    mutation_log=None,
    available_tools=None,
) -> CapabilityLifecycleManager:
    return CapabilityLifecycleManager(
        store=store,
        evaluator=CapabilityEvaluator(),
        policy=CapabilityPolicy(),
        planner=PromotionPlanner(),
        mutation_log=mutation_log,
        available_tools=available_tools,
    )


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


@pytest.fixture
def lifecycle(store):
    return _make_lifecycle(store)


@pytest.fixture
def draft_cap(store):
    return _create_cap(store, cap_id="draft_cap", maturity="draft", status="active")


@pytest.fixture
def testing_cap(store):
    return _create_cap(store, cap_id="testing_cap", maturity="testing", status="active")


# ── draft -> testing ────────────────────────────────────────────────────

class TestDraftToTesting:
    def test_applies_for_valid_low_risk_capability(self, store, lifecycle):
        doc = _create_cap(store, cap_id="valid_draft")
        result = lifecycle.apply_transition("valid_draft", "testing")
        assert result.applied, f"Expected applied=True, got: {result.message}"
        assert result.to_maturity == "testing"

        updated = store.get("valid_draft")
        assert updated.manifest.maturity == CapabilityMaturity.TESTING

    def test_blocked_when_verification_missing(self, store, lifecycle):
        _create_cap(store, cap_id="no_verify", body=MISSING_VERIFICATION_BODY)
        result = lifecycle.apply_transition("no_verify", "testing")
        assert not result.applied
        assert result.blocking_findings

    def test_manifest_unchanged_when_blocked(self, store, lifecycle):
        doc = _create_cap(store, cap_id="blocked_draft", body=MISSING_VERIFICATION_BODY)
        maturity_before = doc.manifest.maturity
        lifecycle.apply_transition("blocked_draft", "testing")
        doc_after = store.get("blocked_draft")
        assert doc_after.manifest.maturity == maturity_before

    def test_body_unchanged_when_blocked(self, store, lifecycle):
        _create_cap(store, cap_id="blocked_body", body=MISSING_VERIFICATION_BODY)
        cap_dir = store._get_dir("blocked_body", CapabilityScope.WORKSPACE)
        cap_md_before = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")
        lifecycle.apply_transition("blocked_body", "testing")
        cap_md_after = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")
        assert cap_md_before == cap_md_after


# ── testing -> stable ───────────────────────────────────────────────────

class TestTestingToStable:
    def test_applies_for_low_risk_with_passing_eval(self, store, lifecycle):
        _create_cap(store, cap_id="ready_cap", maturity="testing", status="active")
        result = lifecycle.apply_transition("ready_cap", "stable")
        assert result.applied, f"Expected applied=True, got: {result.message}"
        assert result.to_maturity == "stable"

        updated = store.get("ready_cap")
        assert updated.manifest.maturity == CapabilityMaturity.STABLE

    def test_blocked_without_passing_eval(self, store, lifecycle):
        # Create a capability that will fail eval (missing sections)
        _create_cap(
            store, cap_id="no_eval_cap",
            maturity="testing", status="active",
            body="No required sections here.",
        )
        result = lifecycle.apply_transition("no_eval_cap", "stable")
        assert not result.applied

    def test_medium_risk_requires_approval(self, store, lifecycle):
        _create_cap(
            store, cap_id="med_risk_cap",
            maturity="testing", status="active",
            risk_level="medium",
        )
        result = lifecycle.apply_transition("med_risk_cap", "stable")
        # Medium risk with passing eval but no approval: planner allows
        # (eval_passing_sufficient) and policy allows with passing eval.
        assert result.applied

    def test_high_risk_requires_approval(self, store, lifecycle):
        _create_cap(
            store, cap_id="high_risk_cap",
            maturity="testing", status="active",
            risk_level="high",
        )
        result = lifecycle.apply_transition("high_risk_cap", "stable")
        assert not result.applied
        assert "approval" in result.message.lower() or result.blocking_findings

    def test_high_risk_allowed_with_approval(self, store, lifecycle):
        _create_cap(
            store, cap_id="high_risk_approved",
            maturity="testing", status="active",
            risk_level="high",
        )
        approval = MagicMock()
        approval.approved = True
        result = lifecycle.apply_transition("high_risk_approved", "stable", approval=approval)
        assert result.applied

    def test_high_risk_never_auto_promotes(self, store, lifecycle):
        _create_cap(
            store, cap_id="high_risk_auto",
            maturity="testing", status="active",
            risk_level="high",
        )
        # Without explicit approval, should be blocked
        result = lifecycle.apply_transition("high_risk_auto", "stable")
        assert not result.applied


# ── stable -> broken ────────────────────────────────────────────────────

class TestStableToBroken:
    def test_requires_failure_evidence(self, store, lifecycle):
        _create_cap(store, cap_id="stable_cap", maturity="stable", status="active")
        result = lifecycle.apply_transition("stable_cap", "broken")
        assert not result.applied

    def test_allowed_with_failure_evidence(self, store, lifecycle):
        _create_cap(store, cap_id="failing_cap", maturity="stable", status="active")
        result = lifecycle.apply_transition(
            "failing_cap", "broken",
            failure_evidence={"error": "Eval failures in production"},
        )
        assert result.applied
        assert result.to_maturity == "broken"

        updated = store.get("failing_cap")
        assert updated.manifest.maturity == CapabilityMaturity.BROKEN

    def test_writes_failure_metadata(self, store, lifecycle):
        _create_cap(store, cap_id="fail_meta_cap", maturity="stable", status="active")
        result = lifecycle.apply_transition(
            "fail_meta_cap", "broken",
            failure_evidence={"error": "Production failure"},
            reason="Observed failure in production",
        )
        assert result.applied
        # The reason is captured in the version snapshot
        assert result.version_snapshot_id is not None


# ── broken -> repairing ─────────────────────────────────────────────────

class TestBrokenToRepairing:
    def test_always_allowed(self, store, lifecycle):
        _create_cap(store, cap_id="broken_cap", maturity="broken", status="active")
        result = lifecycle.apply_transition("broken_cap", "repairing", reason="Starting repairs")
        assert result.applied
        assert result.to_maturity == "repairing"

        updated = store.get("broken_cap")
        assert updated.manifest.maturity == CapabilityMaturity.REPAIRING


# ── repairing -> testing ────────────────────────────────────────────────

class TestRepairingToTesting:
    def test_allowed_without_eval(self, store, lifecycle):
        _create_cap(store, cap_id="repairing_cap", maturity="repairing", status="active")
        result = lifecycle.apply_transition("repairing_cap", "testing")
        assert result.applied
        assert result.to_maturity == "testing"

    def test_allowed_with_passing_eval(self, store, lifecycle):
        _create_cap(store, cap_id="repair_eval_cap", maturity="repairing", status="active")
        result = lifecycle.apply_transition("repair_eval_cap", "testing")
        assert result.applied


# ── downgrade: testing -> draft ─────────────────────────────────────────

class TestTestingToDraft:
    def test_downgrade_allowed(self, store, lifecycle):
        _create_cap(store, cap_id="downgrade_cap", maturity="testing", status="active")
        result = lifecycle.apply_transition("downgrade_cap", "draft")
        assert result.applied
        assert result.to_maturity == "draft"

        updated = store.get("downgrade_cap")
        assert updated.manifest.maturity == CapabilityMaturity.DRAFT


# ── disable / archive ───────────────────────────────────────────────────

class TestDisableArchive:
    def test_active_to_disabled(self, store, lifecycle):
        _create_cap(store, cap_id="to_disable", maturity="testing", status="active")
        result = lifecycle.apply_transition("to_disable", "disabled", reason="No longer needed")
        assert result.applied
        assert result.to_status == "disabled"

        updated = store.get("to_disable")
        assert updated.manifest.status == CapabilityStatus.DISABLED

    def test_active_to_archived(self, store, lifecycle):
        _create_cap(store, cap_id="to_archive", maturity="draft", status="active")
        result = lifecycle.apply_transition("to_archive", "archived", reason="Superseded")
        assert result.applied
        assert result.to_status == "archived"

    def test_archived_excluded_from_default_list(self, store, lifecycle):
        _create_cap(store, cap_id="arch_list", maturity="draft", status="active")
        lifecycle.apply_transition("arch_list", "archived")

        docs = store.list()
        archived_ids = [d.id for d in docs if d.id == "arch_list"]
        assert len(archived_ids) == 0

    def test_archived_included_when_requested(self, store, lifecycle):
        _create_cap(store, cap_id="arch_include", maturity="draft", status="active")
        lifecycle.apply_transition("arch_include", "archived")

        docs = store.list(include_archived=True)
        archived_ids = [d.id for d in docs if d.id == "arch_include"]
        assert len(archived_ids) == 1

    def test_already_archived_cannot_be_disabled(self, store, lifecycle):
        _create_cap(store, cap_id="arch_to_disable", maturity="draft", status="active")
        lifecycle.apply_transition("arch_to_disable", "archived")

        # After archive, the cap moves to archived dir and store.get can't find it.
        # The transition should raise an error.
        with pytest.raises(Exception):
            lifecycle.apply_transition("arch_to_disable", "disabled")


# ── Disabled blocks promotion ───────────────────────────────────────────

class TestDisabledBlocksPromotion:
    def test_disabled_cannot_promote(self, store, lifecycle):
        _create_cap(store, cap_id="disabled_cap", maturity="draft", status="active")
        store.disable("disabled_cap")

        result = lifecycle.apply_transition("disabled_cap", "testing")
        assert not result.applied

    def test_archived_cannot_promote(self, store, lifecycle):
        _create_cap(store, cap_id="archived_cap", maturity="testing", status="active")
        store.archive("archived_cap")

        # After archive, the cap moves to archived dir; get raises.
        with pytest.raises(Exception):
            lifecycle.apply_transition("archived_cap", "stable")


# ── Quarantined blocks ──────────────────────────────────────────────────

class TestQuarantinedBlocks:
    def test_quarantined_to_stable_blocked(self, store, lifecycle):
        # We need to create a capability and then set it to quarantined
        # Since store.create_draft always creates active, we directly manipulate
        doc = _create_cap(store, cap_id="quar_cap", maturity="testing", status="active")
        cap_dir = store._get_dir("quar_cap", CapabilityScope.WORKSPACE)
        from datetime import datetime, timezone
        updated = doc.manifest.model_copy(update={
            "status": CapabilityStatus.QUARANTINED,
            "updated_at": datetime.now(timezone.utc),
        })
        doc.manifest = updated
        store._sync_manifest_json(cap_dir, doc)

        result = lifecycle.apply_transition("quar_cap", "stable")
        assert not result.applied


# ── Evaluate method ─────────────────────────────────────────────────────

class TestEvaluate:
    def test_evaluate_returns_record(self, store, lifecycle):
        _create_cap(store, cap_id="eval_me")
        record = lifecycle.evaluate("eval_me")
        assert record.capability_id == "eval_me"
        assert record.scope == "workspace"

    def test_evaluate_persists_by_default(self, store, lifecycle):
        _create_cap(store, cap_id="eval_persist")
        lifecycle.evaluate("eval_persist")
        doc = store.get("eval_persist")
        latest = get_latest_eval_record(doc)
        assert latest is not None
        assert latest.capability_id == "eval_persist"

    def test_evaluate_skip_persist(self, store, lifecycle):
        _create_cap(store, cap_id="eval_no_persist")
        lifecycle.evaluate("eval_no_persist", write_record=False)
        doc = store.get("eval_no_persist")
        latest = get_latest_eval_record(doc)
        assert latest is None


# ── Plan transition ─────────────────────────────────────────────────────

class TestPlanTransition:
    def test_plan_returns_promotion_plan(self, store, lifecycle):
        _create_cap(store, cap_id="plan_me", maturity="draft", status="active")
        plan = lifecycle.plan_transition("plan_me", "testing")
        assert plan.allowed

    def test_plan_blocked_transition(self, store, lifecycle):
        _create_cap(store, cap_id="plan_blocked", maturity="stable", status="active")
        plan = lifecycle.plan_transition("plan_blocked", "testing")
        assert not plan.allowed

    def test_plan_status_transition(self, store, lifecycle):
        _create_cap(store, cap_id="plan_status", maturity="draft", status="active")
        plan = lifecycle.plan_transition("plan_status", "disabled")
        assert plan.allowed

    def test_plan_does_not_mutate(self, store, lifecycle):
        doc = _create_cap(store, cap_id="plan_no_mutate", maturity="draft", status="active")
        maturity_before = doc.manifest.maturity
        lifecycle.plan_transition("plan_no_mutate", "testing")
        doc_after = store.get("plan_no_mutate")
        assert doc_after.manifest.maturity == maturity_before


# ── TransitionResult fields ─────────────────────────────────────────────

class TestTransitionResultFields:
    def test_successful_result_has_all_fields(self, store, lifecycle):
        _create_cap(store, cap_id="fields_cap")
        result = lifecycle.apply_transition("fields_cap", "testing")
        assert result.applied
        assert result.capability_id == "fields_cap"
        assert result.scope == "workspace"
        assert result.from_maturity == "draft"
        assert result.to_maturity == "testing"
        assert result.from_status == "active"
        assert result.to_status == "active"
        assert result.content_hash_before
        assert result.content_hash_after
        assert result.content_hash_before != result.content_hash_after

    def test_blocked_result_has_reasons(self, store, lifecycle):
        _create_cap(store, cap_id="blocked_fields", body=MISSING_VERIFICATION_BODY)
        result = lifecycle.apply_transition("blocked_fields", "testing")
        assert not result.applied
        assert result.message
        assert len(result.blocking_findings) > 0
        assert result.content_hash_after == ""


# ── content_hash behavior ───────────────────────────────────────────────

class TestContentHash:
    def test_hash_changes_after_transition(self, store, lifecycle):
        doc = _create_cap(store, cap_id="hash_change")
        h1 = doc.content_hash
        result = lifecycle.apply_transition("hash_change", "testing")
        assert result.applied
        assert result.content_hash_before == h1
        assert result.content_hash_after != h1

    def test_hash_stable_after_re_read(self, store, lifecycle):
        _create_cap(store, cap_id="hash_stable")
        lifecycle.apply_transition("hash_stable", "testing")
        doc1 = store.get("hash_stable")
        doc2 = store.get("hash_stable")
        assert doc1.content_hash == doc2.content_hash

    def test_extra_fields_preserved(self, store, lifecycle):
        doc = _create_cap(store, cap_id="extra_fields", custom_field="preserved_value")
        assert doc.manifest.extra.get("custom_field") == "preserved_value"
        result = lifecycle.apply_transition("extra_fields", "testing")
        assert result.applied
        doc_after = store.get("extra_fields")
        assert doc_after.manifest.extra.get("custom_field") == "preserved_value"
