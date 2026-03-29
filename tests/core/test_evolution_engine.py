"""Tests for EvolutionEngine."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_guard(approved=True, hard_violations=None):
    guard = MagicMock()
    guard.validate_evolution = AsyncMock(
        return_value={"approved": approved, "violations": [] if approved else ["违规"]}
    )
    guard.validate_hard_constraints = MagicMock(return_value=hard_violations or [])
    return guard


class TestParseDiff:
    def test_parses_valid_diff(self, tmp_path):
        with patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", tmp_path / "backups"):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(MagicMock(), MagicMock())

        json_str = '{"diffs": [{"action": "add", "location": "", "content": "新内容", "description": "追加了内容"}], "summary": "小改动"}'
        result = engine._parse_diff(json_str)
        assert len(result["diffs"]) == 1
        assert result["summary"] == "小改动"

    def test_returns_empty_on_no_json(self, tmp_path):
        with patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", tmp_path / "backups"):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(MagicMock(), MagicMock())

        result = engine._parse_diff("没有 JSON 内容")
        assert result["diffs"] == []

    def test_returns_empty_on_malformed_json(self, tmp_path):
        with patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", tmp_path / "backups"):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(MagicMock(), MagicMock())

        result = engine._parse_diff('{"diffs": [broken')
        assert result["diffs"] == []


class TestApplyDiffs:
    def _make_engine(self, tmp_path):
        with patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", tmp_path / "backups"):
            from src.core.evolution_engine import EvolutionEngine
            return EvolutionEngine(MagicMock(), MagicMock())

    def test_add_at_location(self, tmp_path):
        engine = self._make_engine(tmp_path)
        soul = "## 性格\n安静温柔。\n## 兴趣\n读书。"
        diffs = [{"action": "add", "location": "## 兴趣\n读书。", "content": "\n喜欢摄影。", "description": "加了摄影"}]
        result = engine._apply_diffs(soul, diffs)
        assert "喜欢摄影" in result
        assert "读书" in result

    def test_add_at_end_when_location_not_found(self, tmp_path):
        engine = self._make_engine(tmp_path)
        soul = "## 性格\n安静。"
        diffs = [{"action": "add", "location": "不存在的位置", "content": "追加内容", "description": "追加"}]
        result = engine._apply_diffs(soul, diffs)
        assert "追加内容" in result

    def test_modify_existing(self, tmp_path):
        engine = self._make_engine(tmp_path)
        soul = "她非常内敛安静。"
        diffs = [{"action": "modify", "location": "非常内敛安静", "content": "内敛安静，偶尔有点话多", "description": "微调"}]
        result = engine._apply_diffs(soul, diffs)
        assert "内敛安静，偶尔有点话多" in result
        assert "非常内敛安静" not in result

    def test_remove_text(self, tmp_path):
        engine = self._make_engine(tmp_path)
        soul = "Lapwing 白发蓝眸。这句话要删掉。她很安静。"
        diffs = [{"action": "remove", "location": "这句话要删掉。", "content": "", "description": "删除"}]
        result = engine._apply_diffs(soul, diffs)
        assert "这句话要删掉" not in result
        assert "Lapwing 白发蓝眸" in result

    def test_noop_when_location_not_found_for_modify(self, tmp_path):
        engine = self._make_engine(tmp_path)
        soul = "原始内容。"
        diffs = [{"action": "modify", "location": "不存在", "content": "新内容", "description": "修改"}]
        result = engine._apply_diffs(soul, diffs)
        assert result == "原始内容。"


class TestEvolve:
    def _patch_paths(self, tmp_path):
        soul_path = tmp_path / "soul.md"
        soul_path.write_text("Lapwing 白发蓝眸，她安静温柔。", encoding="utf-8")
        rules_path = tmp_path / "rules.md"
        rules_path.write_text("- [2026-01-01] 不要主动问是否继续\n", encoding="utf-8")
        changelog_path = tmp_path / "changelog.md"
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        return soul_path, rules_path, changelog_path, journal_dir, backup_dir

    async def test_full_evolution_cycle(self, tmp_path):
        soul_path, rules_path, changelog_path, journal_dir, backup_dir = self._patch_paths(tmp_path)

        diff_response = '{"diffs": [{"action": "add", "location": "", "content": "她学会了倾听。", "description": "加了倾听"}], "summary": "微调了一处"}'
        router = MagicMock()
        router.complete = AsyncMock(return_value=diff_response)
        guard = _make_guard(approved=True)

        with patch("src.core.evolution_engine.SOUL_PATH", soul_path), \
             patch("src.core.evolution_engine.RULES_PATH", rules_path), \
             patch("src.core.evolution_engine.CHANGELOG_PATH", changelog_path), \
             patch("src.core.evolution_engine.JOURNAL_DIR", journal_dir), \
             patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", backup_dir), \
             patch("src.core.evolution_engine.load_prompt", return_value="prompt {current_soul} {rules} {recent_journals}"):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(router, guard)
            result = await engine.evolve()

        assert result["success"] is True
        assert "summary" in result
        assert soul_path.read_text(encoding="utf-8") != "Lapwing 白发蓝眸，她安静温柔。"
        assert changelog_path.exists()
        assert any(backup_dir.glob("soul_*.md"))

    async def test_rejects_when_constitution_fails(self, tmp_path):
        soul_path, rules_path, changelog_path, journal_dir, backup_dir = self._patch_paths(tmp_path)

        diff_response = '{"diffs": [{"action": "remove", "location": "白发", "content": "", "description": "删除"}], "summary": "删除了白发"}'
        router = MagicMock()
        router.complete = AsyncMock(return_value=diff_response)
        guard = _make_guard(approved=False)

        with patch("src.core.evolution_engine.SOUL_PATH", soul_path), \
             patch("src.core.evolution_engine.RULES_PATH", rules_path), \
             patch("src.core.evolution_engine.CHANGELOG_PATH", changelog_path), \
             patch("src.core.evolution_engine.JOURNAL_DIR", journal_dir), \
             patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", backup_dir), \
             patch("src.core.evolution_engine.load_prompt", return_value="prompt {current_soul} {rules} {recent_journals}"):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(router, guard)
            result = await engine.evolve()

        assert result["success"] is False
        assert "宪法校验未通过" in result["error"]

    async def test_rejects_when_too_many_diffs(self, tmp_path):
        soul_path, rules_path, changelog_path, journal_dir, backup_dir = self._patch_paths(tmp_path)

        diffs = [{"action": "add", "location": "", "content": f"内容{i}", "description": f"改动{i}"} for i in range(6)]
        diff_response = f'{{"diffs": {__import__("json").dumps(diffs)}, "summary": "太多了"}}'
        router = MagicMock()
        router.complete = AsyncMock(return_value=diff_response)
        guard = _make_guard(approved=True)

        with patch("src.core.evolution_engine.SOUL_PATH", soul_path), \
             patch("src.core.evolution_engine.RULES_PATH", rules_path), \
             patch("src.core.evolution_engine.CHANGELOG_PATH", changelog_path), \
             patch("src.core.evolution_engine.JOURNAL_DIR", journal_dir), \
             patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", backup_dir), \
             patch("src.core.evolution_engine.load_prompt", return_value="prompt {current_soul} {rules} {recent_journals}"):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(router, guard)
            result = await engine.evolve()

        assert result["success"] is False
        assert "超过宪法限制" in result["error"]

    async def test_skips_when_no_input_material(self, tmp_path):
        soul_path = tmp_path / "soul.md"
        soul_path.write_text("Lapwing 白发蓝眸。", encoding="utf-8")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("src.core.evolution_engine.SOUL_PATH", soul_path), \
             patch("src.core.evolution_engine.RULES_PATH", tmp_path / "nonexistent_rules.md"), \
             patch("src.core.evolution_engine.CHANGELOG_PATH", tmp_path / "changelog.md"), \
             patch("src.core.evolution_engine.JOURNAL_DIR", tmp_path / "nonexistent_journal"), \
             patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", backup_dir):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(MagicMock(), _make_guard())
            result = await engine.evolve()

        assert result["success"] is False
        assert "没有规则和日记" in result["error"]

    async def test_hard_constraint_failure_prevents_write(self, tmp_path):
        soul_path, rules_path, changelog_path, journal_dir, backup_dir = self._patch_paths(tmp_path)
        original_content = soul_path.read_text(encoding="utf-8")

        diff_response = '{"diffs": [{"action": "modify", "location": "Lapwing", "content": "Robot", "description": "改名"}], "summary": "改了名字"}'
        router = MagicMock()
        router.complete = AsyncMock(return_value=diff_response)
        # LLM approves but hard constraints catch it
        guard = MagicMock()
        guard.validate_evolution = AsyncMock(return_value={"approved": True, "violations": []})
        guard.validate_hard_constraints = MagicMock(return_value=["缺少核心身份标识 'Lapwing'"])

        with patch("src.core.evolution_engine.SOUL_PATH", soul_path), \
             patch("src.core.evolution_engine.RULES_PATH", rules_path), \
             patch("src.core.evolution_engine.CHANGELOG_PATH", changelog_path), \
             patch("src.core.evolution_engine.JOURNAL_DIR", journal_dir), \
             patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", backup_dir), \
             patch("src.core.evolution_engine.load_prompt", return_value="prompt {current_soul} {rules} {recent_journals}"):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(router, guard)
            result = await engine.evolve()

        assert result["success"] is False
        assert "硬约束" in result["error"]
        # Soul file should not have been modified
        assert soul_path.read_text(encoding="utf-8") == original_content


class TestRevert:
    async def test_reverts_to_latest_backup(self, tmp_path):
        soul_path = tmp_path / "soul.md"
        soul_path.write_text("Lapwing 当前版本。", encoding="utf-8")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        backup_file = backup_dir / "soul_20260101_120000.md"
        backup_file.write_text("Lapwing 旧版本 白发蓝眸。", encoding="utf-8")
        changelog_path = tmp_path / "changelog.md"

        with patch("src.core.evolution_engine.SOUL_PATH", soul_path), \
             patch("src.core.evolution_engine.CHANGELOG_PATH", changelog_path), \
             patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", backup_dir):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(MagicMock(), MagicMock())
            result = await engine.revert()

        assert result["success"] is True
        assert soul_path.read_text(encoding="utf-8") == "Lapwing 旧版本 白发蓝眸。"

    async def test_fails_when_no_backups(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("src.core.evolution_engine.SOUL_PATH", tmp_path / "soul.md"), \
             patch("src.core.evolution_engine.CHANGELOG_PATH", tmp_path / "changelog.md"), \
             patch("src.core.evolution_engine.DATA_DIR", tmp_path), \
             patch("src.core.evolution_engine._BACKUP_DIR", backup_dir):
            from src.core.evolution_engine import EvolutionEngine
            engine = EvolutionEngine(MagicMock(), MagicMock())
            result = await engine.revert()

        assert result["success"] is False
        assert "没有可用的备份" in result["error"]
