"""Phase 2A tests: CapabilityStore CRUD, lifecycle, listing, mutation log integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.capabilities.document import STANDARD_DIRS, CapabilityDocument
from src.capabilities.errors import CapabilityError, InvalidDocumentError
from src.capabilities.schema import CapabilityScope, CapabilityStatus, CapabilityType
from src.capabilities.store import CapabilityStore
from src.capabilities.index import CapabilityIndex


def _make_store(tmp_path: Path, *, with_index: bool = False) -> CapabilityStore:
    kwargs = {}
    if with_index:
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        kwargs["index"] = idx
    return CapabilityStore(data_dir=tmp_path / "capabilities", **kwargs)


def _create_doc(store: CapabilityStore, scope=CapabilityScope.WORKSPACE, **overrides) -> CapabilityDocument:
    return store.create_draft(
        scope=scope,
        name=overrides.pop("name", "Test Capability"),
        description=overrides.pop("description", "A test capability."),
        **overrides,
    )


# ── create_draft ──────────────────────────────────────────────

class TestCreateDraft:
    @pytest.fixture
    def store(self, tmp_path):
        return _make_store(tmp_path)

    def test_creates_directory_layout(self, store):
        doc = _create_doc(store, cap_id="my_cap")
        cap_dir = store._get_dir("my_cap", CapabilityScope.WORKSPACE)
        assert cap_dir.is_dir()
        assert (cap_dir / "CAPABILITY.md").exists()
        assert (cap_dir / "manifest.json").exists()

    def test_creates_standard_subdirs(self, store):
        doc = _create_doc(store, cap_id="my_cap")
        cap_dir = store._get_dir("my_cap", CapabilityScope.WORKSPACE)
        for sd in STANDARD_DIRS:
            assert (cap_dir / sd).is_dir(), f"Missing standard dir: {sd}"

    def test_writes_valid_capability_md(self, store):
        doc = _create_doc(store, cap_id="my_cap", body="# Hello World")
        cap_dir = store._get_dir("my_cap", CapabilityScope.WORKSPACE)
        content = (cap_dir / "CAPABILITY.md").read_text(encoding="utf-8")
        assert "---" in content
        assert "id: my_cap" in content
        assert "# Hello World" in content

    def test_writes_manifest_json(self, store):
        doc = _create_doc(store, cap_id="my_cap")
        cap_dir = store._get_dir("my_cap", CapabilityScope.WORKSPACE)
        manifest = json.loads((cap_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["id"] == "my_cap"
        assert manifest["status"] == "active"
        assert manifest["maturity"] == "draft"

    def test_computes_stable_content_hash(self, store):
        doc = _create_doc(store, cap_id="hash_test")
        assert doc.content_hash is not None
        assert len(doc.content_hash) == 64

    def test_hash_stable_across_reads(self, store):
        doc1 = _create_doc(store, cap_id="hash_stable")
        doc2 = store.get("hash_stable", CapabilityScope.WORKSPACE)
        assert doc1.content_hash == doc2.content_hash

    def test_duplicate_id_same_scope_raises(self, store):
        _create_doc(store, cap_id="dup")
        with pytest.raises(FileExistsError):
            _create_doc(store, cap_id="dup")

    def test_same_id_different_scopes_allowed(self, store):
        doc1 = _create_doc(store, cap_id="same_id", scope=CapabilityScope.WORKSPACE)
        doc2 = _create_doc(store, cap_id="same_id", scope=CapabilityScope.GLOBAL)
        assert doc1.directory != doc2.directory

    def test_auto_generates_id(self, store):
        doc = _create_doc(store)  # no cap_id
        assert doc.id is not None
        assert doc.id.startswith("workspace_")

    def test_default_status_active(self, store):
        doc = _create_doc(store, cap_id="status_test")
        assert doc.manifest.status == CapabilityStatus.ACTIVE

    def test_default_maturity_draft(self, store):
        doc = _create_doc(store, cap_id="mat_test")
        assert doc.manifest.maturity.value == "draft"

    def test_returns_capability_document(self, store):
        doc = _create_doc(store, cap_id="rt")
        assert isinstance(doc, CapabilityDocument)
        assert doc.name == "Test Capability"
        assert doc.type == CapabilityType.SKILL

    def test_custom_type(self, store):
        doc = _create_doc(store, cap_id="ct", type="workflow")
        assert doc.type == CapabilityType.WORKFLOW

    def test_custom_tags_and_triggers(self, store):
        doc = _create_doc(store, cap_id="tags_test", tags=["python", "web"], triggers=["on_push"])
        assert set(doc.manifest.tags) == {"python", "web"}
        assert doc.manifest.triggers == ["on_push"]

    def test_body_written_correctly(self, store):
        doc = _create_doc(store, cap_id="body_test", body="# Section\n\nContent.")
        assert "# Section" in doc.body

    def test_creates_across_all_scopes(self, store):
        for scope in CapabilityScope:
            doc = _create_doc(store, scope=scope)
            assert doc.scope == scope


# ── get ───────────────────────────────────────────────────────

class TestGet:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path)
        _create_doc(s, cap_id="get_me", name="Get Me")
        _create_doc(s, cap_id="global_only", scope=CapabilityScope.GLOBAL, name="Global Only")
        _create_doc(s, cap_id="ws_only", scope=CapabilityScope.WORKSPACE, name="WS Only")
        # Same id in two scopes - workspace should win over global
        _create_doc(s, cap_id="dupe_id", scope=CapabilityScope.GLOBAL, name="Global Dupe")
        _create_doc(s, cap_id="dupe_id", scope=CapabilityScope.WORKSPACE, name="WS Dupe")
        return s

    def test_get_with_explicit_scope(self, store):
        doc = store.get("get_me", CapabilityScope.WORKSPACE)
        assert doc.name == "Get Me"

    def test_get_nonexistent_raises(self, store):
        with pytest.raises(CapabilityError):
            store.get("nope", CapabilityScope.WORKSPACE)

    def test_get_without_scope_uses_precedence(self, store):
        doc = store.get("dupe_id")
        assert doc.name == "WS Dupe"
        assert doc.scope == CapabilityScope.WORKSPACE

    def test_get_falls_back_to_lower_scope(self, store):
        doc = store.get("global_only")
        assert doc.name == "Global Only"
        assert doc.scope == CapabilityScope.GLOBAL

    def test_get_nonexistent_without_scope_raises(self, store):
        with pytest.raises(CapabilityError):
            store.get("does_not_exist_anywhere")


# ── list ──────────────────────────────────────────────────────

class TestList:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path)
        _create_doc(s, cap_id="a", name="Alpha", tags=["python"])
        _create_doc(s, cap_id="b", name="Beta", type="workflow")
        _create_doc(s, cap_id="c", name="Gamma", scope=CapabilityScope.GLOBAL)
        return s

    def test_list_all_active(self, store):
        results = store.list()
        assert len(results) == 3

    def test_list_by_scope(self, store):
        results = store.list(scope=CapabilityScope.GLOBAL)
        assert len(results) == 1
        assert results[0].id == "c"

    def test_list_by_type(self, store):
        results = store.list(type="workflow")
        assert len(results) == 1
        assert results[0].id == "b"

    def test_list_by_tags(self, store):
        results = store.list(tags=["python"])
        assert len(results) == 1
        assert results[0].id == "a"

    def test_list_excludes_disabled_by_default(self, store):
        store.disable("a", CapabilityScope.WORKSPACE)
        results = store.list()
        ids = {d.id for d in results}
        assert "a" not in ids

    def test_list_includes_disabled_with_flag(self, store):
        store.disable("a", CapabilityScope.WORKSPACE)
        results = store.list(include_disabled=True)
        ids = {d.id for d in results}
        assert "a" in ids

    def test_list_excludes_archived_by_default(self, store):
        store.archive("a", CapabilityScope.WORKSPACE)
        results = store.list()
        ids = {d.id for d in results}
        assert "a" not in ids

    def test_list_includes_archived_with_flag(self, store):
        store.archive("a", CapabilityScope.WORKSPACE)
        results = store.list(include_archived=True)
        ids = {d.id for d in results}
        assert "a" in ids

    def test_list_empty_store(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.list() == []

    def test_list_respects_limit(self, store):
        results = store.list(limit=1)
        assert len(results) == 1


# ── search ────────────────────────────────────────────────────

class TestSearch:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path, with_index=True)
        _create_doc(s, cap_id="a", name="Python HTTP Client", description="Makes HTTP requests",
                    tags=["python", "web"], triggers=["on_http"])
        _create_doc(s, cap_id="b", name="Rust CLI Tool", description="Command-line utility",
                    tags=["rust", "cli"], triggers=["on_cli"])
        _create_doc(s, cap_id="c", name="Python Data Tool", description="Analyzes data",
                    tags=["python", "data"])
        return s

    def test_search_by_name(self, store):
        results = store.search("python")
        ids = {d.id for d in results}
        assert ids == {"a", "c"}

    def test_search_by_description(self, store):
        results = store.search("HTTP")
        ids = {d.id for d in results}
        assert ids == {"a"}

    def test_search_by_trigger(self, store):
        results = store.search("on_cli")
        ids = {d.id for d in results}
        assert ids == {"b"}

    def test_search_by_tag(self, store):
        results = store.search("rust")
        ids = {d.id for d in results}
        assert ids == {"b"}

    def test_search_with_filters(self, store):
        results = store.search("python", filters={"maturity": "draft"})
        ids = {d.id for d in results}
        assert ids == {"a", "c"}

    def test_search_no_match(self, store):
        results = store.search("nonexistent_xyz")
        assert len(results) == 0

    def test_search_without_index_falls_back_to_fs(self, tmp_path):
        store = _make_store(tmp_path)  # no index
        _create_doc(store, cap_id="a", name="Python Tool", description="Does stuff")
        results = store.search("python")
        assert len(results) == 1
        assert results[0].id == "a"

    def test_search_disabled_excluded(self, store):
        store.disable("a", CapabilityScope.WORKSPACE)
        results = store.search("python")
        ids = {d.id for d in results}
        assert "a" not in ids

    def test_search_archived_excluded(self, store):
        store.archive("a", CapabilityScope.WORKSPACE)
        results = store.search("python")
        ids = {d.id for d in results}
        assert "a" not in ids


# ── disable ───────────────────────────────────────────────────

class TestDisable:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path, with_index=True)
        _create_doc(s, cap_id="to_disable")
        return s

    def test_disable_sets_status(self, store):
        doc = store.disable("to_disable", CapabilityScope.WORKSPACE)
        assert doc.manifest.status == CapabilityStatus.DISABLED

    def test_disable_preserves_files(self, store):
        store.disable("to_disable", CapabilityScope.WORKSPACE)
        cap_dir = store._get_dir("to_disable", CapabilityScope.WORKSPACE)
        assert cap_dir.is_dir()
        assert (cap_dir / "CAPABILITY.md").exists()
        assert (cap_dir / "manifest.json").exists()

    def test_disable_nonexistent_raises(self, store):
        with pytest.raises(CapabilityError):
            store.disable("nope")

    def test_disable_idempotent(self, store):
        store.disable("to_disable", CapabilityScope.WORKSPACE)
        doc = store.disable("to_disable", CapabilityScope.WORKSPACE)
        assert doc.manifest.status == CapabilityStatus.DISABLED

    def test_disable_updates_manifest_json(self, store):
        store.disable("to_disable", CapabilityScope.WORKSPACE)
        cap_dir = store._get_dir("to_disable", CapabilityScope.WORKSPACE)
        manifest = json.loads((cap_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "disabled"

    def test_disable_without_scope_uses_precedence(self, store):
        doc = store.disable("to_disable")
        assert doc.manifest.status == CapabilityStatus.DISABLED


# ── archive ───────────────────────────────────────────────────

class TestArchive:
    @pytest.fixture
    def store(self, tmp_path):
        s = _make_store(tmp_path, with_index=True)
        _create_doc(s, cap_id="to_archive")
        return s

    def test_archive_moves_directory(self, store):
        store.archive("to_archive", CapabilityScope.WORKSPACE)
        original = store._get_dir("to_archive", CapabilityScope.WORKSPACE)
        assert not original.exists()
        archive_dirs = list((store.data_dir / "archived" / "workspace").iterdir())
        assert len(archive_dirs) >= 1

    def test_archive_excludes_from_default_list(self, store):
        store.archive("to_archive", CapabilityScope.WORKSPACE)
        results = store.list()
        ids = {d.id for d in results}
        assert "to_archive" not in ids

    def test_archive_preserves_files(self, store):
        store.archive("to_archive", CapabilityScope.WORKSPACE)
        archive_dirs = list((store.data_dir / "archived" / "workspace").iterdir())
        archived = archive_dirs[0]
        assert (archived / "CAPABILITY.md").exists()
        assert (archived / "manifest.json").exists()

    def test_archive_nonexistent_raises(self, store):
        with pytest.raises(CapabilityError):
            store.archive("nope")


# ── rebuild_index / refresh_index_for ─────────────────────────

class TestIndexManagement:
    def test_rebuild_index(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        _create_doc(store, cap_id="a")
        _create_doc(store, cap_id="b")
        count = store.rebuild_index()
        assert count == 2

    def test_rebuild_index_no_index_returns_zero(self, tmp_path):
        store = _make_store(tmp_path)  # no index
        assert store.rebuild_index() == 0

    def test_refresh_index_for(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        _create_doc(store, cap_id="refresh_me")
        store.refresh_index_for("refresh_me", CapabilityScope.WORKSPACE)
        assert store._index.get("refresh_me", "workspace") is not None

    def test_refresh_index_for_nonexistent_no_error(self, tmp_path):
        store = _make_store(tmp_path, with_index=True)
        store.refresh_index_for("nope", CapabilityScope.WORKSPACE)  # should not raise


# ── MutationLog integration ───────────────────────────────────

class TestMutationLogIntegration:
    def test_create_draft_calls_record(self, tmp_path):
        mock_log = MagicMock()
        mock_log.record = MagicMock()
        store = CapabilityStore(data_dir=tmp_path / "capabilities", mutation_log=mock_log)
        doc = _create_doc(store, cap_id="ml_test")
        mock_log.record.assert_called()

    def test_disable_calls_record(self, tmp_path):
        mock_log = MagicMock()
        mock_log.record = MagicMock()
        store = CapabilityStore(data_dir=tmp_path / "capabilities", mutation_log=mock_log)
        _create_doc(store, cap_id="ml_disable")
        mock_log.record.reset_mock()
        store.disable("ml_disable", CapabilityScope.WORKSPACE)
        mock_log.record.assert_called()

    def test_archive_calls_record(self, tmp_path):
        mock_log = MagicMock()
        mock_log.record = MagicMock()
        store = CapabilityStore(data_dir=tmp_path / "capabilities", mutation_log=mock_log)
        _create_doc(store, cap_id="ml_archive")
        mock_log.record.reset_mock()
        store.archive("ml_archive", CapabilityScope.WORKSPACE)
        mock_log.record.assert_called()

    def test_no_mutation_log_no_error(self, tmp_path):
        store = _make_store(tmp_path)  # mutation_log=None
        doc = _create_doc(store, cap_id="no_ml")
        store.disable("no_ml", CapabilityScope.WORKSPACE)
        # archive with no mutation_log
        _create_doc(store, cap_id="no_ml_2")
        store.archive("no_ml_2", CapabilityScope.WORKSPACE)
        # no exceptions raised


# ── Hash stability through store operations ───────────────────

class TestHashStability:
    @pytest.fixture
    def store(self, tmp_path):
        return _make_store(tmp_path)

    def test_hash_identical_after_create_and_read(self, store):
        doc1 = _create_doc(store, cap_id="hash1")
        doc2 = store.get("hash1", CapabilityScope.WORKSPACE)
        assert doc1.content_hash == doc2.content_hash

    def test_hash_changes_after_disable(self, store):
        doc1 = _create_doc(store, cap_id="hash2")
        h1 = doc1.content_hash
        doc2 = store.disable("hash2", CapabilityScope.WORKSPACE)
        assert doc2.content_hash != h1  # status change changes hash

    def test_no_self_referential_hash_churn(self, store):
        """Repeated get should return same hash."""
        _create_doc(store, cap_id="hash3")
        h1 = store.get("hash3", CapabilityScope.WORKSPACE).content_hash
        h2 = store.get("hash3", CapabilityScope.WORKSPACE).content_hash
        assert h1 == h2
