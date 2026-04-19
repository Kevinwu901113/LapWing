"""SemanticDistiller unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.episodic_store import EpisodicStore
from src.memory.semantic_distiller import SemanticDistiller
from src.memory.semantic_store import SemanticStore
from src.memory.vector_store import MemoryVectorStore


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
        dedup_threshold=0.99,  # admit most facts for test clarity
    )


def _make_distiller(router_response: str, episodic, semantic):
    router = MagicMock()
    router.complete = AsyncMock(return_value=router_response)
    return SemanticDistiller(
        router=router,
        episodic_store=episodic,
        semantic_store=semantic,
        episodes_window=10,
    ), router


class TestDistillRecent:
    async def test_distills_facts_from_episodes(
        self, episodic, semantic,
    ):
        await episodic.add_episode(summary="Kevin 今天早上 8 点喝了手冲咖啡")
        await episodic.add_episode(summary="Kevin 又在下午 3 点喝了手冲咖啡")
        await episodic.add_episode(summary="Kevin 晚上去看了道奇的比赛")

        response = (
            "kevin | Kevin 每天喝手冲咖啡\n"
            "kevin | Kevin 喜欢看道奇的比赛\n"
        )
        distiller, router = _make_distiller(response, episodic, semantic)

        written = await distiller.distill_recent()
        assert written == 2

        hits_coffee = await semantic.query("咖啡", top_k=3)
        assert any("手冲" in h.content for h in hits_coffee)
        hits_dodgers = await semantic.query("道奇", top_k=3)
        assert any("道奇" in h.content for h in hits_dodgers)

        router.complete.assert_awaited_once()

    async def test_source_episodes_attached(self, episodic, semantic):
        entry = await episodic.add_episode(summary="道奇的重要事件")
        assert entry is not None
        response = "kevin | Kevin 关注道奇"
        distiller, _ = _make_distiller(response, episodic, semantic)
        await distiller.distill_recent()

        hits = await semantic.query("道奇", top_k=2)
        assert hits
        assert entry.episode_id in hits[0].source_episodes

    async def test_returns_zero_on_no_episodes(self, episodic, semantic):
        distiller, router = _make_distiller("kevin | x", episodic, semantic)
        written = await distiller.distill_recent()
        assert written == 0
        router.complete.assert_not_called()

    async def test_returns_zero_on_llm_failure(self, episodic, semantic):
        await episodic.add_episode(summary="some event for distillation")
        router = MagicMock()
        router.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        distiller = SemanticDistiller(
            router=router,
            episodic_store=episodic,
            semantic_store=semantic,
        )
        written = await distiller.distill_recent()
        assert written == 0

    async def test_rejects_malformed_lines(self, episodic, semantic):
        await episodic.add_episode(summary="some event for distillation")
        response = (
            "not a valid line\n"
            "kevin | valid fact\n"
            "no pipe here either\n"
            "# comment line\n"
            " | empty category\n"
            "```\n"
        )
        distiller, _ = _make_distiller(response, episodic, semantic)
        written = await distiller.distill_recent()
        assert written == 1  # only "kevin | valid fact"

    async def test_dedup_skips_duplicate_fact(
        self, tmp_path, vector_store, episodic,
    ):
        # Strict dedup threshold so the "same" fact is dropped on second
        # invocation.
        strict = SemanticStore(
            memory_dir=tmp_path / "sem",
            vector_store=vector_store,
            dedup_threshold=0.9,
        )
        await episodic.add_episode(summary="Kevin 喝咖啡事件")

        response = "kevin | Kevin 喜欢喝咖啡"

        d1, _ = _make_distiller(response, episodic, strict)
        assert await d1.distill_recent() == 1

        d2, _ = _make_distiller(response, episodic, strict)
        # Second run: distiller calls add_fact which dedups → returns None
        assert await d2.distill_recent() == 0
