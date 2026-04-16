"""tests/core/test_soul_manager.py — SoulManager 测试。"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from src.core.soul_manager import SoulManager


@pytest.fixture
def soul_env(tmp_path):
    """创建临时 soul 环境。"""
    soul_path = tmp_path / "soul.md"
    soul_path.write_text("# Lapwing\n\n原始内容。", encoding="utf-8")

    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()

    return SoulManager(soul_path=soul_path, snapshot_dir=snapshot_dir)


class TestRead:
    def test_read_existing_file(self, soul_env):
        content = soul_env.read()
        assert "Lapwing" in content
        assert "原始内容" in content

    def test_read_missing_file(self, tmp_path):
        mgr = SoulManager(
            soul_path=tmp_path / "nonexistent.md",
            snapshot_dir=tmp_path / "snapshots",
        )
        assert mgr.read() == ""


class TestEdit:
    def test_edit_normal(self, soul_env):
        result = soul_env.edit("# Lapwing\n\n新内容。", actor="kevin")
        assert result["success"] is True
        assert "已更新" in result["reason"]
        assert soul_env.read() == "# Lapwing\n\n新内容。"

    def test_edit_no_change(self, soul_env):
        original = soul_env.read()
        result = soul_env.edit(original, actor="kevin")
        assert result["success"] is True
        assert "没有变化" in result["reason"]

    def test_edit_creates_snapshot(self, soul_env):
        soul_env.edit("# Lapwing\n\n修改后。", actor="kevin")
        snapshots = soul_env.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["actor"] == "kevin"

    def test_diff_summary_correct(self, soul_env):
        result = soul_env.edit("# Lapwing\n\n新内容。\n额外行。", actor="kevin")
        assert "+" in result["diff_summary"]


class TestCooldown:
    def test_lapwing_blocked_within_cooldown(self, soul_env):
        # 第一次编辑（as lapwing）
        soul_env.edit("# 第一次修改", actor="lapwing")

        # 第二次编辑应该被冷却拒绝
        result = soul_env.edit("# 第二次修改", actor="lapwing")
        assert result["success"] is False
        assert "不足" in result["reason"]

    def test_kevin_not_blocked_by_cooldown(self, soul_env):
        # 先 lapwing 编辑
        soul_env.edit("# Lapwing 修改", actor="lapwing")

        # Kevin 编辑应该成功（豁免冷却）
        result = soul_env.edit("# Kevin 修改", actor="kevin")
        assert result["success"] is True

    def test_lapwing_can_edit_after_cooldown(self, soul_env):
        # 编辑一次
        soul_env.edit("# 修改", actor="lapwing")

        # 模拟 25 小时后
        future_time = datetime(2099, 1, 1, tzinfo=ZoneInfo("Asia/Taipei"))
        with patch(
            "src.core.soul_manager.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = future_time
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = soul_env.edit("# 冷却后修改", actor="lapwing")
            assert result["success"] is True


class TestSnapshots:
    def test_list_snapshots_empty(self, soul_env):
        assert soul_env.list_snapshots() == []

    def test_list_snapshots_after_edits(self, soul_env):
        soul_env.edit("# v1", actor="kevin")
        soul_env.edit("# v2", actor="kevin")
        snapshots = soul_env.list_snapshots()
        assert len(snapshots) == 2
        # 最新的排在前面
        assert snapshots[0]["diff_summary"] != ""

    def test_list_snapshots_limit(self, soul_env):
        for i in range(5):
            soul_env.edit(f"# v{i}", actor="kevin")
        snapshots = soul_env.list_snapshots(limit=2)
        assert len(snapshots) == 2


class TestRollback:
    def test_rollback_success(self, soul_env):
        original = soul_env.read()
        soul_env.edit("# 新版本", actor="kevin")

        # 获取快照 ID
        snapshots = soul_env.list_snapshots()
        snapshot_id = snapshots[0]["snapshot_id"]

        # 回滚
        result = soul_env.rollback(snapshot_id)
        assert result["success"] is True
        assert soul_env.read() == original

    def test_rollback_nonexistent_snapshot(self, soul_env):
        result = soul_env.rollback("soul_99991231_235959")
        assert result["success"] is False
        assert "不存在" in result["reason"]

    def test_rollback_creates_backup_snapshot(self, soul_env):
        soul_env.edit("# 新版本", actor="kevin")
        snapshots_before = len(soul_env.list_snapshots())

        snapshot_id = soul_env.list_snapshots()[0]["snapshot_id"]
        soul_env.rollback(snapshot_id)

        # 回滚前会先保存当前版本
        assert len(soul_env.list_snapshots()) == snapshots_before + 1


class TestDiff:
    def test_get_diff(self, soul_env):
        soul_env.edit("# 新版本\n\n完全不同的内容。", actor="kevin")
        snapshot_id = soul_env.list_snapshots()[0]["snapshot_id"]
        diff = soul_env.get_diff(snapshot_id)
        assert "+" in diff or "-" in diff

    def test_diff_nonexistent(self, soul_env):
        diff = soul_env.get_diff("soul_99991231_235959")
        assert "不存在" in diff

    def test_diff_same_content(self, soul_env):
        # 编辑后立即回滚，当前内容 = 快照内容
        soul_env.edit("# 新版本", actor="kevin")
        snapshot_id = soul_env.list_snapshots()[0]["snapshot_id"]
        soul_env.rollback(snapshot_id)
        # 此时最旧的快照就是原始内容
        # 找回滚后保存的快照（第一个，也就是最新的，是回滚前保存的新版本）
        newest = soul_env.list_snapshots()[0]
        diff = soul_env.get_diff(newest["snapshot_id"])
        # 新版本 vs 原始内容 => 应该有差异
        assert diff  # 不为空


class TestCleanup:
    def test_cleanup_old_snapshots(self, tmp_path):
        soul_path = tmp_path / "soul.md"
        soul_path.write_text("初始", encoding="utf-8")
        snapshot_dir = tmp_path / "snapshots"

        mgr = SoulManager(soul_path=soul_path, snapshot_dir=snapshot_dir)
        mgr.MAX_SNAPSHOTS = 3

        for i in range(5):
            mgr.edit(f"版本 {i}", actor="kevin")

        md_files = list(snapshot_dir.glob("soul_*.md"))
        meta_files = list(snapshot_dir.glob("soul_*.meta.json"))
        assert len(md_files) <= 3
        assert len(meta_files) <= 3
