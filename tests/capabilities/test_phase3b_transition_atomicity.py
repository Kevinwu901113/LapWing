"""Phase 3B tests: transition atomicity, no-mutation-on-failure, snapshot/index/mutation log."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.index import CapabilityIndex
from src.capabilities.lifecycle import CapabilityLifecycleManager
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.promotion import PromotionPlanner
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityScope,
    CapabilityStatus,
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

Verify the output.

## Failure handling

Log and retry on failure.
"""


def _make_store(tmp_path: Path, *, with_index: bool = False) -> CapabilityStore:
    kwargs: dict = {}
    if with_index:
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        kwargs["index"] = idx
    return CapabilityStore(data_dir=tmp_path / "capabilities", **kwargs)


def _create_cap(store, *, cap_id="cap", body=VALID_BODY, maturity="draft", status="active", risk_level="low"):
    return store.create_draft(
        scope=CapabilityScope.WORKSPACE,
        cap_id=cap_id,
        name="Test Cap",
        description="Test capability.",
        body=body,
        risk_level=risk_level,
    )


def _make_lifecycle(store, **kwargs):
    return CapabilityLifecycleManager(
        store=store,
        evaluator=CapabilityEvaluator(),
        policy=CapabilityPolicy(),
        planner=PromotionPlanner(),
        **kwargs,
    )


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


# ── No-mutation on blocked transition ───────────────────────────────────

class TestNoMutationOnBlock:
    def test_blocked_does_not_change_manifest_json(self, store, tmp_path):
        _create_cap(store, cap_id="no_mut_json", body="No sections.")
        cap_dir = store._get_dir("no_mut_json", CapabilityScope.WORKSPACE)
        manifest_before = (cap_dir / "manifest.json").read_text(encoding="utf-8")

        lm = _make_lifecycle(store)
        result = lm.apply_transition("no_mut_json", "testing")
        assert not result.applied

        manifest_after = (cap_dir / "manifest.json").read_text(encoding="utf-8")
        assert manifest_before == manifest_after

    def test_blocked_does_not_change_capability_md(self, store, tmp_path):
        _create_cap(store, cap_id="no_mut_md", body="No sections.")
        cap_dir = store._get_dir("no_mut_md", CapabilityScope.WORKSPACE)
        cap_md_before = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")

        lm = _make_lifecycle(store)
        result = lm.apply_transition("no_mut_md", "testing")
        assert not result.applied

        cap_md_after = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")
        assert cap_md_before == cap_md_after

    def test_blocked_does_not_write_version_snapshot(self, store, tmp_path):
        _create_cap(store, cap_id="no_snap", body="No sections.")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("no_snap", "testing")
        assert not result.applied

        doc = store.get("no_snap")
        snapshots = list_version_snapshots(doc)
        assert len(snapshots) == 0

    def test_blocked_does_not_change_index(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="no_idx_change", body="No sections.")

        lm = _make_lifecycle(store2)
        result = lm.apply_transition("no_idx_change", "testing")
        assert not result.applied

        row = idx.get("no_idx_change", "workspace")
        assert row is not None
        assert row["maturity"] == "draft"

    def test_blocked_does_not_record_transition_event(self, store):
        _create_cap(store, cap_id="no_mut_log", body="No sections.")
        mock_log = MagicMock()
        mock_log.record = MagicMock()
        lm = _make_lifecycle(store, mutation_log=mock_log)

        result = lm.apply_transition("no_mut_log", "testing")
        assert not result.applied
        # Eval record write may call the log (eval.record_written), but
        # the transition event must NOT be recorded.
        transition_calls = [
            c for c in mock_log.record.call_args_list
            if c[0][0] == "capability.transition_applied"
        ]
        assert len(transition_calls) == 0


# ── Snapshot behavior on successful transition ──────────────────────────

class TestSnapshotOnSuccess:
    def test_successful_transition_writes_version_snapshot(self, store):
        _create_cap(store, cap_id="snap_ok")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("snap_ok", "testing")
        assert result.applied
        assert result.version_snapshot_id is not None

        doc = store.get("snap_ok")
        snapshots = list_version_snapshots(doc)
        assert len(snapshots) == 1

    def test_snapshot_includes_manifest_json(self, store):
        _create_cap(store, cap_id="snap_manifest")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("snap_manifest", "testing")
        assert result.applied

        doc = store.get("snap_manifest")
        snapshots = list_version_snapshots(doc)
        snap_dir = doc.directory / snapshots[0].snapshot_dir
        assert (snap_dir / "manifest.json").exists()

    def test_snapshot_includes_capability_md(self, store):
        _create_cap(store, cap_id="snap_md")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("snap_md", "testing")
        assert result.applied

        doc = store.get("snap_md")
        snapshots = list_version_snapshots(doc)
        snap_dir = doc.directory / snapshots[0].snapshot_dir
        assert (snap_dir / "CAPABILITY.md").exists()

    def test_snapshot_preserves_pre_transition_state(self, store):
        _create_cap(store, cap_id="snap_state")
        doc_before = store.get("snap_state")

        lm = _make_lifecycle(store)
        result = lm.apply_transition("snap_state", "testing")
        assert result.applied

        doc_after = store.get("snap_state")
        snapshots = list_version_snapshots(doc_after)
        snap_dir = doc_after.directory / snapshots[0].snapshot_dir
        snap_manifest = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
        assert snap_manifest["maturity"] == "draft"

    def test_disable_transition_writes_snapshot(self, store):
        _create_cap(store, cap_id="snap_disable")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("snap_disable", "disabled")
        assert result.applied

        doc = store.get("snap_disable")
        snapshots = list_version_snapshots(doc)
        assert len(snapshots) >= 1

    def test_archive_transition_writes_snapshot(self, store):
        _create_cap(store, cap_id="snap_archive")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("snap_archive", "archived")
        assert result.applied
        assert result.version_snapshot_id is not None


