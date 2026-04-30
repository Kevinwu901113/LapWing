"""Phase 2A tests: CapabilityIndex SQLite-backed lookup."""

from __future__ import annotations

import pytest

from src.capabilities.index import CapabilityIndex
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)
from src.capabilities.document import CapabilityDocument
from pathlib import Path


def _make_doc(
    cap_id: str = "test_001",
    name: str = "Test Capability",
    description: str = "A test capability.",
    type: CapabilityType = CapabilityType.SKILL,
    scope: CapabilityScope = CapabilityScope.WORKSPACE,
    maturity: CapabilityMaturity = CapabilityMaturity.DRAFT,
    status: CapabilityStatus = CapabilityStatus.ACTIVE,
    risk_level: CapabilityRiskLevel = CapabilityRiskLevel.LOW,
    tags: list | None = None,
    triggers: list | None = None,
    body: str = "Body content.",
    directory: Path | None = None,
    **overrides,
) -> CapabilityDocument:
    if directory is None:
        directory = Path(f"/tmp/test/{cap_id}")
    m = CapabilityManifest(
        id=cap_id,
        name=name,
        description=description,
        type=type,
        scope=scope,
        version="0.1.0",
        maturity=maturity,
        status=status,
        risk_level=risk_level,
        tags=tags or [],
        triggers=triggers or [],
    )
    return CapabilityDocument(manifest=m, body=body, directory=directory)


def _make_index(tmp_path: Path) -> CapabilityIndex:
    idx = CapabilityIndex(tmp_path / "index.db")
    idx.init()
    return idx


# ── Init and schema ───────────────────────────────────────────

