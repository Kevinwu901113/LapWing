"""Phase 7A tests: inspect_capability_package — parse + eval + policy, zero writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.document import CapabilityParser
from src.capabilities.errors import CapabilityError
from src.capabilities.evaluator import CapabilityEvaluator
from src.capabilities.import_quarantine import inspect_capability_package
from src.capabilities.index import CapabilityIndex
from src.capabilities.policy import CapabilityPolicy
from src.capabilities.schema import CapabilityScope
from src.capabilities.store import CapabilityStore


def _make_store(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(data_dir=tmp_path / "capabilities")


def _write_package(dir_path: Path, *, manifest_overrides: dict | None = None, body: str = "", include_capability_md: bool = True) -> Path:
    """Create a valid capability package directory for testing."""
    dir_path.mkdir(parents=True, exist_ok=True)
    fm = {
        "id": "test_pkg_01",
        "name": "Test Package",
        "description": "A test capability package for import testing.",
        "type": "skill",
        "scope": "user",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "active",
        "risk_level": "low",
        "triggers": ["test trigger"],
        "tags": ["test", "import"],
    }
    if manifest_overrides:
        fm.update(manifest_overrides)

    if include_capability_md:
        fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
        md = f"---\n{fm_yaml}\n---\n\n# Test Package\n\n## When to use\nFor testing imports.\n\n## Procedure\n1. Inspect\n2. Import\n\n## Verification\nCheck the import report.\n\n## Failure handling\nRe-import if needed.\n\n{body}"
        (dir_path / "CAPABILITY.md").write_text(md, encoding="utf-8")

    manifest = {
        "id": fm["id"],
        "name": fm["name"],
        "description": fm["description"],
        "type": fm["type"],
        "scope": fm["scope"],
        "version": fm.get("version", "0.1.0"),
        "maturity": fm.get("maturity", "draft"),
        "status": fm.get("status", "active"),
        "risk_level": fm.get("risk_level", "low"),
        "triggers": fm.get("triggers", []),
        "tags": fm.get("tags", []),
    }
    (dir_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for sub in ("scripts", "tests", "examples"):
        (dir_path / sub).mkdir(exist_ok=True)

    return dir_path


@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


@pytest.fixture
def evaluator():
    return CapabilityEvaluator()


@pytest.fixture
def policy():
    return CapabilityPolicy()


# ── Valid package inspection ─────────────────────────────────────────


class TestInspectValidPackage:
    def test_valid_package_inspected(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "valid_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
        )
        assert result.id == "test_pkg_01"
        assert result.name == "Test Package"
        assert result.type == "skill"
        assert result.status == "quarantined"
        assert result.maturity == "draft"
        assert result.target_scope == "user"
        assert result.would_import is True
        assert "quarantin" in result.quarantine_reason.lower()

    def test_inspect_includes_eval_findings(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "eval_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
        )
        assert isinstance(result.eval_findings, list)
        assert isinstance(result.eval_passed, bool)
        assert isinstance(result.eval_score, float)

    def test_inspect_includes_policy_findings(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "policy_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
        )
        assert isinstance(result.policy_findings, list)

    def test_inspect_no_files_written(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "no_write_pkg")
        before = set(p.name for p in pkg.iterdir())
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
        )
        after = set(p.name for p in pkg.iterdir())
        assert before == after  # No files added to source

    def test_inspect_no_index_update(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "no_index_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
        )
        # Inspect should not create any files in the store
        assert not list(store.data_dir.rglob("*.json"))
        assert not list(store.data_dir.rglob("CAPABILITY.md"))


# ── Invalid / edge case inspection ────────────────────────────────────


class TestInspectInvalidPackage:
    def test_nonexistent_path_rejected(self, store, evaluator, policy):
        with pytest.raises(CapabilityError, match="does not exist"):
            inspect_capability_package(
                path="/tmp/nonexistent_import_pkg_xyz",
                store=store, evaluator=evaluator, policy=policy,
            )

    def test_file_not_directory_rejected(self, tmp_path, store, evaluator, policy):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("not a directory")
        with pytest.raises(CapabilityError, match="not a directory"):
            inspect_capability_package(
                path=f, store=store, evaluator=evaluator, policy=policy,
            )

    def test_missing_capability_md_rejected(self, tmp_path, store, evaluator, policy):
        pkg = tmp_path / "no_md"
        pkg.mkdir()
        (pkg / "manifest.json").write_text('{"id":"test","name":"T","description":"D","type":"skill","scope":"user","version":"0.1.0","maturity":"draft","status":"active","risk_level":"low"}')
        with pytest.raises(CapabilityError, match="Missing CAPABILITY.md"):
            inspect_capability_package(
                path=pkg, store=store, evaluator=evaluator, policy=policy,
            )

    def test_invalid_manifest_rejected_cleanly(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "invalid_manifest", manifest_overrides={"type": "invalid_type_value"})
        with pytest.raises(CapabilityError):
            inspect_capability_package(
                path=pkg, store=store, evaluator=evaluator, policy=policy,
            )


# ── File listings ─────────────────────────────────────────────────────


class TestInspectFileListings:
    def test_include_files_true(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "files_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
            include_files=True,
        )
        assert "scripts" in result.files
        assert "tests" in result.files

    def test_include_files_false(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "nofiles_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
            include_files=False,
        )
        assert result.files == {}


# ── Scope ─────────────────────────────────────────────────────────────


class TestInspectScope:
    def test_default_target_scope_is_user(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "scope_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
        )
        assert result.target_scope == "user"

    def test_explicit_target_scope(self, tmp_path, store, evaluator, policy):
        pkg = _write_package(tmp_path / "workspace_scope_pkg")
        result = inspect_capability_package(
            path=pkg, store=store, evaluator=evaluator, policy=policy,
            target_scope="workspace",
        )
        assert result.target_scope == "workspace"
