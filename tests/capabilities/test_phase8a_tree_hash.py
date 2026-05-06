"""Phase 8A-1: Tree hash tests — determinism, exclusions, includes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.provenance import (
    compute_capability_tree_hash,
    compute_package_hash,
)


def _write_capability(dir_path: Path, cap_id: str, **overrides) -> None:
    """Write a minimal capability to dir_path."""
    fm = {
        "id": cap_id,
        "name": f"Test {cap_id}",
        "description": "Test capability for tree hashing.",
        "type": "skill",
        "scope": "user",
        "version": "0.1.0",
        "maturity": "draft",
        "status": "quarantined",
        "risk_level": "low",
        "triggers": [],
        "tags": [],
        "trust_required": "developer",
        "required_tools": [],
        "required_permissions": [],
    }
    fm.update(overrides)
    fm_yaml = yaml.dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = (
        f"---\n{fm_yaml}\n---\n\n"
        "## When to use\nTest.\n\n"
        "## Procedure\n1. Test\n\n"
        "## Verification\nPass.\n\n"
        "## Failure handling\nRetry."
    )
    (dir_path / "CAPABILITY.md").write_text(md, encoding="utf-8")
    manifest = {k: v for k, v in fm.items() if k not in ("version",)}
    (dir_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_file(dir_path: Path, rel_path: str, content: str) -> None:
    """Write a file relative to dir_path, creating parent dirs."""
    target = dir_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class TestTreeHashDeterminism:
    """Same tree produces same hash. Different tree produces different hash."""

    def test_same_content_same_hash(self, tmp_path: Path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            d.mkdir()
            _write_capability(d, "test-cap")
        h1 = compute_capability_tree_hash(dir_a)
        h2 = compute_capability_tree_hash(dir_b)
        assert h1 == h2
        assert len(h1) == 64

    def test_different_capability_md_changes_hash(self, tmp_path: Path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            d.mkdir()
            _write_capability(d, "test-cap")
        (dir_b / "CAPABILITY.md").write_text("# Different content", encoding="utf-8")
        assert compute_capability_tree_hash(dir_a) != compute_capability_tree_hash(dir_b)

    def test_different_manifest_changes_hash(self, tmp_path: Path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            d.mkdir()
            _write_capability(d, "test-cap")
        _write_capability(dir_b, "test-cap", description="Changed description.")
        assert compute_capability_tree_hash(dir_a) != compute_capability_tree_hash(dir_b)

    def test_empty_directory(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        h = compute_capability_tree_hash(d)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_nonexistent_directory(self, tmp_path: Path):
        h = compute_capability_tree_hash(tmp_path / "does_not_exist")
        assert h == ""

    def test_file_ordering_does_not_matter(self, tmp_path: Path):
        """Files sorted by name produce the same hash regardless of FS order."""
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        (d / "scripts").mkdir()
        (d / "scripts" / "b.py").write_text("b", encoding="utf-8")
        (d / "scripts" / "a.py").write_text("a", encoding="utf-8")
        h1 = compute_capability_tree_hash(d)

        # Recreate in different order
        d2 = tmp_path / "cap2"
        d2.mkdir()
        _write_capability(d2, "test-cap")
        (d2 / "scripts").mkdir()
        (d2 / "scripts" / "a.py").write_text("a", encoding="utf-8")
        (d2 / "scripts" / "b.py").write_text("b", encoding="utf-8")
        h2 = compute_capability_tree_hash(d2)
        assert h1 == h2

    def test_package_hash_is_alias(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        assert compute_capability_tree_hash(d) == compute_package_hash(d)


class TestTreeHashIncludes:
    """Files that must be included in the tree hash."""

    def test_includes_capability_md(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        h1 = compute_capability_tree_hash(d)
        (d / "CAPABILITY.md").write_text("# Changed", encoding="utf-8")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_includes_manifest_json(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        h1 = compute_capability_tree_hash(d)
        _write_capability(d, "test-cap", description="New description")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_includes_scripts(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "scripts/run.py", "print('hello')")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_includes_tests(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "tests/test_foo.py", "def test(): pass")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2

    def test_includes_examples(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "examples/example.md", "# Example")
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2


class TestTreeHashExcludes:
    """Files and directories that must be excluded from the tree hash."""

    def _setup_cap(self, tmp_path: Path) -> Path:
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        return d

    def test_excludes_provenance_json(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        (d / "provenance.json").write_text('{"test": true}', encoding="utf-8")
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_import_report_json(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "import_report.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_activation_report_json(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "activation_report.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_evals(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "evals/eval_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_traces(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "traces/trace_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_versions(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "versions/v1_snapshot/manifest.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_quarantine_audit_reports(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "quarantine_audit_reports/audit_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_quarantine_reviews(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "quarantine_reviews/review_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_quarantine_transition_requests(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "quarantine_transition_requests/qtr_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_quarantine_activation_plans(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "quarantine_activation_plans/qap_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_quarantine_activation_reports(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "quarantine_activation_reports/qar_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_provenance_verification_logs(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "provenance_verification_logs/log_001.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_sqlite(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "index.sqlite", "binary data")
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_hidden_directory(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "__pycache__/module.pyc", "cached")
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_dot_files(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, ".hidden/config.json", '{"test": true}')
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_excludes_gitkeep(self, tmp_path: Path):
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        _write_file(d, "scripts/.gitkeep", "")
        (d / ".gitkeep").write_text("", encoding="utf-8")
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_manifest_normalized_ignores_content_hash(self, tmp_path: Path):
        """manifest.json content_hash changes should not affect tree hash."""
        d = self._setup_cap(tmp_path)
        h1 = compute_capability_tree_hash(d)
        # Add content_hash to manifest.json
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        manifest["content_hash"] = "abc123"
        (d / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2


class TestTreeHashBinary:
    """Binary file handling."""

    def test_binary_file_hashed_by_bytes(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        binary_content = bytes(range(256))
        (d / "scripts").mkdir()
        (d / "scripts" / "data.bin").write_bytes(binary_content)
        h1 = compute_capability_tree_hash(d)
        # Same bytes, same hash
        d2 = tmp_path / "cap2"
        d2.mkdir()
        _write_capability(d2, "test-cap")
        (d2 / "scripts").mkdir()
        (d2 / "scripts" / "data.bin").write_bytes(binary_content)
        h2 = compute_capability_tree_hash(d2)
        assert h1 == h2

    def test_binary_file_change_changes_hash(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        (d / "scripts").mkdir()
        (d / "scripts" / "data.bin").write_bytes(bytes([1, 2, 3]))
        h1 = compute_capability_tree_hash(d)
        (d / "scripts" / "data.bin").write_bytes(bytes([4, 5, 6]))
        h2 = compute_capability_tree_hash(d)
        assert h1 != h2


class TestTreeHashSymlinks:
    """Symlinks are skipped (never followed)."""

    def test_symlinks_skipped(self, tmp_path: Path):
        d = tmp_path / "cap"
        d.mkdir()
        _write_capability(d, "test-cap")
        (d / "examples").mkdir()
        real_file = tmp_path / "real.txt"
        real_file.write_text("real content", encoding="utf-8")
        link = d / "examples" / "linked.txt"
        link.symlink_to(real_file)
        h = compute_capability_tree_hash(d)
        assert isinstance(h, str)
        assert len(h) == 64
