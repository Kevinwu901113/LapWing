"""测试文件快照管理器。"""

import os
import pytest
from pathlib import Path

from src.core.checkpoint_manager import CheckpointManager


class TestCheckpointManager:
    def test_snapshot_creates_commit(self, tmp_path):
        work_dir = tmp_path / "project"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("hello")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        commit_hash = mgr.snapshot(str(work_dir))
        assert commit_hash is not None
        assert len(commit_hash) == 40  # full SHA

    def test_snapshot_skips_no_changes(self, tmp_path):
        work_dir = tmp_path / "project"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("hello")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        # 第一次快照
        mgr.snapshot(str(work_dir))
        mgr.new_turn()  # 重置 per-turn 限制
        # 没改文件，第二次应该返回 None
        result = mgr.snapshot(str(work_dir))
        assert result is None

    def test_snapshot_once_per_turn(self, tmp_path):
        work_dir = tmp_path / "project"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("hello")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        first = mgr.snapshot(str(work_dir))
        assert first is not None
        # 同一 turn 内第二次调用应返回 None
        (work_dir / "file.txt").write_text("changed")
        second = mgr.snapshot(str(work_dir))
        assert second is None

    def test_new_turn_resets(self, tmp_path):
        work_dir = tmp_path / "project"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("hello")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        mgr.snapshot(str(work_dir))
        mgr.new_turn()
        (work_dir / "file.txt").write_text("changed")
        result = mgr.snapshot(str(work_dir))
        assert result is not None

    def test_rollback_restores_file(self, tmp_path):
        work_dir = tmp_path / "project"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("original")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        commit_hash = mgr.snapshot(str(work_dir))
        assert commit_hash is not None

        # 修改文件
        (work_dir / "file.txt").write_text("modified")
        assert (work_dir / "file.txt").read_text() == "modified"

        # 回滚
        success = mgr.rollback(str(work_dir), commit_hash)
        assert success is True
        assert (work_dir / "file.txt").read_text() == "original"

    def test_list_checkpoints(self, tmp_path):
        work_dir = tmp_path / "project"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("v1")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        mgr.snapshot(str(work_dir))
        mgr.new_turn()
        (work_dir / "file.txt").write_text("v2")
        mgr.snapshot(str(work_dir))

        checkpoints = mgr.list_checkpoints(str(work_dir))
        assert len(checkpoints) == 2
        assert "hash" in checkpoints[0]
        assert "time" in checkpoints[0]

    def test_shadow_repo_doesnt_pollute_workdir(self, tmp_path):
        work_dir = tmp_path / "project"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("hello")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        mgr.snapshot(str(work_dir))

        # .git 不应出现在工作目录
        assert not (work_dir / ".git").exists()

    def test_nonexistent_dir_returns_none(self, tmp_path):
        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        result = mgr.snapshot(str(tmp_path / "nonexistent"))
        assert result is None

    def test_rollback_nonexistent_returns_false(self, tmp_path):
        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        result = mgr.rollback(str(tmp_path / "project"), "abc123")
        assert result is False

    def test_list_checkpoints_no_repo(self, tmp_path):
        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        result = mgr.list_checkpoints(str(tmp_path / "project"))
        assert result == []

    def test_multiple_dirs_independent(self, tmp_path):
        dir_a = tmp_path / "project_a"
        dir_b = tmp_path / "project_b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "a.txt").write_text("aaa")
        (dir_b / "b.txt").write_text("bbb")

        mgr = CheckpointManager(base_dir=tmp_path / "checkpoints")
        hash_a = mgr.snapshot(str(dir_a))
        hash_b = mgr.snapshot(str(dir_b))
        assert hash_a is not None
        assert hash_b is not None
        # 独立目录应有不同 hash
        assert hash_a != hash_b
