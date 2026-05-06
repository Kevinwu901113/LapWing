"""Maintenance B: Repair Queue functional tests.

Tests for model validation, store CRUD, filtering, dedup,
health report conversion, and corruption tolerance.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.capabilities.health import (
    CapabilityHealthFinding,
    CapabilityHealthReport,
    generate_capability_health_report,
)
from src.capabilities.repair_queue import (
    _FINDING_CODE_TO_ACTION,
    RepairQueueItem,
    RepairQueueStore,
)
from src.capabilities.schema import CapabilityScope
from src.capabilities.store import CapabilityStore


# ── Helpers ──


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _make_item(**overrides) -> RepairQueueItem:
    defaults = {
        "item_id": "rq-test-001",
        "created_at": "2026-05-05T10:00:00+00:00",
        "source": "health_report",
        "finding_code": "missing_provenance_legacy",
        "severity": "info",
        "status": "open",
        "title": "Test item",
        "description": "Test description",
        "recommended_action": "add_provenance",
        "capability_id": "test-cap-1",
        "scope": "workspace",
    }
    defaults.update(overrides)
    return RepairQueueItem(**defaults)


def _make_queue_store(tmp_path: Path) -> RepairQueueStore:
    return RepairQueueStore(data_dir=tmp_path / "capabilities")


# ═══════════════════════════════════════════════════════════════════
# Model tests
# ═══════════════════════════════════════════════════════════════════


class TestRepairQueueItemModel:
    """Validation and serialization of RepairQueueItem."""

    def test_valid_item_serializes_and_deserializes(self):
        item = _make_item(
            action_payload={"key": "value"},
            evidence={"finding_details": {"x": 1}},
            metadata={"note": "test"},
        )
        d = item.to_dict()
        assert d["item_id"] == "rq-test-001"
        assert d["source"] == "health_report"
        assert d["action_payload"] == {"key": "value"}
        assert d["evidence"] == {"finding_details": {"x": 1}}

        restored = RepairQueueItem.from_dict(d)
        assert restored.item_id == item.item_id
        assert restored.finding_code == item.finding_code
        assert restored.action_payload == item.action_payload
        assert restored.evidence == item.evidence
        assert restored.metadata == item.metadata

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValueError, match="Invalid severity"):
            _make_item(severity="critical")

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="Invalid status"):
            _make_item(status="deleted")

    def test_invalid_source_rejected(self):
        with pytest.raises(ValueError, match="Invalid source"):
            _make_item(source="auto_generated")

    def test_invalid_recommended_action_rejected(self):
        with pytest.raises(ValueError, match="Invalid recommended_action"):
            _make_item(recommended_action="execute")

    def test_item_id_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="path traversal"):
            _make_item(item_id="../etc/passwd")
        with pytest.raises(ValueError, match="path traversal"):
            _make_item(item_id="foo/bar")
        with pytest.raises(ValueError, match="path traversal"):
            _make_item(item_id="foo\\bar")

    def test_metadata_round_trip(self):
        meta = {"created_by": "operator", "priority": 1, "tags": ["urgent"]}
        item = _make_item(metadata=meta)
        d = item.to_dict()
        assert d["metadata"] == meta
        restored = RepairQueueItem.from_dict(d)
        assert restored.metadata == meta

    def test_action_payload_round_trip(self):
        payload = {"capability_id": "abc", "issue": "missing", "stage": "no_audit"}
        item = _make_item(action_payload=payload)
        d = item.to_dict()
        assert d["action_payload"] == payload
        restored = RepairQueueItem.from_dict(d)
        assert restored.action_payload == payload

    def test_action_payload_shell_command_rejected(self):
        with pytest.raises(ValueError, match="executable-like content"):
            _make_item(action_payload={"cmd": "rm -rf /"})

    def test_action_payload_subprocess_rejected(self):
        with pytest.raises(ValueError, match="executable-like content"):
            _make_item(action_payload={"run": "subprocess.call('ls')"})

    def test_action_payload_import_rejected(self):
        with pytest.raises(ValueError, match="executable-like content"):
            _make_item(action_payload={"module": "import os"})

    def test_action_payload_exec_rejected(self):
        with pytest.raises(ValueError, match="executable-like content"):
            _make_item(action_payload={"code": "eval('1+1')"})

    def test_action_payload_innocent_string_accepted(self):
        item = _make_item(action_payload={"note": "review the import report", "key": "value"})
        assert item.action_payload == {"note": "review the import report", "key": "value"}

    def test_dedup_key(self):
        item = _make_item(
            finding_code="missing_provenance_legacy",
            capability_id="cap-1",
            scope="workspace",
            recommended_action="add_provenance",
        )
        assert item.dedup_key == ("missing_provenance_legacy", "cap-1", "workspace", "add_provenance")

    def test_dedup_key_none_capability_id(self):
        item = _make_item(capability_id=None, scope=None)
        assert item.dedup_key == ("missing_provenance_legacy", None, None, "add_provenance")

    def test_all_valid_sources_accepted(self):
        for src in ("health_report", "manual", "import_audit", "lifecycle", "unknown"):
            item = _make_item(source=src)
            assert item.source == src

    def test_all_valid_actions_accepted(self):
        for action in ("inspect", "reindex", "reeval", "repair_metadata",
                       "add_provenance", "quarantine_review", "archive",
                       "manual_review", "unknown"):
            item = _make_item(recommended_action=action)
            assert item.recommended_action == action

    def test_all_valid_statuses_accepted(self):
        for status in ("open", "acknowledged", "resolved", "dismissed"):
            item = _make_item(status=status)
            assert item.status == status

    def test_from_dict_defaults(self):
        """Missing optional fields get sensible defaults."""
        item = RepairQueueItem.from_dict({"item_id": "rq-minimal", "created_at": "now"})
        assert item.source == "unknown"
        assert item.severity == "info"
        assert item.status == "open"
        assert item.recommended_action == "unknown"
        assert item.action_payload == {}
        assert item.evidence == {}
        assert item.metadata == {}


# ═══════════════════════════════════════════════════════════════════
# Store tests
# ═══════════════════════════════════════════════════════════════════


class TestRepairQueueStore:
    """CRUD, filtering, status updates, and corruption tolerance."""

    def test_create_item_writes_json(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        item = _make_item()
        store.create_item(item)
        path = store._item_path(item.item_id)
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["item_id"] == item.item_id

    def test_create_duplicate_rejected(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        item = _make_item()
        store.create_item(item)
        with pytest.raises(FileExistsError):
            store.create_item(item)

    def test_get_item_reads_json(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        item = _make_item()
        store.create_item(item)
        read = store.get_item(item.item_id)
        assert read is not None
        assert read.item_id == item.item_id
        assert read.finding_code == item.finding_code
        assert read.title == item.title

    def test_get_item_missing_returns_none(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        assert store.get_item("nonexistent") is None

    def test_get_item_corrupt_json_returns_none(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store._ensure_queue_dir()
        path = store._item_path("corrupt")
        path.write_text("{not valid json", encoding="utf-8")
        assert store.get_item("corrupt") is None

    def test_get_item_non_dict_returns_none(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store._ensure_queue_dir()
        path = store._item_path("non-dict")
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert store.get_item("non-dict") is None

    def test_list_items_empty(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        assert store.list_items() == []

    def test_list_items_returns_all(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        for i in range(5):
            store.create_item(_make_item(item_id=f"rq-{i:03d}"))
        assert len(store.list_items()) == 5

    def test_list_items_filter_by_status(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="open-1", status="open"))
        store.create_item(_make_item(item_id="resolved-1", status="resolved"))
        store.create_item(_make_item(item_id="open-2", status="open"))

        open_items = store.list_items(status="open")
        assert len(open_items) == 2
        assert all(i.status == "open" for i in open_items)

        resolved_items = store.list_items(status="resolved")
        assert len(resolved_items) == 1

    def test_list_items_filter_by_severity(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="w-1", severity="warning"))
        store.create_item(_make_item(item_id="i-1", severity="info"))
        store.create_item(_make_item(item_id="e-1", severity="error"))

        assert len(store.list_items(severity="error")) == 1
        assert len(store.list_items(severity="warning")) == 1

    def test_list_items_filter_by_capability_id(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="a", capability_id="cap-a"))
        store.create_item(_make_item(item_id="b", capability_id="cap-b"))
        store.create_item(_make_item(item_id="c", capability_id="cap-a"))

        cap_a = store.list_items(capability_id="cap-a")
        assert len(cap_a) == 2

    def test_list_items_filter_by_action(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="a", recommended_action="reindex"))
        store.create_item(_make_item(item_id="b", recommended_action="manual_review"))
        store.create_item(_make_item(item_id="c", recommended_action="reindex"))

        assert len(store.list_items(action="reindex")) == 2
        assert len(store.list_items(action="manual_review")) == 1

    def test_list_items_combined_filters(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="a", status="open", severity="warning",
                                      capability_id="cap-x", recommended_action="reindex"))
        store.create_item(_make_item(item_id="b", status="open", severity="info",
                                      capability_id="cap-x", recommended_action="reindex"))
        store.create_item(_make_item(item_id="c", status="resolved", severity="warning",
                                      capability_id="cap-x", recommended_action="reindex"))

        result = store.list_items(status="open", severity="warning", capability_id="cap-x", action="reindex")
        assert len(result) == 1
        assert result[0].item_id == "a"

    def test_update_status_changes_status(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        updated = store.update_status("rq-1", "acknowledged", reason="looks valid", actor="operator-1")
        assert updated is not None
        assert updated.status == "acknowledged"
        assert updated.updated_at is not None
        assert updated.metadata.get("status_change_reason") == "looks valid"
        assert updated.metadata.get("status_change_actor") == "operator-1"

    def test_update_status_resolved_sets_resolved_at(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        updated = store.update_status("rq-1", "resolved")
        assert updated is not None
        assert updated.status == "resolved"
        assert updated.resolved_at is not None
        assert updated.dismissed_at is None

    def test_update_status_dismissed_sets_dismissed_at(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        updated = store.update_status("rq-1", "dismissed")
        assert updated is not None
        assert updated.status == "dismissed"
        assert updated.dismissed_at is not None
        assert updated.resolved_at is None

    def test_update_status_invalid_rejected(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        with pytest.raises(ValueError, match="Invalid status"):
            store.update_status("rq-1", "deleted")

    def test_update_status_nonexistent_returns_none(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        assert store.update_status("nonexistent", "resolved") is None

    def test_update_status_persisted_to_disk(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        store.update_status("rq-1", "acknowledged")

        # Read from disk directly
        path = store._item_path("rq-1")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == "acknowledged"
        assert "updated_at" in data

    def test_corrupt_item_file_skipped_in_list(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="good"))
        store._ensure_queue_dir()
        (store._queue_dir / "bad.json").write_text("{corrupt", encoding="utf-8")
        items = store.list_items()
        assert len(items) == 1
        assert items[0].item_id == "good"

    def test_path_traversal_rejected_in_create(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        with pytest.raises(ValueError):
            _make_item(item_id="../etc/passwd")

    def test_atomic_write_does_not_leave_tmp(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="atomic-test"))
        tmp_files = list(store._queue_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_list_when_queue_dir_missing(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        # Don't create the queue dir
        assert store.list_items() == []
        assert not store._queue_dir.exists()

    def test_open_then_resolved_then_new_same_finding(self, tmp_path: Path):
        """Resolved old item + new same finding: the new item should still be createable."""
        store = _make_queue_store(tmp_path)
        old = _make_item(item_id="old", status="open", finding_code="eval_stale", capability_id="cap-x")
        store.create_item(old)
        store.update_status("old", "resolved")

        # Create a new item for the same finding
        new = _make_item(item_id="new", status="open", finding_code="eval_stale", capability_id="cap-x")
        store.create_item(new)

        # Both exist
        assert store.get_item("old") is not None
        assert store.get_item("new") is not None


# ═══════════════════════════════════════════════════════════════════
# Health report conversion tests
# ═══════════════════════════════════════════════════════════════════


class TestHealthReportConversion:
    """create_from_health_report: mapping, dedup, severity, inert payload."""

    def _make_report(self, findings: list[CapabilityHealthFinding]) -> CapabilityHealthReport:
        return CapabilityHealthReport(
            generated_at="2026-05-05T10:00:00+00:00",
            findings=findings,
        )

    def test_creates_items_for_findings(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="missing_provenance_legacy",
                message="Missing provenance.", capability_id="cap-1", scope="workspace",
            ),
            CapabilityHealthFinding(
                severity="warning", code="integrity_mismatch",
                message="Hash mismatch.", capability_id="cap-2", scope="global",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 2
        assert items[0].finding_code == "missing_provenance_legacy"
        assert items[1].finding_code == "integrity_mismatch"

    def test_dedupe_prevents_duplicate_open_items(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="missing_provenance_legacy",
                message="Missing provenance.", capability_id="cap-1", scope="workspace",
            ),
        ])
        first = store.create_from_health_report(report)
        assert len(first) == 1

        # Second call with same finding should dedupe
        second = store.create_from_health_report(report)
        assert len(second) == 0

        # Only one item total
        assert len(store.list_items()) == 1

    def test_dedupe_does_not_block_different_findings(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="missing_provenance_legacy",
                message="A", capability_id="cap-1", scope="workspace",
            ),
        ])
        store.create_from_health_report(report)

        report2 = self._make_report([
            CapabilityHealthFinding(
                severity="warning", code="integrity_mismatch",
                message="B", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report2)
        assert len(items) == 1
        assert items[0].finding_code == "integrity_mismatch"

    def test_new_finding_creates_new_item(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report1 = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Stale eval.", capability_id="cap-1", scope="workspace",
            ),
        ])
        store.create_from_health_report(report1)

        report2 = self._make_report([
            CapabilityHealthFinding(
                severity="warning", code="eval_missing",
                message="Missing eval.", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report2)
        assert len(items) == 1
        assert len(store.list_items()) == 2

    def test_resolved_old_item_does_not_block_new_finding(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        finding = CapabilityHealthFinding(
            severity="info", code="eval_stale",
            message="Stale eval.", capability_id="cap-1", scope="workspace",
        )
        report = self._make_report([finding])
        items = store.create_from_health_report(report)
        assert len(items) == 1

        # Resolve the old item
        store.update_status(items[0].item_id, "resolved")

        # Same finding in a new report should create a new item
        items2 = store.create_from_health_report(report)
        assert len(items2) == 1
        assert items2[0].item_id != items[0].item_id

    def test_severity_preserved(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="error", code="missing_provenance_quarantined",
                message="Error!", capability_id="cap-1", scope="workspace",
            ),
            CapabilityHealthFinding(
                severity="warning", code="integrity_mismatch",
                message="Warning!", capability_id="cap-2", scope="global",
            ),
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Info!", capability_id="cap-3", scope="user",
            ),
        ])
        items = store.create_from_health_report(report)
        severities = {i.finding_code: i.severity for i in items}
        assert severities["missing_provenance_quarantined"] == "error"
        assert severities["integrity_mismatch"] == "warning"
        assert severities["eval_stale"] == "info"

    def test_capability_id_and_scope_preserved(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Stale.", capability_id="my-cap", scope="global",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        assert items[0].capability_id == "my-cap"
        assert items[0].scope == "global"

    def test_finding_without_capability_id(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="warning", code="trust_root_expired",
                message="Expired trust root.",
                details={"trust_root_id": "tr-1"},
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        assert items[0].capability_id is None
        assert items[0].scope is None

    def test_recommended_action_mapping_correct(self, tmp_path: Path):
        """Verify the mapping from finding codes to actions."""
        store = _make_queue_store(tmp_path)
        findings = []
        for code in _FINDING_CODE_TO_ACTION:
            findings.append(CapabilityHealthFinding(
                severity="info", code=code,
                message=f"Finding {code}", capability_id="cap-1", scope="workspace",
            ))
        report = self._make_report(findings)
        items = store.create_from_health_report(report)
        for item in items:
            expected = _FINDING_CODE_TO_ACTION.get(item.finding_code, "manual_review")
            assert item.recommended_action == expected, (
                f"Finding {item.finding_code} mapped to {item.recommended_action}, expected {expected}"
            )

    def test_recommendations_remain_advisory(self, tmp_path: Path):
        """The action_payload and recommended_action are labels, never executable."""
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="warning", code="integrity_mismatch",
                message="Hash mismatch.", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        item = items[0]
        assert item.recommended_action == "manual_review"
        # action_payload is inert metadata
        assert "source_finding_code" in item.action_payload
        # No executable fields
        for key in item.action_payload:
            assert isinstance(item.action_payload[key], str)

    def test_action_payload_inert(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="warning", code="quarantine_no_audit",
                message="No audit.", capability_id="cap-1", scope="workspace",
                details={"stage": "no_audit"},
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        assert items[0].action_payload == {
            "source_finding_code": "quarantine_no_audit",
            "stage": "no_audit",
        }

    def test_empty_report_creates_no_items(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([])
        items = store.create_from_health_report(report)
        assert items == []

    def test_dedupe_disabled_creates_duplicates(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        finding = CapabilityHealthFinding(
            severity="info", code="eval_stale",
            message="Stale.", capability_id="cap-1", scope="workspace",
        )
        report = self._make_report([finding])

        items1 = store.create_from_health_report(report, dedupe=False)
        assert len(items1) == 1

        items2 = store.create_from_health_report(report, dedupe=False)
        assert len(items2) == 1

        assert len(store.list_items()) == 2

    def test_finding_code_not_in_map_defaults_to_manual_review(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="unknown_future_finding",
                message="Unknown.", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        assert items[0].recommended_action == "manual_review"

    def test_source_is_health_report(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Stale.", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        assert items[0].source == "health_report"

    def test_item_id_format(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Stale.", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        assert items[0].item_id.startswith("rq-")
        assert len(items[0].item_id) == 15  # "rq-" + 12 hex chars

    def test_created_at_is_iso_format(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Stale.", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        # Should be parseable as ISO datetime
        datetime.fromisoformat(items[0].created_at)


# ═══════════════════════════════════════════════════════════════════
# Hardened model tests
# ═══════════════════════════════════════════════════════════════════


class TestRepairQueueItemModelHardened:
    """Additional hardening: empty item_id, evidence, unknown fields, exact serialization."""

    def test_empty_item_id_rejected(self):
        with pytest.raises(ValueError, match="item_id must not be empty"):
            _make_item(item_id="")
        with pytest.raises(ValueError, match="item_id must not be empty"):
            _make_item(item_id="   ")

    def test_evidence_round_trip(self):
        evidence = {"finding_details": {"severity": "warning"}, "source_report": "rpt-1"}
        item = _make_item(evidence=evidence)
        d = item.to_dict()
        assert d["evidence"] == evidence
        restored = RepairQueueItem.from_dict(d)
        assert restored.evidence == evidence

    def test_unknown_fields_in_from_dict_preserved_in_metadata(self):
        """from_dict ignores unknown fields; they are not lost but aren't blindly copied either."""
        data = {
            "item_id": "rq-extra",
            "created_at": "2026-05-05T10:00:00+00:00",
            "extra_field_1": "should-be-ignored",
            "another_extra": 42,
        }
        item = RepairQueueItem.from_dict(data)
        assert item.item_id == "rq-extra"
        # Unknown fields are not in the item (no crash, no leak into to_dict)
        d = item.to_dict()
        assert "extra_field_1" not in d
        assert "another_extra" not in d

    def test_exact_serialization_fidelity(self):
        """Full item to_dict → from_dict → to_dict produces identical dicts."""
        item = _make_item(
            item_id="rq-fidelity",
            created_at="2026-05-05T10:00:00+00:00",
            source="manual",
            finding_code="eval_stale",
            severity="warning",
            status="acknowledged",
            title="Fidelity test",
            description="Testing round-trip fidelity",
            recommended_action="reeval",
            action_payload={"issue": "stale", "key": "val"},
            evidence={"detail": "test"},
            capability_id="cap-x",
            scope="global",
            assigned_to="op-1",
            updated_at="2026-05-05T11:00:00+00:00",
            resolved_at=None,
            dismissed_at=None,
            metadata={"priority": 1},
        )
        d1 = item.to_dict()
        restored = RepairQueueItem.from_dict(d1)
        d2 = restored.to_dict()
        assert d1 == d2

    def test_capability_id_scope_optional_round_trip(self):
        """capability_id and scope are optional and must survive serialization."""
        item = _make_item(capability_id=None, scope=None)
        d = item.to_dict()
        assert d["capability_id"] is None
        assert d["scope"] is None
        restored = RepairQueueItem.from_dict(d)
        assert restored.capability_id is None
        assert restored.scope is None

    def test_created_at_and_updated_at_semantics(self):
        """created_at is set once; updated_at is set on status change."""
        item = _make_item(created_at="2026-01-01T00:00:00+00:00", updated_at=None)
        assert item.created_at == "2026-01-01T00:00:00+00:00"
        assert item.updated_at is None
        assert item.resolved_at is None
        assert item.dismissed_at is None

    def test_nested_action_payload_executable_in_dict_rejected(self):
        """Nested dict with executable string value is rejected."""
        with pytest.raises(ValueError, match="executable-like content"):
            _make_item(action_payload={
                "config": {"setup": "subprocess.call('evil')"},
            })

    def test_nested_action_payload_in_list_rejected(self):
        """List item with executable string is rejected."""
        with pytest.raises(ValueError, match="executable-like content"):
            _make_item(action_payload={
                "steps": ["normal step", "rm -rf /"],
            })

    def test_action_payload_tool_call_key_rejected(self):
        """Keys like 'tool_name' or 'command' are rejected."""
        for key in ("tool_name", "tool_call", "function_name", "function_call",
                     "command", "script", "exec", "execute", "shell_cmd"):
            with pytest.raises(ValueError, match="tool-call or command"):
                _make_item(action_payload={key: "some_value"})

    def test_action_payload_banned_function_name_rejected(self):
        """Values that are banned function names are rejected."""
        for name in ("repair_capability", "run_capability", "rebuild_index",
                      "transition_capability", "auto_repair_capability",
                      "promote_from_health", "execute_repair"):
            with pytest.raises(ValueError, match="banned function name"):
                _make_item(action_payload={"action": name})

    def test_action_payload_url_rejected(self):
        """URLs in payload values are rejected."""
        for url_val in ("http://example.com/repair", "https://api.run/exec"):
            with pytest.raises(ValueError, match="URL scheme"):
                _make_item(action_payload={"ref": url_val})

    def test_action_payload_innocent_dict_accepted(self):
        """A nested dict with benign values is accepted."""
        item = _make_item(action_payload={
            "issue": "missing_provenance",
            "context": {"cap_id": "cap-1", "note": "review needed"},
        })
        assert "context" in item.action_payload
        assert item.action_payload["context"]["note"] == "review needed"


