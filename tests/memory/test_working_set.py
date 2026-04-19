"""WorkingSet unit tests."""

from __future__ import annotations

import pytest

from src.memory.episodic_store import EpisodicStore
from src.memory.semantic_store import SemanticStore
from src.memory.vector_store import MemoryVectorStore
from src.memory.working_set import WorkingSet


@pytest.fixture
def vector_store(tmp_path):
    return MemoryVectorStore(persist_dir=str(tmp_path / "chroma"))


@pytest.fixture
def episodic(tmp_path, vector_store):
    return EpisodicStore(
        memory_dir=tmp_path / "episodic",
        vector_store=vector_store,
    )


@pytest.fixture
def semantic(tmp_path, vector_store):
    return SemanticStore(
        memory_dir=tmp_path / "semantic",
        vector_store=vector_store,
        dedup_threshold=0.99,  # tests work without dedup interference
    )


class TestRetrieve:
    async def test_merges_both_layers(self, episodic, semantic):
        await episodic.add_episode(summary="Kevin 问了道奇的比赛")
        await semantic.add_fact(category="kevin", content="Kevin 喜欢道奇")
        ws = WorkingSet(episodic_store=episodic, semantic_store=semantic)

        snippets = await ws.retrieve("道奇", top_k=10)
        ids = [s.note_id for s in snippets.snippets]
        assert any(i.startswith("ep_") for i in ids)
        assert any(i.startswith("sem_") for i in ids)

    async def test_content_carries_layer_prefix(self, episodic, semantic):
        await episodic.add_episode(summary="今天和 Kevin 聊到新的论文")
        await semantic.add_fact(category="kevin", content="Kevin 在读研究生")
        ws = WorkingSet(episodic_store=episodic, semantic_store=semantic)

        snippets = await ws.retrieve("Kevin", top_k=10)
        contents = [s.content for s in snippets.snippets]
        assert any(c.startswith("[情景") for c in contents)
        assert any(c.startswith("[知识") for c in contents)

    async def test_sorted_by_score_desc(self, episodic, semantic):
        await episodic.add_episode(summary="道奇直接命中的记录")
        await episodic.add_episode(summary="无关的散文")
        await semantic.add_fact(
            category="kevin", content="Kevin 喜欢道奇比赛",
        )
        ws = WorkingSet(episodic_store=episodic, semantic_store=semantic)

        snippets = await ws.retrieve("道奇", top_k=10)
        scores = [s.score for s in snippets.snippets]
        assert scores == sorted(scores, reverse=True)

    async def test_empty_query(self, episodic, semantic):
        ws = WorkingSet(episodic_store=episodic, semantic_store=semantic)
        snippets = await ws.retrieve("")
        assert snippets.snippets == ()

    async def test_both_stores_none(self):
        ws = WorkingSet()
        snippets = await ws.retrieve("anything")
        assert snippets.snippets == ()

    async def test_one_store_none(self, episodic):
        await episodic.add_episode(summary="只有情景，没有语义")
        ws = WorkingSet(episodic_store=episodic, semantic_store=None)
        snippets = await ws.retrieve("情景", top_k=5)
        assert snippets.snippets
        assert all(s.note_id.startswith("ep_") for s in snippets.snippets)

    async def test_top_k_budget_enforced(self, episodic):
        for i in range(20):
            await episodic.add_episode(
                summary=f"独立事件 {i}: 关于 topic_search 的不同实例",
            )
        ws = WorkingSet(episodic_store=episodic)
        snippets = await ws.retrieve("topic_search", top_k=3)
        assert len(snippets.snippets) <= 3

    async def test_store_failure_does_not_break_other(
        self, episodic, semantic, monkeypatch,
    ):
        await episodic.add_episode(summary="working episodic 条目")
        await semantic.add_fact(category="kevin", content="working semantic 条目")

        # Sabotage episodic.query
        async def boom(*_a, **_k):
            raise RuntimeError("chromadb down")

        monkeypatch.setattr(episodic, "query", boom)

        ws = WorkingSet(episodic_store=episodic, semantic_store=semantic)
        snippets = await ws.retrieve("条目", top_k=5)
        # Semantic results survived; no exception bubbled up.
        assert snippets.snippets
        assert all(s.note_id.startswith("sem_") for s in snippets.snippets)


class TestFormat:
    async def test_episodic_body_has_date(self, episodic):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        await episodic.add_episode(
            summary="某日事件",
            occurred_at=datetime(2026, 4, 17, 12, 0, tzinfo=ZoneInfo("Asia/Taipei")),
        )
        ws = WorkingSet(episodic_store=episodic)
        snippets = await ws.retrieve("某日", top_k=1)
        assert snippets.snippets
        assert "4/17" in snippets.snippets[0].content

    async def test_semantic_body_has_category(self, semantic):
        await semantic.add_fact(
            category="world", content="2026 世界杯在美墨加",
        )
        ws = WorkingSet(semantic_store=semantic)
        snippets = await ws.retrieve("世界杯", top_k=1)
        assert snippets.snippets
        assert "[知识 / world]" in snippets.snippets[0].content

    async def test_truncates_long_content(self, semantic):
        long = "Kevin " + "A" * 500
        await semantic.add_fact(category="kevin", content=long)
        ws = WorkingSet(semantic_store=semantic)
        snippets = await ws.retrieve("Kevin", top_k=1)
        assert snippets.snippets
        assert len(snippets.snippets[0].content) <= 301  # budget + ellipsis
