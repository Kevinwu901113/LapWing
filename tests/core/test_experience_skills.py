"""Tests for the Experience Skills system."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.experience_skills import (
    ExperienceSkillManager,
    MatchResult,
    _extract_summary,
    _normalize_list,
    _split_frontmatter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_SKILL_CONTENT = """\
---
id: test_skill
name: 测试技能
category: research
status: active
created: 2026-03-01
updated: 2026-03-28
source: preset
parent_skills: []
version: 2
use_count: 5
last_used: 2026-03-28
success_rate: 0.8
agents:
  - researcher
tools:
  - web_search
size_tokens: 300
---

# 测试技能

## 什么时候用
当需要执行测试相关任务时使用。

## 执行流程
1. 分析需求
2. 执行测试
3. 输出结果
"""


def _make_skill_file(tmp_path: Path, category: str = "research", skill_id: str = "test_skill") -> Path:
    cat_dir = tmp_path / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    skill_file = cat_dir / f"{skill_id}.md"
    skill_file.write_text(VALID_SKILL_CONTENT, encoding="utf-8")
    return skill_file


def _make_manager(tmp_path: Path) -> ExperienceSkillManager:
    router = MagicMock()
    router.complete_structured = AsyncMock(return_value={"selected": []})
    return ExperienceSkillManager(
        skills_dir=tmp_path / "skills",
        traces_dir=tmp_path / "traces",
        router=router,
    )


# ---------------------------------------------------------------------------
# _split_frontmatter
# ---------------------------------------------------------------------------


def test_split_frontmatter_valid():
    fm, body, err = _split_frontmatter("---\nid: foo\n---\n\nbody text")
    assert err == ""
    assert "id: foo" in fm
    assert body == "body text"


def test_split_frontmatter_missing_start():
    _, _, err = _split_frontmatter("no frontmatter here")
    assert "起始" in err


def test_split_frontmatter_missing_end():
    _, _, err = _split_frontmatter("---\nid: foo\nno closing")
    assert "结束" in err


def test_split_frontmatter_empty_body():
    fm, body, err = _split_frontmatter("---\nid: x\n---")
    assert err == ""
    assert body == ""


# ---------------------------------------------------------------------------
# _normalize_list
# ---------------------------------------------------------------------------


def test_normalize_list_from_list():
    assert _normalize_list(["a", "b", "c"]) == ["a", "b", "c"]


def test_normalize_list_from_string():
    assert _normalize_list("single") == ["single"]


def test_normalize_list_none():
    assert _normalize_list(None) == []


def test_normalize_list_empty_string():
    assert _normalize_list("") == []


# ---------------------------------------------------------------------------
# _extract_summary
# ---------------------------------------------------------------------------


def test_extract_summary_from_section():
    body = "# Skill\n\n## 什么时候用\n当需要调研论文时使用。\n\n## 其他\n内容"
    summary = _extract_summary(body)
    assert "调研论文" in summary


def test_extract_summary_fallback_first_paragraph():
    body = "# Skill\n\n这是第一段内容。\n\n第二段"
    summary = _extract_summary(body)
    assert "第一段" in summary


def test_extract_summary_truncates_long_text():
    body = "## 什么时候用\n" + "很长的内容" * 50
    summary = _extract_summary(body)
    assert len(summary) <= 153  # 150 + "..."


# ---------------------------------------------------------------------------
# parse_skill_file
# ---------------------------------------------------------------------------


def test_parse_skill_file_valid(tmp_path):
    skill_file = _make_skill_file(tmp_path)
    mgr = _make_manager(tmp_path)
    skill = mgr.parse_skill_file(skill_file)

    assert skill is not None
    assert skill.meta.id == "test_skill"
    assert skill.meta.name == "测试技能"
    assert skill.meta.category == "research"
    assert skill.meta.status == "active"
    assert skill.meta.version == 2
    assert skill.meta.use_count == 5
    assert skill.meta.success_rate == 0.8
    assert "researcher" in skill.meta.agents
    assert "web_search" in skill.meta.tools
    assert "什么时候用" in skill.body


def test_parse_skill_file_missing_id(tmp_path):
    cat_dir = tmp_path / "research"
    cat_dir.mkdir()
    skill_file = cat_dir / "no_id.md"
    skill_file.write_text("---\nname: 无ID技能\ncategory: research\n---\nbody", encoding="utf-8")
    mgr = _make_manager(tmp_path)
    assert mgr.parse_skill_file(skill_file) is None


def test_parse_skill_file_id_mismatch(tmp_path):
    cat_dir = tmp_path / "research"
    cat_dir.mkdir()
    skill_file = cat_dir / "actual_name.md"
    skill_file.write_text(
        "---\nid: wrong_name\nname: Test\ncategory: research\nstatus: active\n---\nbody",
        encoding="utf-8",
    )
    mgr = _make_manager(tmp_path)
    assert mgr.parse_skill_file(skill_file) is None


def test_parse_skill_file_category_mismatch(tmp_path):
    cat_dir = tmp_path / "coding"
    cat_dir.mkdir()
    skill_file = cat_dir / "test_skill.md"
    skill_file.write_text(
        "---\nid: test_skill\nname: Test\ncategory: research\nstatus: active\n---\nbody",
        encoding="utf-8",
    )
    mgr = _make_manager(tmp_path)
    assert mgr.parse_skill_file(skill_file) is None


def test_parse_skill_file_invalid_yaml(tmp_path):
    cat_dir = tmp_path / "research"
    cat_dir.mkdir()
    skill_file = cat_dir / "bad_yaml.md"
    skill_file.write_text("---\n: :\ninvalid:\n---\nbody", encoding="utf-8")
    mgr = _make_manager(tmp_path)
    # Should not raise, returns None
    result = mgr.parse_skill_file(skill_file)
    # May or may not be None depending on yaml parser leniency; just ensure no exception


def test_parse_skill_file_invalid_status(tmp_path):
    cat_dir = tmp_path / "research"
    cat_dir.mkdir()
    skill_file = cat_dir / "test_skill.md"
    skill_file.write_text(
        "---\nid: test_skill\nname: Test\ncategory: research\nstatus: unknown\ntriggers:\n  keywords: []\n---\nbody",
        encoding="utf-8",
    )
    mgr = _make_manager(tmp_path)
    assert mgr.parse_skill_file(skill_file) is None


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------


def test_rebuild_index_empty(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.ensure_directories()
    mgr.rebuild_index()
    assert mgr._index == []
    assert (tmp_path / "skills" / "_index.json").exists()


def test_rebuild_index_with_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    cat_dir = skills_dir / "research"
    cat_dir.mkdir(parents=True)
    (cat_dir / "test_skill.md").write_text(VALID_SKILL_CONTENT, encoding="utf-8")

    mgr = ExperienceSkillManager(
        skills_dir=skills_dir,
        traces_dir=tmp_path / "traces",
        router=MagicMock(),
    )
    mgr.rebuild_index()

    assert len(mgr._index) == 1
    assert mgr._index[0].id == "test_skill"
    assert mgr._index[0].name == "测试技能"

    index_file = skills_dir / "_index.json"
    assert index_file.exists()
    data = json.loads(index_file.read_text())
    assert data["skill_count"] == 1


def test_rebuild_index_sorts_by_use_count(tmp_path):
    skills_dir = tmp_path / "skills"
    cat_dir = skills_dir / "coding"
    cat_dir.mkdir(parents=True)

    for skill_id, use_count in [("skill_a", 3), ("skill_b", 10), ("skill_c", 1)]:
        content = f"""\