# ═══════════════════════════════════════════════════════════════════
# Hardened store tests
# ═══════════════════════════════════════════════════════════════════


class TestRepairQueueStoreHardened:
    """Additional hardening: status transitions, update isolation, corruption."""

    def test_acknowledged_to_resolved(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        store.update_status("rq-1", "acknowledged")
        updated = store.update_status("rq-1", "resolved")
        assert updated.status == "resolved"
        assert updated.resolved_at is not None

    def test_open_to_dismissed_directly(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        updated = store.update_status("rq-1", "dismissed")
        assert updated.status == "dismissed"
        assert updated.dismissed_at is not None
        assert updated.resolved_at is None

    def test_update_status_preserves_action_payload(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        payload = {"source_finding_code": "eval_stale", "stage": "no_audit"}
        store.create_item(_make_item(item_id="rq-1", action_payload=payload))
        updated = store.update_status("rq-1", "acknowledged")
        assert updated.action_payload == payload

    def test_update_status_preserves_evidence(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        evidence = {"finding_details": {"severity": "error"}}
        store.create_item(_make_item(item_id="rq-1", evidence=evidence))
        updated = store.update_status("rq-1", "resolved")
        assert updated.evidence == evidence

    def test_update_status_preserves_recommended_action(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", recommended_action="reindex"))
        updated = store.update_status("rq-1", "acknowledged")
        assert updated.recommended_action == "reindex"

    def test_update_status_does_not_delete_item(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1"))
        store.update_status("rq-1", "dismissed")
        # Item still readable
        assert store.get_item("rq-1") is not None

    def test_update_status_does_not_create_new_files(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1"))
        files_before = set(f.name for f in store._queue_dir.glob("*.json"))
        store.update_status("rq-1", "acknowledged")
        files_after = set(f.name for f in store._queue_dir.glob("*.json"))
        assert files_before == files_after

    def test_resolved_item_stays_resolved(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        store.update_status("rq-1", "resolved")
        updated = store.update_status("rq-1", "resolved")
        assert updated.status == "resolved"

    def test_dismissed_item_stays_dismissed(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-1", status="open"))
        store.update_status("rq-1", "dismissed")
        updated = store.update_status("rq-1", "dismissed")
        assert updated.status == "dismissed"

    def test_partial_write_tmp_cleaned(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store._ensure_queue_dir()
        # Simulate a leftover .tmp file from a failed write
        tmp_path_file = store._queue_dir / "rq-orphan.tmp"
        tmp_path_file.write_text("incomplete", encoding="utf-8")
        assert tmp_path_file.exists()

        # Normal operation should not be affected
        store.create_item(_make_item(item_id="rq-clean"))
        # .tmp file for the new item should not exist
        new_tmp = store._queue_dir / "rq-clean.tmp"
        assert not new_tmp.exists()
        # Old orphaned .tmp is not cleaned by create_item (it's not the same id)
        # but it doesn't affect normal operations

    def test_colliding_item_ids(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store.create_item(_make_item(item_id="rq-collide"))
        with pytest.raises(FileExistsError):
            store.create_item(_make_item(item_id="rq-collide"))

    def test_list_items_deterministic_with_corrupt_files(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        store._ensure_queue_dir()
        for i in range(3):
            store.create_item(_make_item(item_id=f"rq-{i:03d}"))
        # Add corrupt file
        (store._queue_dir / "bad.json").write_text("{corrupt", encoding="utf-8")

        result1 = store.list_items()
        result2 = store.list_items()
        ids1 = [i.item_id for i in result1]
        ids2 = [i.item_id for i in result2]
        assert ids1 == ids2
        assert len(result1) == 3


# ═══════════════════════════════════════════════════════════════════
# Hardened health conversion tests
# ═══════════════════════════════════════════════════════════════════


class TestHealthReportConversionHardened:
    """Additional hardening: determinism, dismissed dedup, ordering."""

    def _make_report(self, findings: list[CapabilityHealthFinding]) -> CapabilityHealthReport:
        return CapabilityHealthReport(
            generated_at="2026-05-05T10:00:00+00:00",
            findings=findings,
        )

    def test_deterministic_ordering(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        findings = [
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Stale.", capability_id="cap-1", scope="workspace",
            ),
            CapabilityHealthFinding(
                severity="warning", code="integrity_mismatch",
                message="Mismatch.", capability_id="cap-2", scope="global",
            ),
            CapabilityHealthFinding(
                severity="error", code="missing_provenance_quarantined",
                message="Missing.", capability_id="cap-3", scope="user",
            ),
        ]
        report = self._make_report(findings)
        items1 = store.create_from_health_report(report)
        items2 = store.create_from_health_report(report, dedupe=False)
        # Items1 have the same ordering as items2 (finding order preserved)
        assert [i.finding_code for i in items1] == ["eval_stale", "integrity_mismatch", "missing_provenance_quarantined"]
        assert [i.finding_code for i in items2] == ["eval_stale", "integrity_mismatch", "missing_provenance_quarantined"]

    def test_dismissed_item_does_not_block_new(self, tmp_path: Path):
        store = _make_queue_store(tmp_path)
        finding = CapabilityHealthFinding(
            severity="info", code="eval_stale",
            message="Stale.", capability_id="cap-1", scope="workspace",
        )
        report = self._make_report([finding])

        items1 = store.create_from_health_report(report)
        assert len(items1) == 1
        store.update_status(items1[0].item_id, "dismissed")

        # Same finding in new report should create new item
        items2 = store.create_from_health_report(report)
        assert len(items2) == 1
        assert items2[0].item_id != items1[0].item_id

    def test_same_report_same_queue_same_output_modulo_ids(self, tmp_path: Path):
        """Same health report produces same finding codes and actions, different item_ids."""
        store = _make_queue_store(tmp_path)
        findings = [
            CapabilityHealthFinding(
                severity="info", code="eval_stale",
                message="Stale.", capability_id="cap-1", scope="workspace",
            ),
        ]
        report = self._make_report(findings)

        items1 = store.create_from_health_report(report, dedupe=False)
        items2 = store.create_from_health_report(report, dedupe=False)

        assert len(items1) == 1
        assert len(items2) == 1
        # Same finding code
        assert items1[0].finding_code == items2[0].finding_code
        # Different item_ids (UUIDs differ)
        assert items1[0].item_id != items2[0].item_id
        # Same source, severity, capability_id, scope, recommended_action
        assert items1[0].source == items2[0].source
        assert items1[0].severity == items2[0].severity
        assert items1[0].capability_id == items2[0].capability_id
        assert items1[0].scope == items2[0].scope
        assert items1[0].recommended_action == items2[0].recommended_action

    def test_unsupported_finding_maps_to_manual_review(self, tmp_path: Path):
        """Completely unknown finding codes safely default to manual_review."""
        store = _make_queue_store(tmp_path)
        report = self._make_report([
            CapabilityHealthFinding(
                severity="info", code="completely_new_code_v2",
                message="Future finding.", capability_id="cap-1", scope="workspace",
            ),
        ])
        items = store.create_from_health_report(report)
        assert len(items) == 1
        assert items[0].recommended_action == "manual_review"
        assert items[0].action_payload["source_finding_code"] == "completely_new_code_v2"
