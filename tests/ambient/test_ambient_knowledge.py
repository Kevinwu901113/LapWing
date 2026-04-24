"""AmbientKnowledgeStore 单元测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.ambient.ambient_knowledge import AmbientKnowledgeStore
from src.ambient.models import AmbientEntry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _make_entry(
    key: str = "test:key",
    category: str = "test",
    topic: str = "测试条目",
    summary: str = "这是一条测试",
    expires_at: str | None = None,
    source: str = "test",
) -> AmbientEntry:
    return AmbientEntry(
        key=key,
        category=category,
        topic=topic,
        data="{}",
        summary=summary,
        fetched_at=_now_iso(),
        expires_at=expires_at or _future_iso(1),
        source=source,
        confidence=1.0,
    )


@pytest.fixture
async def store(tmp_path):
    s = AmbientKnowledgeStore(db_path=tmp_path / "ambient.db")
    await s.init()
    yield s
    await s.close()


# ── 基本 CRUD ───────────────────────────────────────────────────────

class TestCRUD:
    async def test_put_and_get(self, store):
        entry = _make_entry()
        await store.put("k1", entry)
        result = await store.get("k1")
        assert result is not None
        assert result.key == "k1"
        assert result.summary == "这是一条测试"

    async def test_get_missing_returns_none(self, store):
        result = await store.get("nonexistent")
        assert result is None

    async def test_delete(self, store):
        await store.put("k1", _make_entry())
        await store.delete("k1")
        result = await store.get("k1")
        assert result is None

    async def test_upsert(self, store):
        await store.put("k1", _make_entry(summary="v1"))
        await store.put("k1", _make_entry(summary="v2"))
        result = await store.get("k1")
        assert result is not None
        assert result.summary == "v2"


# ── TTL / 过期 ──────────────────────────────────────────────────────

class TestTTL:
    async def test_expired_entry_returns_none(self, store):
        entry = _make_entry(expires_at=_past_iso(1))
        await store.put("expired", entry)
        result = await store.get("expired")
        assert result is None

    async def test_get_if_fresh_alias(self, store):
        entry = _make_entry(expires_at=_past_iso(1))
        await store.put("expired", entry)
        result = await store.get_if_fresh("expired")
        assert result is None

    async def test_fresh_entry_returned(self, store):
        entry = _make_entry(expires_at=_future_iso(2))
        await store.put("fresh", entry)
        result = await store.get("fresh")
        assert result is not None


# ── 分类查询 ────────────────────────────────────────────────────────

class TestCategory:
    async def test_get_by_category(self, store):
        await store.put("w1", _make_entry(key="w1", category="weather"))
        await store.put("w2", _make_entry(key="w2", category="weather"))
        await store.put("s1", _make_entry(key="s1", category="sports"))
        results = await store.get_by_category("weather")
        assert len(results) == 2
        assert all(r.category == "weather" for r in results)

    async def test_get_by_category_excludes_expired(self, store):
        await store.put("fresh", _make_entry(
            key="fresh", category="news", expires_at=_future_iso(1),
        ))
        await store.put("stale", _make_entry(
            key="stale", category="news", expires_at=_past_iso(1),
        ))
        results = await store.get_by_category("news")
        assert len(results) == 1
        assert results[0].key == "fresh"


# ── get_all_fresh ───────────────────────────────────────────────────

class TestGetAllFresh:
    async def test_returns_only_fresh(self, store):
        await store.put("a", _make_entry(key="a", expires_at=_future_iso(1)))
        await store.put("b", _make_entry(key="b", expires_at=_past_iso(1)))
        await store.put("c", _make_entry(key="c", expires_at=_future_iso(2)))
        results = await store.get_all_fresh()
        keys = {r.key for r in results}
        assert "a" in keys
        assert "c" in keys
        assert "b" not in keys

    async def test_empty_store(self, store):
        results = await store.get_all_fresh()
        assert results == ()


# ── cleanup_expired ─────────────────────────────────────────────────

class TestCleanup:
    async def test_cleanup_removes_expired(self, store):
        await store.put("live", _make_entry(key="live", expires_at=_future_iso(1)))
        await store.put("dead", _make_entry(key="dead", expires_at=_past_iso(1)))
        count = await store.cleanup_expired()
        assert count == 1
        stats = await store.stats()
        assert stats["total"] == 1

    async def test_cleanup_returns_zero_if_none_expired(self, store):
        await store.put("live", _make_entry(key="live"))
        count = await store.cleanup_expired()
        assert count == 0


# ── LRU 驱逐 ───────────────────────────────────────────────────────

class TestLRUEviction:
    async def test_evicts_oldest_when_over_capacity(self, store):
        for i in range(51):
            await store.put(f"k{i:03d}", _make_entry(key=f"k{i:03d}"))
        stats = await store.stats()
        assert stats["total"] == 50

    async def test_accessed_entry_survives_eviction(self, store):
        # 先插入 50 条
        for i in range(50):
            await store.put(f"k{i:03d}", _make_entry(key=f"k{i:03d}"))
        # 访问第一条（更新 last_accessed_at）
        await store.get("k000")
        # 再插入一条触发驱逐
        await store.put("k050", _make_entry(key="k050"))
        # k000 因为刚访问过应该存活
        result = await store.get("k000")
        assert result is not None


# ── stats ───────────────────────────────────────────────────────────

class TestStats:
    async def test_stats(self, store):
        await store.put("a", _make_entry(key="a", expires_at=_future_iso(1)))
        await store.put("b", _make_entry(key="b", expires_at=_past_iso(1)))
        stats = await store.stats()
        assert stats["total"] == 2
        assert stats["fresh"] == 1


# ── 并发安全 ────────────────────────────────────────────────────────

class TestConcurrency:
    async def test_concurrent_writes(self, store):
        async def writer(n):
            await store.put(f"c{n}", _make_entry(key=f"c{n}"))

        await asyncio.gather(*(writer(i) for i in range(20)))
        stats = await store.stats()
        assert stats["total"] == 20