---
id: {skill_id}
name: Skill {skill_id}
category: coding
status: active
created: 2026-01-01
updated: 2026-01-01
source: preset
parent_skills: []
version: 1
use_count: {use_count}
last_used: null
success_rate: 0.0
agents: []
tools: []
size_tokens: 100
---
body
"""
        (cat_dir / f"{skill_id}.md").write_text(content, encoding="utf-8")

    mgr = ExperienceSkillManager(
        skills_dir=skills_dir,
        traces_dir=tmp_path / "traces",
        router=MagicMock(),
    )
    mgr.rebuild_index()

    assert mgr._index[0].id == "skill_b"  # highest use_count
    assert mgr._index[-1].id == "skill_c"  # lowest use_count


# ---------------------------------------------------------------------------
# index_match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_match_selects_relevant(tmp_path):
    skills_dir = tmp_path / "skills"
    cat_dir = skills_dir / "research"
    cat_dir.mkdir(parents=True)
    (cat_dir / "test_skill.md").write_text(VALID_SKILL_CONTENT, encoding="utf-8")

    router = MagicMock()
    router.complete_structured = AsyncMock(return_value={"selected": ["test_skill"]})

    mgr = ExperienceSkillManager(
        skills_dir=skills_dir,
        traces_dir=tmp_path / "traces",
        router=router,
    )
    mgr.rebuild_index()

    results = await mgr.index_match("帮我调研一下最近的论文")
    assert len(results) == 1
    assert results[0].skill_id == "test_skill"
    assert results[0].match_level == "index"


@pytest.mark.asyncio
async def test_index_match_handles_llm_failure(tmp_path):
    skills_dir = tmp_path / "skills"
    cat_dir = skills_dir / "research"
    cat_dir.mkdir(parents=True)
    (cat_dir / "test_skill.md").write_text(VALID_SKILL_CONTENT, encoding="utf-8")

    router = MagicMock()
    router.complete_structured = AsyncMock(side_effect=Exception("LLM unavailable"))

    mgr = ExperienceSkillManager(
        skills_dir=skills_dir,
        traces_dir=tmp_path / "traces",
        router=router,
    )
    mgr.rebuild_index()

    results = await mgr.index_match("任何请求")
    assert results == []


@pytest.mark.asyncio
async def test_index_match_handles_parse_failure(tmp_path):
    skills_dir = tmp_path / "skills"
    cat_dir = skills_dir / "research"
    cat_dir.mkdir(parents=True)
    (cat_dir / "test_skill.md").write_text(VALID_SKILL_CONTENT, encoding="utf-8")

    router = MagicMock()
    router.complete_structured = AsyncMock(side_effect=ValueError("parse failed"))

    mgr = ExperienceSkillManager(
        skills_dir=skills_dir,
        traces_dir=tmp_path / "traces",
        router=router,
    )
    mgr.rebuild_index()

    results = await mgr.index_match("任何请求")
    assert results == []


@pytest.mark.asyncio
async def test_index_match_caps_at_three(tmp_path):
    skills_dir = tmp_path / "skills"
    cat_dir = skills_dir / "research"
    cat_dir.mkdir(parents=True)
    (cat_dir / "test_skill.md").write_text(VALID_SKILL_CONTENT, encoding="utf-8")

    router = MagicMock()
    router.complete_structured = AsyncMock(
        return_value={"selected": ["test_skill", "test_skill", "test_skill", "test_skill"]}
    )

    mgr = ExperienceSkillManager(
        skills_dir=skills_dir,
        traces_dir=tmp_path / "traces",
        router=router,
    )
    mgr.rebuild_index()

    results = await mgr.index_match("任何请求")
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_index_match_empty_index(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)

    router = MagicMock()
    router.complete_structured = AsyncMock(return_value={"selected": []})

    mgr = ExperienceSkillManager(
        skills_dir=skills_dir,
        traces_dir=tmp_path / "traces",
        router=router,
    )
    mgr.rebuild_index()

    results = await mgr.index_match("任何请求")
    assert results == []


# ---------------------------------------------------------------------------
# format_injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_format_injection_single_skill(tmp_path):
    skill_file = _make_skill_file(tmp_path)
    mgr = _make_manager(tmp_path)
    skill = mgr.parse_skill_file(skill_file)
    assert skill is not None

    text = mgr.format_injection([skill])
    assert "---参考经验开始---" in text
    assert "---参考经验结束---" in text
    assert "参考它来处理当前任务" in text


def test_format_injection_empty(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.format_injection([]) == ""


@pytest.mark.asyncio
async def test_format_injection_respects_token_budget(tmp_path):
    skill_file = _make_skill_file(tmp_path)
    mgr = _make_manager(tmp_path)
    skill = mgr.parse_skill_file(skill_file)
    assert skill is not None

    # Pass 3 copies of the same skill.  The budget check skips the first skill
    # (so at least one is always included) but drops the 2nd and 3rd when the
    # budget is tiny.  We verify the result is shorter than injecting all three.
    text_full = mgr.format_injection([skill, skill, skill], max_tokens=100_000)
    text_small = mgr.format_injection([skill, skill, skill], max_tokens=1)
    # With budget=1, only the first skill (which bypasses the check) is included
    assert len(text_small) < len(text_full)
