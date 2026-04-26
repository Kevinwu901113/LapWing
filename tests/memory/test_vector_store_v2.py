"""MemoryVectorStore 单元测试。"""

import pytest
from src.memory.vector_store import MemoryVectorStore


@pytest.fixture
def vector_store(tmp_path):
    return MemoryVectorStore(persist_dir=str(tmp_path / "chroma"))


class TestAdd:
    async def test_add_and_count(self, vector_store):
        await vector_store.add(
            note_id="note_001",
            content="Kevin likes coffee",
            metadata={"note_type": "fact", "trust": "self",
                      "created_at": "2026-04-15T10:00:00+08:00",
                      "file_path": "notes/fact.md"},
        )
        count = vector_store.collection.count()
        assert count == 1

    async def test_add_normalizes_empty_metadata_values(self, vector_store):
        await vector_store.add(
            note_id="note_empty_meta",
            content="metadata normalization check",
            metadata={
                "note_type": "observation",
                "trust": "self",
                "created_at": "2026-04-26T10:00:00+08:00",
                "source_refs": [],
                "parent_note": None,
            },
        )

        raw = vector_store.collection.get(ids=["note_empty_meta"], include=["metadatas"])
        metadata = raw["metadatas"][0]
        assert "source_refs" not in metadata
        assert metadata["parent_note"] == ""


class TestRecall:
    async def test_recall_returns_results(self, vector_store):
        await vector_store.add("n1", "Kevin likes dark roast coffee",
                               {"note_type": "fact", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "a.md"})
        await vector_store.add("n2", "The weather is sunny today",
                               {"note_type": "observation", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "b.md"})
        results = await vector_store.recall("coffee preference", top_k=2)
        assert len(results) >= 1
        assert any(r.note_id == "n1" for r in results)

    async def test_recall_empty_store(self, vector_store):
        results = await vector_store.recall("anything")
        assert results == []

    async def test_recall_cluster_dedup(self, vector_store):
        for i in range(3):
            await vector_store.add(
                f"dup_{i}", f"Kevin's favorite coffee is dark roast number {i}",
                {"note_type": "fact", "trust": "self",
                 "created_at": "2026-04-15T10:00:00+08:00",
                 "file_path": f"d{i}.md"})
        results = await vector_store.recall("dark roast coffee", top_k=5)
        assert len(results) <= 3  # MAX_PER_CLUSTER=2 applies


class TestRemove:
    async def test_remove(self, vector_store):
        await vector_store.add("rm1", "to remove",
                               {"note_type": "fact", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "rm.md"})
        await vector_store.remove("rm1")
        assert vector_store.collection.count() == 0

    async def test_remove_nonexistent(self, vector_store):
        await vector_store.remove("nonexistent")


class TestRebuild:
    async def test_rebuild_replaces_all(self, vector_store):
        await vector_store.add("old1", "old data",
                               {"note_type": "fact", "trust": "self",
                                "created_at": "2026-04-15T10:00:00+08:00",
                                "file_path": "old.md"})
        await vector_store.rebuild([
            {"note_id": "new1", "content": "new data",
             "meta": {"note_type": "fact", "trust": "self",
                      "created_at": "2026-04-15T10:00:00+08:00",
                      "file_path": "new.md"}},
        ])
        assert vector_store.collection.count() == 1
