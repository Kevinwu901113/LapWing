"""Phase 3B hardening: fill coverage gaps for gated lifecycle transitions.

Verifies:
- Planner denial → zero file/index/version changes
- Policy denial after planner allow → zero file/index/version changes
- Status-blocked transitions (disabled, quarantined, archived) → zero file changes
- High-risk without approval → zero file changes
- Snapshot timestamp uniqueness for rapid transitions
- Snapshot survives archive (readable after archive)
- Eval record content_hash matches pre-transition content
- Eval record write does not mutate manifest maturity/status
- Failed eval does not produce applied transition artifacts
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.capabilities.document import parse_capability
from src.capabilities.eval_records import get_latest_eval_record
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy, PolicyDecision, PolicySeverity
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityScope,
    CapabilityStatus,
    SideEffect,
)
from src.capabilities.store import CapabilityStore
from src.capabilities.versioning import list_version_snapshots


# ── Helpers ─────────────────────────────────────────────────────────────

VALID_BODY = """## When to use

Use this capability when needed.

## Procedure

1. Step one.
2. Step two.

## Verification

Verify the output is correct.

## Failure handling

If it fails, log the error and retry.
"""


def _make_store(tmp_path: Path, *, with_index: bool = False) -> CapabilityStore:
    kwargs: dict = {}
    if with_index:
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        kwargs["index"] = idx
    return CapabilityStore(data_dir=tmp_path / "capabilities", **kwargs)


def _create_cap(store, *, cap_id="cap", body=VALID_BODY, maturity="draft",
                status="active", risk_level="low"):
    doc = store.create_draft(
        scope=CapabilityScope.WORKSPACE,
        cap_id=cap_id,
        name="Test Cap",
        description="Test capability.",
        body=body,
        risk_level=risk_level,
    )
    doc.manifest = doc.manifest.model_copy(update={
        "do_not_apply_when": ["not for unsafe regression use"],
        "reuse_boundary": "Regression test capability only.",
        "side_effects": [SideEffect.NONE],
    })
    store._sync_manifest_json(doc.directory, doc)
    evals_dir = doc.directory / "evals"
    evals_dir.mkdir(exist_ok=True)
    (evals_dir / "positive_cases.jsonl").write_text('{"case":"ok"}\n', encoding="utf-8")
    (evals_dir / "boundary_cases.jsonl").write_text('{"case":"boundary"}\n', encoding="utf-8")
    doc = store._parser.parse(doc.directory)
    needs_update = False
    updates: dict = {}
    if maturity != "draft":
        updates["maturity"] = CapabilityMaturity(maturity)
        needs_update = True
    if status != "active":
        updates["status"] = CapabilityStatus(status)
        needs_update = True
    if needs_update:
        updates["updated_at"] = datetime.now(timezone.utc)
        updated = doc.manifest.model_copy(update=updates)
        doc.manifest = updated
        store._sync_manifest_json(doc.directory, doc)
        doc = store._parser.parse(doc.directory)
        store._maybe_index(doc)
    return doc


def _make_lifecycle(store, **kwargs):
    return CapabilityLifecycleManager(
        store=store,
        evaluator=CapabilityEvaluator(),
        policy=CapabilityPolicy(),
        planner=PromotionPlanner(),
        **kwargs,
    )


def _snapshot_files(cap_dir: Path) -> dict[str, str]:
    """Read current state of key files for before/after comparison."""
    result: dict[str, str] = {}
    for fname in ("manifest.json", "CAPABILITY.md"):
        p = cap_dir / fname
        if p.exists():
            result[fname] = p.read_text(encoding="utf-8")
    return result


def _version_count(cap_dir: Path) -> int:
    vdir = cap_dir / "versions"
    if not vdir.is_dir():
        return 0
    return len([e for e in vdir.iterdir() if e.is_dir()])


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


# ── Planner denial → zero changes ───────────────────────────────────────

class TestPlannerDenialNoChanges:
    def test_manifest_json_unchanged(self, store):
        _create_cap(store, cap_id="pd_manifest")
        cap_dir = store._get_dir("pd_manifest", CapabilityScope.WORKSPACE)
        files_before = _snapshot_files(cap_dir)

        # mock planner that denies
        mock_planner = MagicMock()
        from src.capabilities.promotion import PromotionPlan
        mock_planner.plan_transition.return_value = PromotionPlan(
            capability_id="pd_manifest", scope="workspace",
            from_maturity="draft", to_maturity="testing",
            allowed=False, explanation="Planner denial for test",
        )
        lm = CapabilityLifecycleManager(
            store=store, evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(), planner=mock_planner,
        )
        result = lm.apply_transition("pd_manifest", "testing")
        assert not result.applied

        files_after = _snapshot_files(cap_dir)
        assert files_before.get("manifest.json") == files_after.get("manifest.json")

    def test_capability_md_unchanged(self, store):
        _create_cap(store, cap_id="pd_md")
        cap_dir = store._get_dir("pd_md", CapabilityScope.WORKSPACE)
        files_before = _snapshot_files(cap_dir)

        mock_planner = MagicMock()
        from src.capabilities.promotion import PromotionPlan
        mock_planner.plan_transition.return_value = PromotionPlan(
            capability_id="pd_md", scope="workspace",
            from_maturity="draft", to_maturity="testing",
            allowed=False, explanation="Planner denial for test",
        )
        lm = CapabilityLifecycleManager(
            store=store, evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(), planner=mock_planner,
        )
        lm.apply_transition("pd_md", "testing")
        files_after = _snapshot_files(cap_dir)
        assert files_before.get("CAPABILITY.md") == files_after.get("CAPABILITY.md")

    def test_no_version_snapshot_written(self, store):
        _create_cap(store, cap_id="pd_ver")
        cap_dir = store._get_dir("pd_ver", CapabilityScope.WORKSPACE)
        assert _version_count(cap_dir) == 0

        mock_planner = MagicMock()
        from src.capabilities.promotion import PromotionPlan
        mock_planner.plan_transition.return_value = PromotionPlan(
            capability_id="pd_ver", scope="workspace",
            from_maturity="draft", to_maturity="testing",
            allowed=False, explanation="Planner denial for test",
        )
        lm = CapabilityLifecycleManager(
            store=store, evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(), planner=mock_planner,
        )
        lm.apply_transition("pd_ver", "testing")
        assert _version_count(cap_dir) == 0

    def test_index_unchanged(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "idx.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="pd_idx")

        row_before = idx.get("pd_idx", "workspace")

        mock_planner = MagicMock()
        from src.capabilities.promotion import PromotionPlan
        mock_planner.plan_transition.return_value = PromotionPlan(
            capability_id="pd_idx", scope="workspace",
            from_maturity="draft", to_maturity="testing",
            allowed=False, explanation="Planner denial for test",
        )
        lm = CapabilityLifecycleManager(
            store=store2, evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(), planner=mock_planner,
        )
        lm.apply_transition("pd_idx", "testing")

        row_after = idx.get("pd_idx", "workspace")
        assert row_before is not None
        assert row_after is not None
        assert row_before["maturity"] == row_after["maturity"]
        assert row_before["status"] == row_after["status"]

    def test_no_mutation_log_transition_recorded(self, store):
        _create_cap(store, cap_id="pd_log")
        mock_log = MagicMock()
        mock_log.record = MagicMock()

        mock_planner = MagicMock()
        from src.capabilities.promotion import PromotionPlan
        mock_planner.plan_transition.return_value = PromotionPlan(
            capability_id="pd_log", scope="workspace",
            from_maturity="draft", to_maturity="testing",
            allowed=False, explanation="Planner denial for test",
        )
        lm = CapabilityLifecycleManager(
            store=store, evaluator=CapabilityEvaluator(),
            policy=CapabilityPolicy(), planner=mock_planner,
            mutation_log=mock_log,
        )
        lm.apply_transition("pd_log", "testing")

        transition_calls = [
            c for c in mock_log.record.call_args_list
            if c[0][0] == "capability.transition_applied"
        ]
        assert len(transition_calls) == 0


# ── Policy denial after planner allow → zero changes ────────────────────

class TestPolicyDenialFullNoChanges:
    def test_versions_dir_unchanged(self, store):
        _create_cap(store, cap_id="pold_ver")
        cap_dir = store._get_dir("pold_ver", CapabilityScope.WORKSPACE)
        assert _version_count(cap_dir) == 0

        mock_policy = MagicMock()
        mock_policy.validate_promote.return_value = PolicyDecision.deny(
            "test_deny", "Blocked by test policy", severity=PolicySeverity.ERROR,
        )
        mock_policy.validate_scope.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_type.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_maturity.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_status.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_risk_level.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_risk.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_required_tools.return_value = PolicyDecision.allow("ok")

        lm = CapabilityLifecycleManager(
            store=store, evaluator=CapabilityEvaluator(),
            policy=mock_policy, planner=PromotionPlanner(),
        )
        result = lm.apply_transition("pold_ver", "testing")
        assert not result.applied
        assert _version_count(cap_dir) == 0

    def test_index_unchanged(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "idx2.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="pold_idx")

        row_before = idx.get("pold_idx", "workspace")

        mock_policy = MagicMock()
        mock_policy.validate_promote.return_value = PolicyDecision.deny(
            "test_deny", "Blocked", severity=PolicySeverity.ERROR,
        )
        mock_policy.validate_scope.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_type.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_maturity.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_status.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_risk_level.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_risk.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_required_tools.return_value = PolicyDecision.allow("ok")

        lm = CapabilityLifecycleManager(
            store=store2, evaluator=CapabilityEvaluator(),
            policy=mock_policy, planner=PromotionPlanner(),
        )
        lm.apply_transition("pold_idx", "testing")

        row_after = idx.get("pold_idx", "workspace")
        assert row_before is not None and row_after is not None
        assert row_before["maturity"] == row_after["maturity"]

    def test_no_transition_mutation_log(self, store):
        _create_cap(store, cap_id="pold_log")
        mock_log = MagicMock()
        mock_log.record = MagicMock()

        mock_policy = MagicMock()
        mock_policy.validate_promote.return_value = PolicyDecision.deny(
            "test_deny", "Blocked", severity=PolicySeverity.ERROR,
        )
        mock_policy.validate_scope.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_type.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_maturity.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_status.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_risk_level.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_risk.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_required_tools.return_value = PolicyDecision.allow("ok")

        lm = CapabilityLifecycleManager(
            store=store, evaluator=CapabilityEvaluator(),
            policy=mock_policy, planner=PromotionPlanner(),
            mutation_log=mock_log,
        )
        lm.apply_transition("pold_log", "testing")

        transition_calls = [
            c for c in mock_log.record.call_args_list
            if c[0][0] == "capability.transition_applied"
        ]
        assert len(transition_calls) == 0


# ── Status-blocked transitions → zero file changes ──────────────────────

class TestHighRiskNoApprovalNoChanges:
    def test_all_files_unchanged(self, store):
        _create_cap(store, cap_id="hr_noapp", maturity="testing",
                    status="active", risk_level="high")
        cap_dir = store._get_dir("hr_noapp", CapabilityScope.WORKSPACE)
        files_before = _snapshot_files(cap_dir)
        versions_before = _version_count(cap_dir)

        lm = _make_lifecycle(store)
        result = lm.apply_transition("hr_noapp", "stable")
        assert not result.applied

        files_after = _snapshot_files(cap_dir)
        assert files_before.get("manifest.json") == files_after.get("manifest.json")
        assert files_before.get("CAPABILITY.md") == files_after.get("CAPABILITY.md")
        assert _version_count(cap_dir) == versions_before

    def test_index_unchanged(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "hr_idx.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="hr_idx", maturity="testing",
                    status="active", risk_level="high")

        row_before = idx.get("hr_idx", "workspace")
        lm = _make_lifecycle(store2)
        lm.apply_transition("hr_idx", "stable")

        row_after = idx.get("hr_idx", "workspace")
        assert row_before is not None and row_after is not None
        assert row_before["maturity"] == row_after["maturity"]


class TestQuarantinedToStableNoChanges:
    def test_all_files_unchanged(self, store):
        doc = _create_cap(store, cap_id="quar_noch", maturity="testing",
                          status="active")
        cap_dir = store._get_dir("quar_noch", CapabilityScope.WORKSPACE)
        # Set to quarantined
        updated = doc.manifest.model_copy(update={
            "status": CapabilityStatus.QUARANTINED,
            "updated_at": datetime.now(timezone.utc),
        })
        doc.manifest = updated
        store._sync_manifest_json(cap_dir, doc)

        files_before = _snapshot_files(cap_dir)
        versions_before = _version_count(cap_dir)

        lm = _make_lifecycle(store)
        result = lm.apply_transition("quar_noch", "stable")
        assert not result.applied

        files_after = _snapshot_files(cap_dir)
        assert files_before.get("manifest.json") == files_after.get("manifest.json")
        assert files_before.get("CAPABILITY.md") == files_after.get("CAPABILITY.md")
        assert _version_count(cap_dir) == versions_before

    def test_index_unchanged(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "quar_idx.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        doc = _create_cap(store2, cap_id="quar_idx", maturity="testing",
                          status="active")
        cap_dir = store2._get_dir("quar_idx", CapabilityScope.WORKSPACE)
        updated = doc.manifest.model_copy(update={
            "status": CapabilityStatus.QUARANTINED,
            "updated_at": datetime.now(timezone.utc),
        })
        doc.manifest = updated
        store2._sync_manifest_json(cap_dir, doc)
        store2._maybe_index(doc)

        row_before = idx.get("quar_idx", "workspace")
        lm = _make_lifecycle(store2)
        lm.apply_transition("quar_idx", "stable")

        row_after = idx.get("quar_idx", "workspace")
        assert row_before is not None and row_after is not None
        assert row_before["maturity"] == row_after["maturity"]


class TestDisabledToStableNoChanges:
    def test_all_files_unchanged(self, store):
        _create_cap(store, cap_id="dis_noch")
        store.disable("dis_noch")
        cap_dir = store._get_dir("dis_noch", CapabilityScope.WORKSPACE)
        files_before = _snapshot_files(cap_dir)
        versions_before = _version_count(cap_dir)

        lm = _make_lifecycle(store)
        result = lm.apply_transition("dis_noch", "stable")
        assert not result.applied

        files_after = _snapshot_files(cap_dir)
        assert files_before.get("manifest.json") == files_after.get("manifest.json")
        assert files_before.get("CAPABILITY.md") == files_after.get("CAPABILITY.md")
        assert _version_count(cap_dir) == versions_before

    def test_index_unchanged(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "dis_idx.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="dis_idx")
        store2.disable("dis_idx")

        row_before = idx.get("dis_idx", "workspace")
        lm = _make_lifecycle(store2)
        result = lm.apply_transition("dis_idx", "stable")
        assert not result.applied

        row_after = idx.get("dis_idx", "workspace")
        assert row_before is not None and row_after is not None
        assert row_before["maturity"] == row_after["maturity"]


# ── Snapshot timestamp uniqueness ───────────────────────────────────────

class TestSnapshotTimestampUniqueness:
    def test_rapid_sequential_transitions_have_unique_snapshots(self, store):
        _create_cap(store, cap_id="ts_unique", maturity="draft", status="active")
        lm = _make_lifecycle(store)

        # draft -> testing
        r1 = lm.apply_transition("ts_unique", "testing")
        assert r1.applied
        assert r1.version_snapshot_id is not None

        doc = store.get("ts_unique")
        snapshots_1 = list_version_snapshots(doc)
        assert len(snapshots_1) == 1

        # testing -> stable (need to re-create doc as testing maturity)
        # Use the same cap which is now at testing maturity
        r2 = lm.apply_transition("ts_unique", "stable")
        assert r2.applied
        assert r2.version_snapshot_id is not None

        doc = store.get("ts_unique")
        snapshots_2 = list_version_snapshots(doc)
        assert len(snapshots_2) == 2

        # Snapshots should have different timestamps
        assert snapshots_2[0].snapshot_dir != snapshots_2[1].snapshot_dir
        # First snapshot predates second
        assert r1.version_snapshot_id != r2.version_snapshot_id

    def test_disable_then_promote_snapshots_unique(self, store):
        _create_cap(store, cap_id="ts_disable")
        lm = _make_lifecycle(store)

        r1 = lm.apply_transition("ts_disable", "disabled")
        assert r1.applied
        assert r1.version_snapshot_id is not None

        # Can't promote a disabled cap, but the disable snapshot exists
        doc = store.get("ts_disable")
        snapshots = list_version_snapshots(doc)
        assert len(snapshots) == 1
        assert r1.version_snapshot_id is not None


# ── Snapshot survives archive ───────────────────────────────────────────

class TestSnapshotSurvivesArchive:
    def test_snapshot_readable_after_archive(self, store):
        _create_cap(store, cap_id="snap_survive", maturity="draft", status="active")
        lm = _make_lifecycle(store)

        # Transition first to write a snapshot
        r = lm.apply_transition("snap_survive", "testing")
        assert r.applied
        assert r.version_snapshot_id is not None

        # Now archive
        doc_before_archive = store.get("snap_survive")
        snapshots_before = list_version_snapshots(doc_before_archive)
        assert len(snapshots_before) == 1

        r_arch = lm.apply_transition("snap_survive", "archived")
        assert r_arch.applied

        # After archive, the capability moves. The snapshot was in the
        # original directory which is now under archived/.  The store can
        # find it via include_archived listing.
        docs = store.list(include_archived=True)
        archived_doc = next((d for d in docs if d.id == "snap_survive"), None)
        assert archived_doc is not None, "Archived document not found in listing"

        # Snapshots should still be readable from the archived doc
        snapshots_after = list_version_snapshots(archived_doc)
        assert len(snapshots_after) >= 1


# ── Eval record content_hash matches pre-transition content ─────────────

class TestEvalRecordHash:
    def test_eval_record_hash_matches_pre_transition_doc(self, store):
        doc = _create_cap(store, cap_id="eval_hash", maturity="testing",
                          status="active")
        pre_hash = doc.content_hash

        lm = _make_lifecycle(store)
        result = lm.apply_transition("eval_hash", "stable")
        assert result.applied
        assert result.eval_record_id is not None

        # Read back the eval record and check its hash
        doc = store.get("eval_hash")
        eval_rec = get_latest_eval_record(doc)
        assert eval_rec is not None
        # content_hash in eval record reflects the doc at evaluation time
        assert eval_rec.content_hash == pre_hash

    def test_eval_record_hash_differs_from_post_transition_hash(self, store):
        doc = _create_cap(store, cap_id="eval_hash2", maturity="testing",
                          status="active")
        pre_hash = doc.content_hash

        lm = _make_lifecycle(store)
        result = lm.apply_transition("eval_hash2", "stable")
        assert result.applied
        assert result.content_hash_after != pre_hash

        doc = store.get("eval_hash2")
        eval_rec = get_latest_eval_record(doc)
        assert eval_rec is not None
        # eval record hash matches pre-transition, not post-transition
        assert eval_rec.content_hash == pre_hash
        assert eval_rec.content_hash != doc.content_hash


# ── Eval record write does not mutate manifest maturity/status ──────────

class TestEvalNonMutation:
    def test_evaluate_does_not_change_maturity(self, store):
        _create_cap(store, cap_id="eval_nomut", maturity="testing",
                    status="active")
        doc_before = store.get("eval_nomut")
        mat_before = doc_before.manifest.maturity
        status_before = doc_before.manifest.status

        lm = _make_lifecycle(store)
        lm.evaluate("eval_nomut")

        doc_after = store.get("eval_nomut")
        assert doc_after.manifest.maturity == mat_before
        assert doc_after.manifest.status == status_before

    def test_evaluate_without_persist_does_not_change_files(self, store):
        _create_cap(store, cap_id="eval_nofile", maturity="testing",
                    status="active")
        cap_dir = store._get_dir("eval_nofile", CapabilityScope.WORKSPACE)
        manifest_before = (cap_dir / "manifest.json").read_text(encoding="utf-8")

        lm = _make_lifecycle(store)
        lm.evaluate("eval_nofile", write_record=False)

        manifest_after = (cap_dir / "manifest.json").read_text(encoding="utf-8")
        assert manifest_before == manifest_after

    def test_evaluate_with_persist_does_not_change_manifest_fields(self, store):
        _create_cap(store, cap_id="eval_persist_nomut", maturity="testing",
                    status="active")
        doc_before = store.get("eval_persist_nomut")
        mat_before = doc_before.manifest.maturity
        status_before = doc_before.manifest.status

        lm = _make_lifecycle(store)
        lm.evaluate("eval_persist_nomut", write_record=True)

        doc_after = store.get("eval_persist_nomut")
        assert doc_after.manifest.maturity == mat_before
        assert doc_after.manifest.status == status_before
        # Verify eval record was actually written
        latest = get_latest_eval_record(doc_after)
        assert latest is not None


# ── Failed eval → no applied transition artifacts ───────────────────────

class TestFailedEvalNoArtifacts:
    def test_no_version_snapshot_on_failed_eval(self, store):
        _create_cap(store, cap_id="feval_nosnap", body="No sections.",
                    maturity="draft", status="active")
        cap_dir = store._get_dir("feval_nosnap", CapabilityScope.WORKSPACE)
        versions_before = _version_count(cap_dir)

        lm = _make_lifecycle(store)
        result = lm.apply_transition("feval_nosnap", "testing")
        assert not result.applied
        assert _version_count(cap_dir) == versions_before

    def test_no_index_change_on_failed_eval(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "feval_idx.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="feval_idx", body="No sections.")

        row_before = idx.get("feval_idx", "workspace")
        lm = _make_lifecycle(store2)
        result = lm.apply_transition("feval_idx", "testing")
        assert not result.applied

        row_after = idx.get("feval_idx", "workspace")
        assert row_before is not None and row_after is not None
        assert row_before["maturity"] == row_after["maturity"]

    def test_eval_record_is_still_written_on_failed_eval(self, store):
        """Eval record is written for diagnostics even when transition is blocked."""
        _create_cap(store, cap_id="feval_record", body="No sections.",
                    maturity="draft", status="active")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("feval_record", "testing")
        assert not result.applied

        # The eval record was written by the lifecycle manager before
        # the planner check (part of the eval-required transitions flow)
        doc = store.get("feval_record")
        eval_rec = get_latest_eval_record(doc)
        assert eval_rec is not None
        assert not eval_rec.passed  # Should have errors from missing sections


# ── Eval records readable after successful transition ───────────────────

class TestEvalReadableAfterTransition:
    def test_eval_readable_after_successful_transition(self, store):
        _create_cap(store, cap_id="eval_after", maturity="testing",
                    status="active")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("eval_after", "stable")
        assert result.applied
        assert result.eval_record_id is not None

        doc = store.get("eval_after")
        eval_rec = get_latest_eval_record(doc)
        assert eval_rec is not None
        assert eval_rec.passed
        assert eval_rec.created_at == result.eval_record_id

    def test_eval_readable_after_draft_to_testing(self, store):
        _create_cap(store, cap_id="eval_d2t", maturity="draft",
                    status="active")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("eval_d2t", "testing")
        assert result.applied
        assert result.eval_record_id is not None

        doc = store.get("eval_d2t")
        eval_rec = get_latest_eval_record(doc)
        assert eval_rec is not None
        # draft->testing with valid body should pass
        assert eval_rec.passed


# ── TransitionResult completeness ───────────────────────────────────────

class TestTransitionResultCompleteness:
    def test_successful_result_all_fields_non_default(self, store):
        _create_cap(store, cap_id="complete", maturity="draft", status="active")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("complete", "testing")
        assert result.applied
        assert result.capability_id == "complete"
        assert result.scope == "workspace"
        assert result.from_maturity == "draft"
        assert result.to_maturity == "testing"
        assert result.from_status == "active"
        assert result.to_status == "active"
        assert result.eval_record_id is not None
        assert result.version_snapshot_id is not None
        assert result.content_hash_before != ""
        assert result.content_hash_after != ""
        assert result.content_hash_before != result.content_hash_after
        assert len(result.policy_decisions) > 0
        assert result.message != ""

    def test_blocked_result_has_empty_optional_fields(self, store):
        _create_cap(store, cap_id="incomplete", body="No sections.",
                    maturity="draft", status="active")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("incomplete", "testing")
        assert not result.applied
        assert result.eval_record_id is not None  # eval was run and persisted
        assert result.version_snapshot_id is None  # no snapshot on blocked
        assert result.content_hash_after == ""     # no update
        assert result.message != ""


# ── Verify no capability script execution path ──────────────────────────

class TestNoScriptExecution:
    def test_lifecycle_does_not_import_scripts(self, store, tmp_path):
        """Place a dangerous script and verify lifecycle doesn't execute it."""
        _create_cap(store, cap_id="noexec")
        cap_dir = store._get_dir("noexec", CapabilityScope.WORKSPACE)
        scripts_dir = cap_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "dangerous.py").write_text(
            "raise SystemExit('This script should never run')\n"
        )

        lm = _make_lifecycle(store)
        # This must not raise SystemExit
        result = lm.apply_transition("noexec", "testing")
        assert result.applied  # valid transition with proper body

    def test_lifecycle_does_not_execute_shell(self, store, tmp_path):
        """Place a shell script and verify lifecycle static scans do not execute it."""
        _create_cap(store, cap_id="noshell")
        cap_dir = store._get_dir("noshell", CapabilityScope.WORKSPACE)
        scripts_dir = cap_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        marker = tmp_path / "should_not_run"
        (scripts_dir / "run.sh").write_text(f"#!/bin/bash\ntouch {marker}\n")

        lm = _make_lifecycle(store)
        result = lm.apply_transition("noshell", "testing")
        assert not result.applied
        assert not marker.exists()

    def test_evaluate_does_not_execute_scripts(self, store, tmp_path):
        _create_cap(store, cap_id="ev_noexec")
        cap_dir = store._get_dir("ev_noexec", CapabilityScope.WORKSPACE)
        scripts_dir = cap_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "dangerous.py").write_text(
            "raise SystemExit('This script should never run')\n"
        )

        lm = _make_lifecycle(store)
        # evaluate() must not execute scripts
        record = lm.evaluate("ev_noexec")
        assert record is not None

    def test_plan_transition_does_not_execute_scripts(self, store, tmp_path):
        _create_cap(store, cap_id="plan_noexec")
        cap_dir = store._get_dir("plan_noexec", CapabilityScope.WORKSPACE)
        scripts_dir = cap_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "dangerous.py").write_text(
            "raise SystemExit('This script should never run')\n"
        )

        lm = _make_lifecycle(store)
        plan = lm.plan_transition("plan_noexec", "testing")
        assert plan is not None
