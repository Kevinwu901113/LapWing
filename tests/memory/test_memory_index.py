"""tests/memory/test_memory_index.py — MemoryIndex 单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.memory.memory_index import MemoryIndex


@pytest.fixture
def index(tmp_path):
    return MemoryIndex(path=tmp_path / "_index.json")


def _make_entry(index: MemoryIndex, **kwargs) -> str:
    defaults = dict(
        category="kevin_fact",
        source_file="kevin_fact/2026-04.md",
        content_preview="Kevin 喜欢中文",
        importance=3,
    )
    defaults.update(kwargs)
    return index.add_entry(**defaults)


# ── 基本 CRUD ─────────────────────────────────────────────────────────────────

def test_add_and_get_entry(index):
    mem_id = _make_entry(index)
    entry = index.get_entry(mem_id)
    assert entry is not None
    assert entry["category"] == "kevin_fact"
    assert entry["content_preview"] == "Kevin 喜欢中文"
    assert entry["importance"] == 3


def test_update_referenced_increments(index):
    mem_id = _make_entry(index)
    index.update_referenced(mem_id)
    index.update_referenced(mem_id)
    entry = index.get_entry(mem_id)
    assert entry["reference_count"] == 2


def test_find_by_content_dedup(index):
    _make_entry(index, content_preview="Kevin 喜欢 Python")
    found = index.find_by_content("Kevin 喜欢 Python")
    assert found is not None
    assert found["content_preview"] == "Kevin 喜欢 Python"
    # 截断到 200 字符
    not_found = index.find_by_content("不存在的内容")
    assert not_found is None


def test_remove_entry(index):
    mem_id = _make_entry(index)
    assert index.remove_entry(mem_id) is True
    assert index.get_entry(mem_id) is None
    assert index.remove_entry(mem_id) is False  # 已不存在


def test_remove_by_source_file(index):
    _make_entry(index, source_file="kevin_fact/2026-04.md", content_preview="条目 A")
    _make_entry(index, source_file="kevin_fact/2026-04.md", content_preview="条目 B")
    _make_entry(index, source_file="knowledge/2026-04.md", content_preview="条目 C")

    removed = index.remove_by_source_file("kevin_fact/2026-04.md")
    assert len(removed) == 2
    assert len(index.find_by_source_file("kevin_fact/2026-04.md")) == 0
    assert len(index.find_by_source_file("knowledge/2026-04.md")) == 1


# ── 重要性计算 ─────────────────────────────────────────────────────────────────

def test_compute_importance_recency_decay(index):
    mem_id = _make_entry(index, category="kevin_fact", importance=5)
    entry = index.get_entry(mem_id)
    score_fresh = index.compute_importance(entry)

    # 模拟 200 天前的条目（超过 180 天衰减窗口 → recency 降低）
    entry_old = dict(entry)
    entry_old["created_at"] = (datetime.now() - timedelta(days=200)).isoformat()
    score_old = index.compute_importance(entry_old)

    assert score_fresh > score_old


def test_compute_importance_decision_slower_decay(index):
    """decision 类衰减窗口 360 天，应比 kevin_fact (180 天) 衰减慢。"""
    base_entry = {
        "created_at": (datetime.now() - timedelta(days=180)).isoformat(),
        "last_referenced": datetime.now().isoformat(),
        "importance": 3,
        "reference_count": 0,
    }
    entry_fact = dict(base_entry, category="kevin_fact")
    entry_decision = dict(base_entry, category="decision")

    score_fact = index.compute_importance(entry_fact)
    score_decision = index.compute_importance(entry_decision)
    assert score_decision > score_fact


# ── 排序 ──────────────────────────────────────────────────────────────────────

def test_ranked_entries_order(index):
    _make_entry(index, content_preview="低重要性", importance=1)
    _make_entry(index, content_preview="高重要性", importance=5)
    _make_entry(index, content_preview="中等重要性", importance=3)

    ranked = index.ranked_entries(limit=10)
    assert ranked[0]["content_preview"] == "高重要性"
    assert ranked[-1]["content_preview"] == "低重要性"


# ── 归档 ──────────────────────────────────────────────────────────────────────

def test_archive_stale_respects_exempt_categories(index):
    """correction 和 decision 永不归档。"""
    m1 = _make_entry(index, category="correction", importance=1)
    m2 = _make_entry(index, category="decision", importance=1)

    # 强制设旧时间
    for mem_id in (m1, m2):
        entry = index._data["entries"][mem_id]
        entry["last_referenced"] = (datetime.now() - timedelta(days=200)).isoformat()
    index._save()

    archived = index.archive_stale(max_age_days=90, min_importance=0.9)
    assert m1 not in archived
    assert m2 not in archived


def test_archive_stale_preserves_important(index):
    """重要性高的条目不归档。"""
    mem_id = _make_entry(index, category="knowledge", importance=5)
    entry = index._data["entries"][mem_id]
    entry["last_referenced"] = (datetime.now() - timedelta(days=200)).isoformat()
    index._save()

    archived = index.archive_stale(max_age_days=90, min_importance=0.2)
    assert mem_id not in archived


def test_archive_stale_marks_old_entries(index):
    """过旧且低重要性的普通条目应被归档。"""
    mem_id = _make_entry(index, category="kevin_fact", importance=1)
    entry = index._data["entries"][mem_id]
    entry["last_referenced"] = (datetime.now() - timedelta(days=200)).isoformat()
    entry["created_at"] = (datetime.now() - timedelta(days=200)).isoformat()
    index._save()

    archived = index.archive_stale(max_age_days=90, min_importance=0.9)
    assert mem_id in archived
    assert index.get_entry(mem_id)["archived"] is True


# ── 健康评分 ───────────────────────────────────────────────────────────────────

def test_health_score_empty(index):
    h = index.health_score()
    assert h["score"] == 0
    assert h["total"] == 0


def test_health_score_calculation(index):
    # 新鲜度：最近被引用
    m1 = _make_entry(index, category="kevin_fact", importance=3)
    index.update_referenced(m1)

    # 覆盖率：多个 category 在近 14 天内有新条目
    _make_entry(index, category="decision", importance=3)
    _make_entry(index, category="knowledge", importance=3)

    h = index.health_score()
    assert h["total"] == 3
    assert 0 <= h["score"] <= 100
    assert "freshness" in h["dimensions"]
    assert "coverage" in h["dimensions"]


# ── 持久化 ────────────────────────────────────────────────────────────────────

def test_persistence(tmp_path):
    """写入后重新加载，数据一致。"""
    path = tmp_path / "_index.json"
    idx1 = MemoryIndex(path=path)
    mem_id = idx1.add_entry(
        category="kevin_fact",
        source_file="kevin_fact/2026-04.md",
        content_preview="持久化测试",
        importance=4,
    )

    idx2 = MemoryIndex(path=path)
    entry = idx2.get_entry(mem_id)
    assert entry is not None
    assert entry["content_preview"] == "持久化测试"
    assert entry["importance"] == 4
