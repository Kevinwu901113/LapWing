"""Step 7 end-to-end memory integration.

Verifies the whole pipeline runs together:
- EpisodicStore + SemanticStore write to a shared MemoryVectorStore
  with the correct ``note_type`` metadata filter
- WorkingSet retrieves relevant hits across layers
- StateViewBuilder exposes them as ``StateView.memory_snippets``
- StateSerializer emits the ``## 记忆片段`` section in the system prompt

The goal is to lock in the integration contract: no piece can be swapped
in isolation without a visible effect here.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.core.state_serializer import serialize
from src.core.state_view import TrajectoryTurn
from src.core.state_view_builder import StateViewBuilder
from src.memory.episodic_store import EpisodicStore
from src.memory.semantic_store import SemanticStore
from src.memory.vector_store import MemoryVectorStore
from src.memory.working_set import WorkingSet

_TAIPEI = ZoneInfo("Asia/Taipei")


@pytest.fixture
def mem_stack(tmp_path):
    """End-to-end memory stack with isolated tmp storage."""
    vector = MemoryVectorStore(persist_dir=str(tmp_path / "chroma"))
    episodic = EpisodicStore(
        memory_dir=tmp_path / "episodic",
        vector_store=vector,
    )
    semantic = SemanticStore(
        memory_dir=tmp_path / "semantic",
        vector_store=vector,
        dedup_threshold=0.95,
    )
    working_set = WorkingSet(
        episodic_store=episodic, semantic_store=semantic,
    )
    return {
        "vector": vector,
        "episodic": episodic,
        "semantic": semantic,
        "working_set": working_set,
    }


class TestChatPathIntegration:
    async def test_memory_flows_from_stores_to_system_prompt(
        self, tmp_path, mem_stack,
    ):
        # Seed the stores
        await mem_stack["episodic"].add_episode(
            summary="Kevin 问了今晚道奇比赛的结果，我查到后告诉了他",
            occurred_at=datetime(2026, 4, 17, 20, 30, tzinfo=_TAIPEI),
        )
        await mem_stack["semantic"].add_fact(
            category="kevin",
            content="Kevin 习惯晚上在家里看道奇的比赛",
        )
        await mem_stack["semantic"].add_fact(
            category="lapwing",
            content="我用 research 工具查体育数据时偶尔会遇到网络超时",
        )

        # Build StateView via a minimal builder — identity docs empty,
        # WorkingSet wired.
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=mem_stack["working_set"],
        )
        view = await builder.build_for_chat(
            "kev",
            trajectory_turns_override=(
                TrajectoryTurn(
                    role="user",
                    content="晚上看道奇了吗？",
                ),
            ),
        )

        # Snippets populated across layers
        assert len(view.memory_snippets.snippets) >= 2
        ids = {s.note_id for s in view.memory_snippets.snippets}
        assert any(i.startswith("ep_") for i in ids)
        assert any(i.startswith("sem_") for i in ids)

        # Prompt surfaces the memory section
        rendered = serialize(view)
        assert "## 记忆片段" in rendered.system_prompt
        assert "[情景" in rendered.system_prompt
        assert "[知识" in rendered.system_prompt

    async def test_inner_path_integration(self, tmp_path, mem_stack):
        await mem_stack["episodic"].add_episode(
            summary="Lapwing 在深夜反思最近的工作节奏",
        )
        await mem_stack["semantic"].add_fact(
            category="lapwing", content="我每晚 12 点前要进入 wind-down",
        )

        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=mem_stack["working_set"],
        )
        view = await builder.build_for_inner(
            trajectory_turns_override=(
                TrajectoryTurn(
                    role="user",
                    content="[内部 tick] 今晚的反思",
                ),
            ),
        )
        assert view.memory_snippets.snippets

    async def test_no_leakage_across_layers(self, mem_stack):
        """Episodic query must not surface semantic hits (and vice versa).

        This enforces the metadata-filter contract: each store's
        ``query`` uses ``where={"note_type": "<layer>"}`` so the
        layers are partitioned inside the shared collection.
        """
        await mem_stack["episodic"].add_episode(summary="episodic only entry foo")
        await mem_stack["semantic"].add_fact(
            category="kevin", content="semantic only entry bar",
        )

        ep_hits = await mem_stack["episodic"].query("semantic only", top_k=5)
        assert all(h.episode_id.startswith("ep_") for h in ep_hits)
        assert all("semantic only" not in h.summary for h in ep_hits)

        sem_hits = await mem_stack["semantic"].query("episodic only", top_k=5)
        assert all(h.fact_id.startswith("sem_") for h in sem_hits)


class TestExtractorThenRetrieve:
    """End-to-end: extract from a conversation, then find it via query."""

    async def test_conversation_to_memory_snippet(self, tmp_path, mem_stack):
        # Simulate: extractor LLM produces "title\n\nsummary" by directly
        # calling add_episode (mirrors what EpisodicExtractor does after
        # parsing the LLM response).
        await mem_stack["episodic"].add_episode(
            title="Kevin 道奇问询",
            summary=(
                "Kevin 问了道奇今晚比赛结果，我用 research 查了但网超时，"
                "最终重试成功并告知了他"
            ),
        )

        # Later conversation: user brings up dodgers again.
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=mem_stack["working_set"],
        )
        view = await builder.build_for_chat(
            "kev",
            trajectory_turns_override=(
                TrajectoryTurn(
                    role="user", content="道奇今天比赛怎么样",
                ),
            ),
        )
        # The previously-extracted episode should be surfaced.
        contents = [s.content for s in view.memory_snippets.snippets]
        assert any("道奇" in c for c in contents)
