"""Tests for SkillRegistryManager."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.core.skill_registry import SkillRegistryManager


def _make_registry(tmp_path: Path) -> SkillRegistryManager:
    return SkillRegistryManager(tmp_path / "skills" / "_registry.json")


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def test_load_creates_default_when_missing(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    assert reg._data["total_executions"] == 0
    assert reg._data["total_with_skill"] == 0
    assert reg._data["skill_match_rate"] == 0.0


def test_load_reads_existing_file(tmp_path):
    reg_path = tmp_path / "skills" / "_registry.json"
    reg_path.parent.mkdir(parents=True)
    reg_path.write_text(
        json.dumps({"total_executions": 42, "total_with_skill": 10, "total_without_skill": 32,
                    "skill_match_rate": 0.24, "match_level_distribution": {},
                    "daily_stats": [], "recent_matches": []}),
        encoding="utf-8",
    )

    reg = SkillRegistryManager(reg_path)
    reg.load()
    assert reg._data["total_executions"] == 42


def test_save_writes_file(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg._data["total_executions"] = 99
    reg.save()

    reg2 = _make_registry(tmp_path)
    reg2.load()
    assert reg2._data["total_executions"] == 99


# ---------------------------------------------------------------------------
# record_execution — totals
# ---------------------------------------------------------------------------


def test_record_execution_increments_total(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id=None, match_level=None)
    assert reg._data["total_executions"] == 1
    assert reg._data["total_without_skill"] == 1
    assert reg._data["total_with_skill"] == 0


def test_record_execution_with_skill(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id="my_skill", match_level="quick")
    assert reg._data["total_executions"] == 1
    assert reg._data["total_with_skill"] == 1
    assert reg._data["total_without_skill"] == 0


def test_record_execution_match_rate(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id="skill_a", match_level="quick")
    reg.record_execution(skill_id=None, match_level=None)
    assert reg._data["total_executions"] == 2
    assert reg._data["skill_match_rate"] == 0.5


# ---------------------------------------------------------------------------
# record_execution — match_level_distribution
# ---------------------------------------------------------------------------


def test_match_level_distribution_quick_maps_to_index(tmp_path):
    """quick is mapped to index since quick_match was removed."""
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id="s", match_level="quick")
    assert reg._data["match_level_distribution"]["index"] == 1
    assert "quick" not in reg._data["match_level_distribution"]


def test_match_level_distribution_index(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id="s", match_level="index")
    assert reg._data["match_level_distribution"]["index"] == 1


def test_match_level_distribution_none(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id=None, match_level=None)
    assert reg._data["match_level_distribution"]["none"] == 1


# ---------------------------------------------------------------------------
# record_execution — daily_stats
# ---------------------------------------------------------------------------


def test_daily_stats_appended(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id="s", match_level="quick")

    today = date.today().isoformat()
    daily = reg._data["daily_stats"]
    assert len(daily) == 1
    assert daily[0]["date"] == today
    assert daily[0]["executions"] == 1
    assert daily[0]["with_skill"] == 1


def test_daily_stats_accumulates_same_day(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id="s", match_level="quick")
    reg.record_execution(skill_id=None, match_level=None)

    daily = reg._data["daily_stats"]
    assert len(daily) == 1  # same day
    assert daily[0]["executions"] == 2


# ---------------------------------------------------------------------------
# record_execution — recent_matches
# ---------------------------------------------------------------------------


def test_recent_matches_appended_for_skill(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id="literature_survey", match_level="quick", request_summary="调研论文")

    recent = reg._data["recent_matches"]
    assert len(recent) == 1
    assert recent[0]["skill_id"] == "literature_survey"
    assert recent[0]["match_level"] == "index"  # quick maps to index
    assert "调研论文" in recent[0]["request_summary"]


def test_recent_matches_not_appended_for_no_skill(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.record_execution(skill_id=None, match_level=None)
    assert reg._data["recent_matches"] == []


def test_recent_matches_capped_at_50(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    for i in range(60):
        reg.record_execution(skill_id=f"skill_{i}", match_level="quick")
    assert len(reg._data["recent_matches"]) == 50


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


def test_get_stats_returns_dict(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    stats = reg.get_stats()
    assert isinstance(stats, dict)
    assert "total_executions" in stats
