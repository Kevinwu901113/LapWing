"""SemanticStore unit tests."""

from __future__ import annotations

import pytest

from src.memory.semantic_store import SemanticStore
from src.memory.vector_store import MemoryVectorStore


@pytest.fixture
def vector_store(tmp_path):
    return MemoryVectorStore(persist_dir=str(tmp_path / "chroma"))


@pytest.fixture
def semantic(tmp_path, vector_store):
    return SemanticStore(
        memory_dir=tmp_path / "semantic",
        vector_store=vector_store,
        dedup_threshold=0.95,  # leave room for near-dupes in tests
    )


class TestAddFact:
    async def test_writes_category_file(self, semantic, tmp_path):
        entry = await semantic.add_fact(
            category="kevin",
            content="Kevin 每天早上喝手冲咖啡",
            source_episodes=["ep_20260417_090000_ab"],
        )
        path = tmp_path / "semantic" / "kevin.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "手冲咖啡" in text
        assert entry is not None
        assert entry.fact_id.startswith("sem_")
        assert entry.category == "kevin"

    async def test_appends_to_existing_category(self, semantic, tmp_path):
        await semantic.add_fact(category="kevin", content="喜欢手冲咖啡")
        await semantic.add_fact(category="kevin", content="工作习惯夜猫子")
        text = (tmp_path / "semantic" / "kevin.md").read_text(encoding="utf-8")
        assert text.count("## ") == 2

    async def test_different_categories_split_files(self, semantic, tmp_path):
        await semantic.add_fact(category="kevin", content="fact about kevin")
        await semantic.add_fact(category="world", content="fact about world")
        assert (tmp_path / "semantic" / "kevin.md").exists()
        assert (tmp_path / "semantic" / "world.md").exists()

    async def test_slugifies_category(self, semantic, tmp_path):
        entry = await semantic.add_fact(
            category="Kevin's Preferences!",
            content="placeholder",
        )
        assert entry is not None
        # Slug strips special chars to underscore, lowercases
        assert "_" in entry.category
        assert "!" not in entry.category

    async def test_source_episodes_round_trip(self, semantic):
        await semantic.add_fact(
            category="kevin",
            content="值得保留的事实",
            source_episodes=["ep_1", "ep_2"],
        )
        hits = await semantic.query("保留的事实", top_k=3)
        assert hits
        assert hits[0].source_episodes == ("ep_1", "ep_2")

    async def test_rejects_empty_content(self, semantic):
        with pytest.raises(ValueError):
            await semantic.add_fact(category="kevin", content="  ")


class TestDedup:
    async def test_exact_duplicate_skipped(self, tmp_path, vector_store):
        # Lower threshold to make test deterministic — all-MiniLM scores
        # near-identical strings very high.
        store = SemanticStore(
            memory_dir=tmp_path / "semantic",
            vector_store=vector_store,
            dedup_threshold=0.9,
        )
        first = await store.add_fact(
            category="kevin", content="Kevin 喜欢道奇",
        )
        second = await store.add_fact(
            category="kevin", content="Kevin 喜欢道奇",
        )
        assert first is not None
        assert second is None  # dedup'd

    async def test_distinct_facts_both_written(self, semantic):
        first = await semantic.add_fact(
            category="kevin", content="Kevin 在台北工作",
        )
        second = await semantic.add_fact(
            category="kevin", content="世界杯决赛是巴西对阿根廷",
        )
        assert first is not None
        assert second is not None

    async def test_high_threshold_admits_near_dupes(self, tmp_path, vector_store):
        store = SemanticStore(
            memory_dir=tmp_path / "semantic",
            vector_store=vector_store,
            dedup_threshold=0.99,  # impossibly strict
        )
        first = await store.add_fact(
            category="kevin", content="Kevin 喜欢咖啡",
        )
        # Paraphrase: similar but not identical.
        second = await store.add_fact(
            category="kevin", content="Kevin 每天喝咖啡的习惯",
        )
        assert first is not None
        assert second is not None  # admitted at 0.99


class TestQuery:
    async def test_query_returns_semantic_only(self, semantic, vector_store):
        await vector_store.add(
            note_id="note_x",
            content="Kevin 喜欢咖啡 — 手写笔记",
            metadata={
                "note_type": "observation",
                "trust": "self",
                "created_at": "2026-04-15T10:00:00+08:00",
                "file_path": "x.md",
            },
        )
        await semantic.add_fact(category="kevin", content="Kevin 喜欢咖啡")

        hits = await semantic.query("咖啡", top_k=10)
        assert hits
        assert all(h.fact_id.startswith("sem_") for h in hits)

    async def test_empty_text(self, semantic):
        await semantic.add_fact(category="kevin", content="有内容")
        assert await semantic.query("") == []