# ── Index refresh on successful transition ──────────────────────────────

class TestIndexRefresh:
    def test_successful_transition_refreshes_index(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="idx_refresh")

        lm = _make_lifecycle(store2)
        result = lm.apply_transition("idx_refresh", "testing")
        assert result.applied

        row = idx.get("idx_refresh", "workspace")
        assert row is not None
        assert row["maturity"] == "testing"

    def test_search_sees_new_maturity_after_transition(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="search_new")

        lm = _make_lifecycle(store2)
        lm.apply_transition("search_new", "testing")

        results = idx.search(filters={"maturity": "testing"})
        matching_ids = [r["id"] for r in results]
        assert "search_new" in matching_ids

    def test_search_sees_new_status_after_disable(self, store, tmp_path):
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="search_disabled")

        lm = _make_lifecycle(store2)
        lm.apply_transition("search_disabled", "disabled")

        row = idx.get("search_disabled", "workspace")
        assert row is not None
        assert row["status"] == "disabled"


# ── Mutation log behavior ───────────────────────────────────────────────

class TestMutationLog:
    def test_records_transition_when_provided(self, store):
        _create_cap(store, cap_id="mut_log")
        mock_log = MagicMock()
        mock_log.record = MagicMock()
        lm = _make_lifecycle(store, mutation_log=mock_log)

        result = lm.apply_transition("mut_log", "testing")
        assert result.applied
        assert mock_log.record.call_count >= 1

        # At least one call should be the transition event (eval record
        # may also be recorded first).
        transition_calls = [
            c for c in mock_log.record.call_args_list
            if c[0][0] == "capability.transition_applied"
        ]
        assert len(transition_calls) >= 1

    def test_works_with_mutation_log_none(self, store):
        _create_cap(store, cap_id="mut_none")
        lm = _make_lifecycle(store, mutation_log=None)
        result = lm.apply_transition("mut_none", "testing")
        assert result.applied

    def test_mutation_log_failure_does_not_corrupt_transition(self, store):
        _create_cap(store, cap_id="mut_fail")
        mock_log = MagicMock()
        mock_log.record = MagicMock(side_effect=RuntimeError("log write failed"))
        lm = _make_lifecycle(store, mutation_log=mock_log)

        result = lm.apply_transition("mut_fail", "testing")
        assert result.applied
        assert result.to_maturity == "testing"

        doc = store.get("mut_fail")
        assert doc.manifest.maturity == CapabilityMaturity.TESTING

    def test_mutation_log_failure_does_not_corrupt_disable(self, store):
        _create_cap(store, cap_id="mut_fail_disable")
        mock_log = MagicMock()
        mock_log.record = MagicMock(side_effect=RuntimeError("log write failed"))
        lm = _make_lifecycle(store, mutation_log=mock_log)

        result = lm.apply_transition("mut_fail_disable", "disabled")
        assert result.applied

        doc = store.get("mut_fail_disable")
        assert doc.manifest.status == CapabilityStatus.DISABLED

    def test_eval_record_log_failure_does_not_corrupt(self, store):
        _create_cap(store, cap_id="eval_log_fail")
        mock_log = MagicMock()
        mock_log.record = MagicMock(side_effect=RuntimeError("log write failed"))
        lm = _make_lifecycle(store, mutation_log=mock_log)

        result = lm.apply_transition("eval_log_fail", "testing")
        assert result.applied


# ── Policy denial → no files changed ────────────────────────────────────

