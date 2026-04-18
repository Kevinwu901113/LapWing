"""tests/core/test_identity_file_manager.py — IdentityFileManager 测试。"""

from pathlib import Path

import pytest

from src.core.identity_file_manager import IdentityFileManager


@pytest.fixture
def voice_env(tmp_path: Path) -> IdentityFileManager:
    file_path = tmp_path / "prompts" / "lapwing_voice.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("## 说话方式\n\n原始内容。", encoding="utf-8")

    snapshot_dir = tmp_path / "identity" / "voice_snapshots"
    return IdentityFileManager(
        file_path=file_path,
        snapshot_dir=snapshot_dir,
        kind="voice",
    )


class TestRead:
    def test_read_existing(self, voice_env):
        assert "原始内容" in voice_env.read()

    def test_read_missing(self, tmp_path):
        mgr = IdentityFileManager(
            file_path=tmp_path / "missing.md",
            snapshot_dir=tmp_path / "snapshots",
            kind="voice",
        )
        assert mgr.read() == ""


class TestEdit:
    def test_edit_writes_and_snapshots(self, voice_env):
        result = voice_env.edit("## 说话方式\n\n新内容。", actor="kevin")
        assert result["success"] is True
        assert voice_env.read() == "## 说话方式\n\n新内容。"
        snaps = voice_env.list_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["actor"] == "kevin"
        assert snaps[0]["snapshot_id"].startswith("voice_")

    def test_edit_no_change(self, voice_env):
        result = voice_env.edit(voice_env.read(), actor="kevin")
        assert result["success"] is True
        assert "没有变化" in result["reason"]
        assert voice_env.list_snapshots() == []

    def test_no_cooldown_for_non_kevin(self, voice_env):
        """voice / constitution 不设 Lapwing 冷却。"""
        first = voice_env.edit("v1", actor="lapwing")
        second = voice_env.edit("v2", actor="lapwing")
        assert first["success"] is True
        assert second["success"] is True
        assert voice_env.read() == "v2"

    def test_edit_triggers_on_after_write(self, tmp_path):
        calls: list[int] = []
        file_path = tmp_path / "x.md"
        file_path.write_text("a", encoding="utf-8")
        mgr = IdentityFileManager(
            file_path=file_path,
            snapshot_dir=tmp_path / "snaps",
            kind="voice",
            on_after_write=lambda: calls.append(1),
        )
        mgr.edit("b", actor="kevin")
        assert calls == [1]

    def test_edit_creates_file_if_missing(self, tmp_path):
        mgr = IdentityFileManager(
            file_path=tmp_path / "new" / "file.md",
            snapshot_dir=tmp_path / "snaps",
            kind="voice",
        )
        result = mgr.edit("hello", actor="kevin")
        assert result["success"] is True
        assert mgr.read() == "hello"


class TestRollback:
    def test_rollback_restores(self, voice_env):
        original = voice_env.read()
        voice_env.edit("changed", actor="kevin")
        snap_id = voice_env.list_snapshots()[0]["snapshot_id"]
        result = voice_env.rollback(snap_id)
        assert result["success"] is True
        assert voice_env.read() == original

    def test_rollback_missing(self, voice_env):
        result = voice_env.rollback("voice_99991231_235959_000000")
        assert result["success"] is False
        assert "不存在" in result["reason"]

    def test_rollback_triggers_on_after_write(self, tmp_path):
        calls: list[int] = []
        file_path = tmp_path / "x.md"
        file_path.write_text("a", encoding="utf-8")
        mgr = IdentityFileManager(
            file_path=file_path,
            snapshot_dir=tmp_path / "snaps",
            kind="voice",
            on_after_write=lambda: calls.append(1),
        )
        mgr.edit("b", actor="kevin")  # snap 1
        snap_id = mgr.list_snapshots()[0]["snapshot_id"]
        calls.clear()
        mgr.rollback(snap_id)
        assert calls == [1]


class TestDiff:
    def test_get_diff(self, voice_env):
        voice_env.edit("完全新内容。", actor="kevin")
        snap_id = voice_env.list_snapshots()[0]["snapshot_id"]
        diff = voice_env.get_diff(snap_id)
        assert "+" in diff or "-" in diff

    def test_get_diff_missing(self, voice_env):
        diff = voice_env.get_diff("voice_99991231_235959_000000")
        assert "不存在" in diff


class TestCleanup:
    def test_max_snapshots_enforced(self, tmp_path):
        file_path = tmp_path / "x.md"
        file_path.write_text("0", encoding="utf-8")
        mgr = IdentityFileManager(
            file_path=file_path,
            snapshot_dir=tmp_path / "snaps",
            kind="voice",
            max_snapshots=3,
        )
        for i in range(5):
            mgr.edit(f"v{i + 1}", actor="kevin")

        md_files = list((tmp_path / "snaps").glob("voice_*.md"))
        meta_files = list((tmp_path / "snaps").glob("voice_*.meta.json"))
        assert len(md_files) <= 3
        assert len(meta_files) <= 3


class TestKindIsolation:
    def test_different_kinds_dont_collide(self, tmp_path):
        snapshot_dir = tmp_path / "shared_snaps"
        v = IdentityFileManager(
            file_path=tmp_path / "voice.md",
            snapshot_dir=snapshot_dir,
            kind="voice",
        )
        c = IdentityFileManager(
            file_path=tmp_path / "constitution.md",
            snapshot_dir=snapshot_dir,
            kind="constitution",
        )
        v.edit("voice1", actor="kevin")
        c.edit("const1", actor="kevin")
        assert len(v.list_snapshots()) == 1
        assert len(c.list_snapshots()) == 1
        assert v.list_snapshots()[0]["snapshot_id"].startswith("voice_")
        assert c.list_snapshots()[0]["snapshot_id"].startswith("constitution_")