class TestInitAndSchema:
    def test_init_creates_db_file(self, tmp_path):
        idx = _make_index(tmp_path)
        assert (tmp_path / "index.db").exists()
        idx.close()

    def test_init_idempotent(self, tmp_path):
        idx = _make_index(tmp_path)
        idx.init()
        idx.init()  # second call should not crash
        assert idx.count() == 0
        idx.close()

    def test_close_and_reopen_preserves_data(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(directory=tmp_path / "cap")
        idx.upsert(doc)
        assert idx.count() == 1
        idx.close()

        idx2 = _make_index(tmp_path)
        assert idx2.count() == 1
        idx2.close()


# ── upsert ────────────────────────────────────────────────────

class TestUpsert:
    def test_insert_new(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(directory=tmp_path / "cap")
        idx.upsert(doc)
        assert idx.count() == 1
        idx.close()

    def test_update_existing(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(name="Original", directory=tmp_path / "cap")
        idx.upsert(doc)
        assert idx.count() == 1

        doc2 = _make_doc(name="Updated", directory=tmp_path / "cap")
        idx.upsert(doc2)
        assert idx.count() == 1

        result = idx.get("test_001", "workspace")
        assert result["name"] == "Updated"
        idx.close()

    def test_multiple_scopes(self, tmp_path):
        idx = _make_index(tmp_path)
        doc1 = _make_doc(cap_id="a", scope=CapabilityScope.WORKSPACE, directory=tmp_path / "cap1")
        doc2 = _make_doc(cap_id="a", scope=CapabilityScope.GLOBAL, directory=tmp_path / "cap2")
        idx.upsert(doc1)
        idx.upsert(doc2)
        assert idx.count() == 2
        idx.close()

    def test_body_content_preserved(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(body="A" * 1000, directory=tmp_path / "cap")
        idx.upsert(doc)
        result = idx.get("test_001", "workspace")
        assert result is not None
        idx.close()


# ── remove ────────────────────────────────────────────────────

class TestRemove:
    def test_remove_existing(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(directory=tmp_path / "cap")
        idx.upsert(doc)
        assert idx.count() == 1
        idx.remove("test_001", "workspace")
        assert idx.count() == 0
        idx.close()

    def test_remove_nonexistent_no_error(self, tmp_path):
        idx = _make_index(tmp_path)
        idx.remove("nope", "workspace")  # should not raise
        idx.close()

    def test_remove_only_affects_target(self, tmp_path):
        idx = _make_index(tmp_path)
        doc1 = _make_doc(cap_id="a", scope=CapabilityScope.WORKSPACE, directory=tmp_path / "cap1")
        doc2 = _make_doc(cap_id="b", scope=CapabilityScope.WORKSPACE, directory=tmp_path / "cap2")
        idx.upsert(doc1)
        idx.upsert(doc2)
        idx.remove("a", "workspace")
        assert idx.count() == 1
        assert idx.get("b", "workspace") is not None
        idx.close()


# ── mark_disabled / mark_archived ─────────────────────────────

class TestMarkDisabled:
    def test_mark_disabled(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(status=CapabilityStatus.ACTIVE, directory=tmp_path / "cap")
        idx.upsert(doc)
        idx.mark_disabled("test_001", "workspace")
        result = idx.get("test_001", "workspace")
        assert result["status"] == "disabled"
        idx.close()


class TestMarkArchived:
    def test_mark_archived(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(status=CapabilityStatus.ACTIVE, directory=tmp_path / "cap")
        idx.upsert(doc)
        idx.mark_archived("test_001", "workspace")
        result = idx.get("test_001", "workspace")
        assert result["status"] == "archived"
        idx.close()


# ── get ───────────────────────────────────────────────────────

class TestGet:
    def test_get_existing(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(directory=tmp_path / "cap")
        idx.upsert(doc)
        result = idx.get("test_001", "workspace")
        assert result is not None
        assert result["id"] == "test_001"
        assert result["scope"] == "workspace"
        idx.close()

    def test_get_nonexistent_returns_none(self, tmp_path):
        idx = _make_index(tmp_path)
        assert idx.get("nope", "workspace") is None
        idx.close()


# ── search ────────────────────────────────────────────────────

class TestSearch:
    @pytest.fixture
    def populated(self, tmp_path):
        idx = _make_index(tmp_path)
        docs = [
            _make_doc(cap_id="a", name="Python HTTP Client", description="Makes HTTP requests",
                      tags=["python", "web"], triggers=["on_http_request"], directory=tmp_path / "cap_a"),
            _make_doc(cap_id="b", name="Rust CLI Tool", description="Command-line utility",
                      tags=["rust", "cli"], triggers=["on_cli"], directory=tmp_path / "cap_b"),
            _make_doc(cap_id="c", name="Python Data Analyzer", description="Analyzes data",
                      tags=["python", "data"], maturity=CapabilityMaturity.STABLE,
                      directory=tmp_path / "cap_c"),
        ]
        for d in docs:
            idx.upsert(d)
        yield idx
        idx.close()

    def test_search_by_name(self, populated):
        results = populated.search("python")
        ids = {r["id"] for r in results}
        assert "a" in ids
        assert "c" in ids

    def test_search_by_description(self, populated):
        results = populated.search("HTTP")
        ids = {r["id"] for r in results}
        assert "a" in ids

    def test_search_by_tag(self, populated):
        results = populated.search("cli")
        ids = {r["id"] for r in results}
        assert "b" in ids

    def test_search_no_match(self, populated):
        results = populated.search("nonexistent_xyz")
        assert len(results) == 0

    def test_filter_by_scope(self, populated):
        results = populated.search(filters={"scope": "workspace"})
        assert len(results) == 3  # all in workspace

    def test_filter_by_maturity(self, populated):
        results = populated.search(filters={"maturity": "stable"})
        ids = {r["id"] for r in results}
        assert ids == {"c"}

    def test_filter_by_status(self, populated):
        results = populated.search(filters={"status": "active"})
        assert len(results) == 3

    def test_filter_by_type(self, populated):
        results = populated.search(filters={"type": "skill"})
        assert len(results) == 3

    def test_filter_by_risk_level(self, populated):
        results = populated.search(filters={"risk_level": "low"})
        assert len(results) == 3

    def test_combined_filters(self, populated):
        results = populated.search("python", filters={"maturity": "draft"})
        ids = {r["id"] for r in results}
        assert ids == {"a"}

    def test_limit(self, populated):
        results = populated.search(limit=1)
        assert len(results) <= 1

    def test_disabled_excluded_by_default(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(cap_id="d", status=CapabilityStatus.DISABLED, directory=tmp_path / "cap_d")
        idx.upsert(doc)
        results = idx.search()
        ids = {r["id"] for r in results}
        assert "d" not in ids
        idx.close()

    def test_archived_excluded_by_default(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(cap_id="e", status=CapabilityStatus.ARCHIVED, directory=tmp_path / "cap_e")
        idx.upsert(doc)
        results = idx.search()
        ids = {r["id"] for r in results}
        assert "e" not in ids
        idx.close()

    def test_disabled_included_with_filter(self, tmp_path):
        idx = _make_index(tmp_path)
        doc = _make_doc(cap_id="d", status=CapabilityStatus.DISABLED, directory=tmp_path / "cap_d")
        idx.upsert(doc)
        results = idx.search(filters={"status": "disabled"})
        ids = {r["id"] for r in results}
        assert "d" in ids
        idx.close()


# ── resolve_with_precedence ───────────────────────────────────

class TestResolveWithPrecedence:
    def test_highest_precedence_wins(self, tmp_path):
        idx = _make_index(tmp_path)
        doc_ws = _make_doc(cap_id="dup", scope=CapabilityScope.WORKSPACE, name="WS",
                           directory=tmp_path / "ws")
        doc_gl = _make_doc(cap_id="dup", scope=CapabilityScope.GLOBAL, name="GL",
                           directory=tmp_path / "gl")
        idx.upsert(doc_ws)
        idx.upsert(doc_gl)
        result = idx.resolve_with_precedence("dup")
        assert result["scope"] == "workspace"  # workspace > global
        idx.close()

    def test_session_beats_all(self, tmp_path):
        idx = _make_index(tmp_path)
        for s in CapabilityScope:
            doc = _make_doc(cap_id="best", scope=s, name=s.value, directory=tmp_path / s.value)
            idx.upsert(doc)
        result = idx.resolve_with_precedence("best")
        assert result["scope"] == "session"
        idx.close()

    def test_excludes_archived_by_default(self, tmp_path):
        idx = _make_index(tmp_path)
        doc_arch = _make_doc(cap_id="arc", scope=CapabilityScope.SESSION,
                             status=CapabilityStatus.ARCHIVED, directory=tmp_path / "arc")
        doc_act = _make_doc(cap_id="arc", scope=CapabilityScope.GLOBAL,
                            status=CapabilityStatus.ACTIVE, directory=tmp_path / "act")
        idx.upsert(doc_arch)
        idx.upsert(doc_act)
        result = idx.resolve_with_precedence("arc")
        assert result["scope"] == "global"  # archived session is excluded
        idx.close()

    def test_includes_archived_with_flag(self, tmp_path):
        idx = _make_index(tmp_path)
        doc_arch = _make_doc(cap_id="arc", scope=CapabilityScope.SESSION,
                             status=CapabilityStatus.ARCHIVED, directory=tmp_path / "arc")
        idx.upsert(doc_arch)
        result = idx.resolve_with_precedence("arc", include_archived=True)
        assert result is not None
        assert result["scope"] == "session"
        idx.close()

    def test_nonexistent_returns_none(self, tmp_path):
        idx = _make_index(tmp_path)
        assert idx.resolve_with_precedence("nope") is None
        idx.close()


# ── count ─────────────────────────────────────────────────────

class TestCount:
    def test_count_all(self, tmp_path):
        idx = _make_index(tmp_path)
        assert idx.count() == 0
        doc = _make_doc(directory=tmp_path / "cap")
        idx.upsert(doc)
        assert idx.count() == 1
        idx.close()

    def test_count_by_scope(self, tmp_path):
        idx = _make_index(tmp_path)
        doc1 = _make_doc(cap_id="a", scope=CapabilityScope.WORKSPACE, directory=tmp_path / "cap1")
        doc2 = _make_doc(cap_id="b", scope=CapabilityScope.GLOBAL, directory=tmp_path / "cap2")
        idx.upsert(doc1)
        idx.upsert(doc2)
        assert idx.count(scope="workspace") == 1
        assert idx.count(scope="global") == 1
        idx.close()