class TestPolicyDenial:
    def test_policy_denial_does_not_change_files(self, store):
        _create_cap(store, cap_id="pol_deny")
        cap_dir = store._get_dir("pol_deny", CapabilityScope.WORKSPACE)
        manifest_before = (cap_dir / "manifest.json").read_text(encoding="utf-8")
        cap_md_before = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")

        # Create a lifecycle manager with a policy that always denies
        mock_policy = MagicMock()
        from src.capabilities.policy import PolicyDecision, PolicySeverity
        mock_policy.validate_promote.return_value = PolicyDecision.deny(
            "test_deny", "Policy denies for test", severity=PolicySeverity.ERROR,
        )
        # But the planner should allow it
        mock_policy.validate_scope.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_type.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_maturity.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_status.return_value = PolicyDecision.allow("ok")
        mock_policy._validate_risk_level.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_risk.return_value = PolicyDecision.allow("ok")
        mock_policy.validate_required_tools.return_value = PolicyDecision.allow("ok")

        lm = CapabilityLifecycleManager(
            store=store,
            evaluator=CapabilityEvaluator(),
            policy=mock_policy,
            planner=PromotionPlanner(),
        )
        result = lm.apply_transition("pol_deny", "testing")
        assert not result.applied

        manifest_after = (cap_dir / "manifest.json").read_text(encoding="utf-8")
        cap_md_after = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")
        assert manifest_before == manifest_after
        assert cap_md_before == cap_md_after


# ── Evaluator failure → no files changed ────────────────────────────────

class TestEvaluatorFailure:
    def test_evaluator_errors_block_transition(self, store):
        _create_cap(store, cap_id="eval_fail", body="No sections at all.")
        cap_dir = store._get_dir("eval_fail", CapabilityScope.WORKSPACE)
        manifest_before = (cap_dir / "manifest.json").read_text(encoding="utf-8")

        lm = _make_lifecycle(store)
        result = lm.apply_transition("eval_fail", "testing")
        assert not result.applied

        manifest_after = (cap_dir / "manifest.json").read_text(encoding="utf-8")
        assert manifest_before == manifest_after

    def test_evaluator_failure_no_files_changed(self, store):
        _create_cap(store, cap_id="eval_no_change", body="No sections.")
        cap_dir = store._get_dir("eval_no_change", CapabilityScope.WORKSPACE)
        cap_md_before = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")

        lm = _make_lifecycle(store)
        lm.apply_transition("eval_no_change", "testing")

        cap_md_after = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")
        assert cap_md_before == cap_md_after


# ── Index refresh failure handling ──────────────────────────────────────

class TestIndexRefreshFailure:
    def test_index_failure_after_manifest_write(self, store, tmp_path):
        """If index refresh fails, the manifest change is already written and
        the transition is still considered applied. This is the documented
        partial-failure behavior: manifest is durable, index is derived."""
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        store2 = CapabilityStore(data_dir=store.data_dir, index=idx)
        _create_cap(store2, cap_id="idx_fail")

        # Make upsert fail on the index
        original_upsert = idx.upsert
        def failing_upsert(doc):
            raise RuntimeError("index write failed")
        idx.upsert = failing_upsert

        lm = _make_lifecycle(store2)
        try:
            result = lm.apply_transition("idx_fail", "testing")
            # If we get here, the exception was caught
            # The manifest change should still be durable
            doc = store.get("idx_fail")
            # We expect the maturity to have changed even if index failed
        except RuntimeError:
            # If the exception propagated, manifest should still be changed
            doc = store.get("idx_fail")
        finally:
            idx.upsert = original_upsert

        # Manifest change is durable regardless of index failure
        doc = store.get("idx_fail")
        assert doc.manifest.maturity == CapabilityMaturity.TESTING


# ── content_hash consistency ────────────────────────────────────────────

class TestContentHashConsistency:
    def test_no_self_referential_hash_churn(self, store):
        _create_cap(store, cap_id="hash_churn")
        lm = _make_lifecycle(store)
        lm.apply_transition("hash_churn", "testing")

        # Read twice, hash should be the same
        doc1 = store.get("hash_churn")
        doc2 = store.get("hash_churn")
        assert doc1.content_hash == doc2.content_hash

    def test_hash_differs_from_before(self, store):
        doc = _create_cap(store, cap_id="hash_diff")
        h_before = doc.content_hash

        lm = _make_lifecycle(store)
        result = lm.apply_transition("hash_diff", "testing")
        assert result.applied
        assert result.content_hash_before != result.content_hash_after

    def test_hash_unchanged_when_blocked(self, store):
        doc = _create_cap(store, cap_id="hash_blocked", body="No sections.")
        h_before = doc.content_hash

        lm = _make_lifecycle(store)
        result = lm.apply_transition("hash_blocked", "testing")
        assert not result.applied
        assert result.content_hash_before == h_before
        assert result.content_hash_after == ""

        doc_after = store.get("hash_blocked")
        assert doc_after.content_hash == h_before


# ── Already-in-target-state ─────────────────────────────────────────────

class TestAlreadyInTargetState:
    def test_already_disabled_returns_blocked(self, store):
        _create_cap(store, cap_id="already_dis")
        store.disable("already_dis")
        lm = _make_lifecycle(store)
        result = lm.apply_transition("already_dis", "disabled")
        assert not result.applied

    def test_already_archived_raises_not_found(self, store):
        _create_cap(store, cap_id="already_arch")
        store.archive("already_arch")
        lm = _make_lifecycle(store)
        # After archive, the cap moves to archived dir; get raises.
        with pytest.raises(Exception):
            lm.apply_transition("already_arch", "archived")
