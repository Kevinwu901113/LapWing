"""Phase 2A tests: version snapshots on disable/archive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.capabilities.document import CapabilityDocument, parse_capability
from src.capabilities.schema import CapabilityManifest, CapabilityMaturity, CapabilityRiskLevel, CapabilityScope, CapabilityStatus, CapabilityType
from src.capabilities.versioning import (
    VersionSnapshot,
    create_version_snapshot,
    list_version_snapshots,
    snapshot_on_archive,
    snapshot_on_disable,
)
from src.capabilities.store import CapabilityStore


_VALID_FRONT_MATTER = {
    "id": "test_skill_01",
    "name": "Test Skill",
    "description": "A test capability.",
    "type": "skill",
    "scope": "workspace",
    "version": "0.1.0",
    "maturity": "draft",
    "status": "active",
    "risk_level": "low",
}


def _write_capability_dir(base: Path, dirname: str, front_matter: dict, body: str = "",
                          manifest: dict | None = None) -> Path:
    cap_dir = base / dirname
    cap_dir.mkdir(parents=True, exist_ok=True)
    fm_yaml = yaml.dump(front_matter, allow_unicode=True, sort_keys=False)
    md = f"---\n{fm_yaml}---\n\n{body}"
    (cap_dir / "CAPABILITY.md").write_text(md, encoding="utf-8")
    if manifest is not None:
        (cap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return cap_dir


# ── create_version_snapshot ───────────────────────────────────

class TestCreateVersionSnapshot:
    def test_creates_snapshot_directory(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        snap = create_version_snapshot(doc, "manual")
        assert (cap_dir / "versions").is_dir()
        snapshots = list((cap_dir / "versions").iterdir())
        assert len(snapshots) == 1
        assert snapshots[0].is_dir()

    def test_copies_capability_md(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER, body="# Test body")
        doc = parse_capability(cap_dir)
        snap = create_version_snapshot(doc, "manual")
        snap_dir = cap_dir / snap.snapshot_dir
        assert (snap_dir / "CAPABILITY.md").exists()
        content = (snap_dir / "CAPABILITY.md").read_text(encoding="utf-8")
        assert "# Test body" in content

    def test_copies_manifest_json(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER,
                                        manifest={"name": "From Manifest"})
        doc = parse_capability(cap_dir)
        snap = create_version_snapshot(doc, "manual")
        snap_dir = cap_dir / snap.snapshot_dir
        assert (snap_dir / "manifest.json").exists()

    def test_reconstructs_manifest_if_missing(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        # Remove manifest.json if it exists (it won't since we didn't write one)
        doc = parse_capability(cap_dir)
        snap = create_version_snapshot(doc, "manual")
        snap_dir = cap_dir / snap.snapshot_dir
        assert (snap_dir / "manifest.json").exists()

    def test_trigger_disabled(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        snap = create_version_snapshot(doc, "disabled")
        assert snap.trigger == "disabled"

    def test_trigger_archived(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        snap = create_version_snapshot(doc, "archived")
        assert snap.trigger == "archived"

    def test_includes_version_and_hash(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        snap = create_version_snapshot(doc, "manual")
        assert snap.version == "0.1.0"
        assert len(snap.content_hash) == 64

    def test_multiple_snapshots_different_timestamps(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        snap1 = create_version_snapshot(doc, "manual")
        import time
        time.sleep(0.1)
        snap2 = create_version_snapshot(doc, "manual")
        assert snap1.snapshot_dir != snap2.snapshot_dir


# ── list_version_snapshots ────────────────────────────────────

class TestListVersionSnapshots:
    def test_empty_when_no_snapshots(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        assert list_version_snapshots(doc) == []

    def test_lists_single_snapshot(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        create_version_snapshot(doc, "manual")
        result = list_version_snapshots(doc)
        assert len(result) == 1
        assert isinstance(result[0], VersionSnapshot)

    def test_lists_multiple_sorted_desc(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        create_version_snapshot(doc, "manual")
        import time
        time.sleep(0.1)
        create_version_snapshot(doc, "manual")
        result = list_version_snapshots(doc)
        assert len(result) == 2

    def test_ignores_other_dirs(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        create_version_snapshot(doc, "manual")
        (cap_dir / "versions" / "not_a_snapshot").mkdir(exist_ok=True)
        result = list_version_snapshots(doc)
        assert len(result) == 1  # the non-v-prefixed dir is ignored


# ── snapshot_on_disable / snapshot_on_archive ─────────────────

class TestConvenienceFunctions:
    def test_snapshot_on_disable(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        snap = snapshot_on_disable(doc)
        assert snap.trigger == "disabled"

    def test_snapshot_on_archive(self, tmp_path):
        cap_dir = _write_capability_dir(tmp_path, "cap", _VALID_FRONT_MATTER)
        doc = parse_capability(cap_dir)
        snap = snapshot_on_archive(doc)
        assert snap.trigger == "archived"


# ── Integration with CapabilityStore ──────────────────────────

class TestIntegrationWithStore:
    @pytest.fixture
    def store(self, tmp_path):
        return CapabilityStore(data_dir=tmp_path / "capabilities")

    def test_disable_creates_snapshot(self, store):
        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            name="Test Skill",
            description="A test capability.",
            cap_id="test_skill_01",
        )
        snapshot_on_disable(doc)
        assert len(list_version_snapshots(doc)) == 1

    def test_archive_creates_snapshot(self, store):
        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            name="Test Skill",
            description="A test capability.",
            cap_id="test_skill_02",
        )
        snapshot_on_archive(doc)
        assert len(list_version_snapshots(doc)) == 1

    def test_disable_enable_disable_creates_two_snapshots(self, store):
        doc = store.create_draft(
            scope=CapabilityScope.WORKSPACE,
            name="Test Skill",
            description="A test capability.",
            cap_id="test_skill_03",
        )
        snapshot_on_disable(doc)
        snapshot_on_disable(doc, reason="second disable")
        assert len(list_version_snapshots(doc)) == 2
