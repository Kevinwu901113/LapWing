"""Phase 7A tests: import_capability_package — quarantine enforcement, origin metadata, duplicate blocking, dry-run, retrieval exclusion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.document import CapabilityParser
from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.import_quarantine import import_capability_package
from src.capabilities.index import CapabilityIndex
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.schema import CapabilityScope, CapabilityStatus
from src.capabilities.store import CapabilityStore


def _make_store(tmp_path: Path, *, with_index: bool = False) -> CapabilityStore:
    kwargs: dict = {}
    if with_index:
        idx = CapabilityIndex(tmp_path / "index.db")
        idx.init()
        kwargs["index"] = idx
    return CapabilityStore(data_dir=tmp_path / "capabilities", **kwargs)


def _write_package(dir_path: Path, *, manifest_overrides: dict | None = None, body_extra: str = "") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    fm = {
        "id": "test_pkg_02",
        "name": "Test Import Package",
        "description": "A test capability for quarantine import.",
        "type": "skill",
        "scope": "user",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "active",
        "risk_level": "low",
        "triggers": ["test"],
        "tags": ["test", "quarantine"],
    }
    if manifest_overrides:
        fm.update(manifest_overrides)

    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = f"---\n{fm_yaml}\n---\n\n# Test Import\n\n## When to use\nFor testing quarantine import.\n\n## Procedure\n1. Import\n2. Verify\n\n## Verification\nCheck import_report.json.\n\n## Failure handling\nClean up quarantine dir.\n{body_extra}"
    (dir_path / "CAPABILITY.md").write_text(md, encoding="utf-8")

    manifest = {
        "id": fm["id"], "name": fm["name"], "description": fm["description"],
        "type": fm["type"], "scope": fm["scope"], "version": fm.get("version", "0.1.0"),
        "maturity": fm.get("maturity", "draft"), "status": fm.get("status", "active"),
        "risk_level": fm.get("risk_level", "low"),
        "triggers": fm.get("triggers", []), "tags": fm.get("tags", []),
    }
    (dir_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for sub in ("scripts", "tests", "examples"):
        (dir_path / sub).mkdir(exist_ok=True)
    return dir_path


@pytest.fixture
def evaluator():
    return CapabilityEvaluator()


@pytest.fixture
def policy():
    return CapabilityPolicy()


# ── Basic import ──────────────────────────────────────────────────────


class TestBasicImport:
    def test_import_into_quarantine(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "src_pkg")
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        assert result.applied is True
        assert result.capability_id == "test_pkg_02"
        qdir = Path(result.quarantine_path)
        assert qdir.is_dir()
        assert (qdir / "CAPABILITY.md").is_file()
        assert (qdir / "import_report.json").is_file()

    def test_status_is_quarantined(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "status_pkg")
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        m = json.loads((Path(result.quarantine_path) / "manifest.json").read_text())
        assert m["status"] == "quarantined"

    def test_maturity_is_draft(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "mat_pkg", manifest_overrides={"maturity": "stable"})
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        m = json.loads((Path(result.quarantine_path) / "manifest.json").read_text())
        assert m["maturity"] == "draft"

    def test_package_status_active_ignored(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "active_src", manifest_overrides={"status": "active", "maturity": "stable"})
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        m = json.loads((Path(result.quarantine_path) / "manifest.json").read_text())
        assert m["status"] == "quarantined"
        assert m["maturity"] == "draft"


# ── Import report ─────────────────────────────────────────────────────


class TestImportReport:
    def test_report_written(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "rpt_pkg")
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
            imported_by="kevin", reason="Test import v0",
        )
        rpt = json.loads(Path(result.import_report_path).read_text())
        assert rpt["source_type"] == "local_package"
        assert "source_path_hash" in rpt
        assert len(rpt["source_path_hash"]) == 64
        assert rpt["imported_by"] == "kevin"
        assert rpt["quarantine_reason"] == "Test import v0"
        assert "original_content_hash" in rpt
        assert isinstance(rpt["eval_findings"], list)
        assert isinstance(rpt["policy_findings"], list)

    def test_source_path_hashed_not_stored_raw(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "hash_pkg")
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        rpt = json.loads(Path(result.import_report_path).read_text())
        assert str(pkg) not in json.dumps(rpt).lower() or str(pkg) not in rpt.get("source_path_hash", "")


# ── Dry run ───────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_writes_nothing(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "dry_pkg")
        quarantine_root = store.data_dir / "quarantine"
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
            dry_run=True,
        )
        assert result.dry_run is True
        assert result.applied is False
        assert result.quarantine_path == ""
        assert result.inspect_result is not None
        # No quarantine directory created
        assert not quarantine_root.exists() or not any(quarantine_root.iterdir())

    def test_dry_run_returns_inspect_info(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "dry2_pkg")
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
            dry_run=True,
        )
        assert result.inspect_result.would_import is True


# ── Duplicate handling ────────────────────────────────────────────────


class TestDuplicateHandling:
    def test_duplicate_active_rejected(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        store.create_draft(
            scope=CapabilityScope.WORKSPACE, cap_id="test_pkg_02",
            name="Existing", description="Already there.",
        )
        pkg = _write_package(tmp_path / "dup_active_pkg")
        result = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        assert result.applied is False
        assert any("already exists" in e.lower() for e in result.errors)

    def test_duplicate_quarantine_rejected(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "dup_quar_pkg")
        r1 = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        assert r1.applied is True
        r2 = import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        assert r2.applied is False
        assert any("quarantine" in e.lower() for e in r2.errors)


# ── Retrieval exclusion ───────────────────────────────────────────────


class TestRetrievalExclusion:
    def test_quarantined_excluded_from_default_list(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "excl_list_pkg")
        import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        docs = store.list()
        ids = {d.manifest.id for d in docs}
        assert "test_pkg_02" not in ids

    def test_quarantined_excluded_from_default_search(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "excl_search_pkg")
        import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        rows = idx.search(query="Import")
        ids = {r["id"] for r in rows}
        assert "test_pkg_02" not in ids

    def test_quarantined_found_with_explicit_status(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "excl_explicit_pkg")
        import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        rows = idx.search(query="Import", filters={"status": "quarantined"})
        ids = {r["id"] for r in rows}
        assert "test_pkg_02" in ids


# ── No execution, no promotion ────────────────────────────────────────


class TestNoExecutionNoPromotion:
    def test_quarantined_blocked_from_run(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "norun_pkg")
        import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        doc = CapabilityParser().parse(store.data_dir / "quarantine" / "test_pkg_02")
        d = policy.validate_run(doc.manifest)
        assert not d.allowed

    def test_quarantined_blocked_from_promote(self, tmp_path, evaluator, policy):
        store = _make_store(tmp_path, with_index=True)
        idx = store._index
        pkg = _write_package(tmp_path / "nopromo_pkg")
        import_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy, index=idx,
        )
        doc = CapabilityParser().parse(store.data_dir / "quarantine" / "test_pkg_02")
        d = policy.validate_promote(doc.manifest)
        assert not d.allowed
