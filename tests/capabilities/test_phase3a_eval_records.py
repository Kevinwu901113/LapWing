"""Phase 3A tests: Eval record persistence — write, read, list, latest."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.capabilities.document import parse_capability
from src.capabilities.eval_records import (
    EVALS_DIR,
    get_latest_eval_record,
    list_eval_records,
    read_eval_record,
    write_eval_record,
)
from src.capabilities.evaluator import (
    CapabilityEvaluator,
    EvalFinding,
    EvalRecord,
    FindingSeverity,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_doc(base: Path, dirname: str = "eval_test_cap") -> Path:
    cap_dir = base / dirname
    cap_dir.mkdir(parents=True)

    fm = {
        "id": "eval_test_01",
        "name": "Eval Test",
        "description": "Testing eval record persistence.",
        "type": "skill",
        "scope": "workspace",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "active",
        "risk_level": "low",
        "tags": ["test"],
        "triggers": ["on_test"],
    }
    body = """## When to use
Testing eval records.

## Procedure
1. Write an eval record.
2. Read it back.

## Verification
Check that the record is correct.

## Failure handling
If the record is wrong, investigate.
"""
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False)
    md = f"---\n{fm_yaml}---\n\n{body}"
    (cap_dir / "CAPABILITY.md").write_text(md, encoding="utf-8")

    for d in ("scripts", "tests", "examples", "evals"):
        (cap_dir / d).mkdir(exist_ok=True)

    return parse_capability(cap_dir)


def _make_record(cap_id: str = "eval_test_01", scope: str = "workspace") -> EvalRecord:
    return EvalRecord(
        capability_id=cap_id,
        scope=scope,
        content_hash="abc123def456",
        passed=True,
        score=0.95,
        findings=[
            EvalFinding(severity=FindingSeverity.INFO, code="test_finding", message="A test finding"),
        ],
    )


@pytest.fixture
def doc(tmp_path):
    return _make_doc(tmp_path)


# ── Write evals ─────────────────────────────────────────────────────────


class TestWriteEvalRecord:
    def test_writes_to_evals_dir(self, doc):
        record = _make_record()
        filepath = write_eval_record(record, doc)
        assert filepath.parent.name == EVALS_DIR
        assert filepath.exists()

    def test_written_file_is_valid_json(self, doc):
        record = _make_record()
        filepath = write_eval_record(record, doc)
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["capability_id"] == "eval_test_01"
        assert data["passed"] is True

    def test_written_file_has_content_hash(self, doc):
        record = _make_record()
        filepath = write_eval_record(record, doc)
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["content_hash"] == "abc123def456"

    def test_written_file_has_findings(self, doc):
        record = _make_record()
        filepath = write_eval_record(record, doc)
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert len(data["findings"]) == 1
        assert data["findings"][0]["code"] == "test_finding"

    def test_write_does_not_mutate_manifest(self, doc):
        record = _make_record()
        old_maturity = doc.manifest.maturity
        old_status = doc.manifest.status
        write_eval_record(record, doc)
        # Re-parse to verify no mutation
        doc2 = parse_capability(doc.directory)
        assert doc2.manifest.maturity == old_maturity
        assert doc2.manifest.status == old_status

    def test_write_with_mutation_log_none_no_error(self, doc):
        record = _make_record()
        filepath = write_eval_record(record, doc, mutation_log=None)
        assert filepath.exists()

    def test_write_with_mutation_log_calls_record(self, doc):
        mock_log = MagicMock()
        mock_log.record = MagicMock()
        record = _make_record()
        write_eval_record(record, doc, mutation_log=mock_log)
        mock_log.record.assert_called()

    def test_multiple_writes(self, doc):
        record1 = _make_record()
        record2 = _make_record()
        record2.created_at = "2026-04-30T10:00:01.000000"
        write_eval_record(record1, doc)
        write_eval_record(record2, doc)
        records = list_eval_records(doc)
        assert len(records) == 2


# ── Read evals ──────────────────────────────────────────────────────────


class TestReadEvalRecord:
    def test_read_existing_record(self, doc):
        record = _make_record()
        write_eval_record(record, doc)
        read = read_eval_record(doc, record.created_at)
        assert read is not None
        assert read.capability_id == record.capability_id
        assert read.passed == record.passed

    def test_read_nonexistent_record(self, doc):
        result = read_eval_record(doc, "2025-01-01T00:00:00.000000")
        assert result is None

    def test_findings_round_trip(self, doc):
        record = _make_record()
        record.findings = [
            EvalFinding(severity=FindingSeverity.ERROR, code="err1", message="Error 1", details={"key": "val"}),
            EvalFinding(severity=FindingSeverity.WARNING, code="warn1", message="Warning 1", location="somewhere"),
            EvalFinding(severity=FindingSeverity.INFO, code="info1", message="Info 1"),
        ]
        write_eval_record(record, doc)
        read = read_eval_record(doc, record.created_at)
        assert read is not None
        assert len(read.findings) == 3
        assert read.findings[0].code == "err1"
        assert read.findings[0].details == {"key": "val"}
        assert read.findings[1].location == "somewhere"


# ── List evals ──────────────────────────────────────────────────────────


class TestListEvalRecords:
    def test_list_empty(self, doc):
        records = list_eval_records(doc)
        assert records == []

    def test_list_sorted_descending(self, doc):
        r1 = _make_record()
        r1.created_at = "2026-04-30T08:00:00.000000"
        r2 = _make_record()
        r2.created_at = "2026-04-30T09:00:00.000000"
        write_eval_record(r1, doc)
        write_eval_record(r2, doc)
        records = list_eval_records(doc)
        assert len(records) == 2
        assert records[0].created_at == "2026-04-30T09:00:00.000000"
        assert records[1].created_at == "2026-04-30T08:00:00.000000"


# ── Latest eval ─────────────────────────────────────────────────────────


class TestGetLatestEvalRecord:
    def test_latest_empty(self, doc):
        result = get_latest_eval_record(doc)
        assert result is None

    def test_latest_returns_most_recent(self, doc):
        r1 = _make_record()
        r1.created_at = "2026-04-30T08:00:00.000000"
        r2 = _make_record()
        r2.created_at = "2026-04-30T09:00:00.000000"
        write_eval_record(r1, doc)
        write_eval_record(r2, doc)
        latest = get_latest_eval_record(doc)
        assert latest is not None
        assert latest.created_at == "2026-04-30T09:00:00.000000"
