"""StateViewBuilder ↔ WorkingSet integration (Step 7 M2).

Previously ``build_for_chat`` / ``build_for_inner`` returned
``MemorySnippets(snippets=())`` unconditionally. With a WorkingSet wired,
the builder must:

- derive a query from the trajectory window
- ask WorkingSet for top-K hits
- return them as ``MemorySnippets`` on the StateView

Also verifies ``StateSerializer`` renders the ``## 记忆片段`` block when
the snippets are non-empty.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.state_serializer import serialize
from src.core.state_view import (
    MemorySnippet,
    MemorySnippets,
    TrajectoryTurn,
)
from src.core.state_view_builder import StateViewBuilder


class _FakeWorkingSet:
    """Stand-in for src.memory.working_set.WorkingSet."""

    def __init__(self, snippets=None):
        self._snippets = snippets or ()
        self.captured_query = None
        self.captured_top_k = None
        self.captured_wiki_entities = None

    async def retrieve(
        self,
        query_text: str,
        *,
        top_k: int = 10,
        wiki_entities=None,
    ):
        self.captured_query = query_text
        self.captured_top_k = top_k
        self.captured_wiki_entities = wiki_entities
        return MemorySnippets(snippets=tuple(self._snippets))


class TestBuildForChat:
    async def test_empty_when_no_working_set(self, tmp_path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
        )
        view = await builder.build_for_chat(
            "desk",
            trajectory_turns_override=(
                TrajectoryTurn(role="user", content="hello"),
            ),
        )
        assert view.memory_snippets.snippets == ()

    async def test_populates_from_working_set(self, tmp_path):
        ws = _FakeWorkingSet(
            snippets=(
                MemorySnippet(note_id="ep_1", content="[情景 4/17] foo", score=0.9),
                MemorySnippet(note_id="sem_1", content="[知识 / kevin] bar", score=0.8),
            ),
        )
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=ws,
            memory_top_k=6,
        )
        view = await builder.build_for_chat(
            "desk",
            trajectory_turns_override=(
                TrajectoryTurn(role="user", content="问一下道奇最近怎么样"),
                TrajectoryTurn(role="assistant", content="我查一下"),
            ),
        )
        assert len(view.memory_snippets.snippets) == 2
        assert view.memory_snippets.snippets[0].note_id == "ep_1"

        # Query derived from the last turns, top_k from builder config.
        assert ws.captured_query is not None
        assert "道奇" in ws.captured_query
        assert ws.captured_top_k == 6

    async def test_empty_trajectory_gives_empty_query(self, tmp_path):
        ws = _FakeWorkingSet()
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=ws,
        )
        view = await builder.build_for_chat(
            "desk",
            trajectory_turns_override=(),
        )
        # No turns → empty query string. Wiki injection still runs at
        # owner auth_level so the call happens with query_text="".
        assert view.memory_snippets.snippets == ()
        assert ws.captured_query == ""
        assert ws.captured_wiki_entities == ["entity.kevin", "entity.lapwing"]

    async def test_system_turns_excluded_from_query(self, tmp_path):
        ws = _FakeWorkingSet()
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=ws,
        )
        await builder.build_for_chat(
            "desk",
            trajectory_turns_override=(
                TrajectoryTurn(role="system", content="voice note internal"),
                TrajectoryTurn(role="user", content="真正的问题"),
            ),
        )
        assert "voice note internal" not in (ws.captured_query or "")
        assert "真正的问题" in (ws.captured_query or "")

    async def test_retrieval_failure_yields_empty(self, tmp_path):
        class Broken:
            async def retrieve(self, *_a, **_k):
                raise RuntimeError("chromadb down")

        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=Broken(),
        )
        view = await builder.build_for_chat(
            "desk",
            trajectory_turns_override=(
                TrajectoryTurn(role="user", content="ask"),
            ),
        )
        assert view.memory_snippets.snippets == ()


class TestBuildForInner:
    async def test_populates_from_working_set(self, tmp_path):
        ws = _FakeWorkingSet(
            snippets=(
                MemorySnippet(note_id="ep_99", content="[情景 4/18] 思考", score=0.7),
            ),
        )
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=ws,
        )
        view = await builder.build_for_inner(
            trajectory_turns_override=(
                TrajectoryTurn(role="user", content="内部 tick 上下文"),
            ),
        )
        assert len(view.memory_snippets.snippets) == 1
        assert "内部 tick" in (ws.captured_query or "")


class TestSerializerMemoryLayer:
    async def test_memory_block_rendered(self, tmp_path):
        ws = _FakeWorkingSet(
            snippets=(
                MemorySnippet(
                    note_id="ep_1",
                    content="[情景 4/17] Kevin 问了道奇",
                    score=0.9,
                ),
                MemorySnippet(
                    note_id="sem_1",
                    content="[知识 / kevin] Kevin 喜欢道奇",
                    score=0.8,
                ),
            ),
        )
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
            working_set=ws,
        )
        view = await builder.build_for_chat(
            "desk",
            trajectory_turns_override=(
                TrajectoryTurn(role="user", content="道奇"),
            ),
        )
        rendered = serialize(view)
        assert "## 记忆片段" in rendered.system_prompt
        assert "[情景 4/17]" in rendered.system_prompt
        assert "[知识 / kevin]" in rendered.system_prompt

    async def test_memory_block_omitted_when_empty(self, tmp_path):
        builder = StateViewBuilder(
            soul_path=tmp_path / "soul.md",
            constitution_path=tmp_path / "constitution.md",
        )
        view = await builder.build_for_chat(
            "desk",
            trajectory_turns_override=(
                TrajectoryTurn(role="user", content="hi"),
            ),
        )
        rendered = serialize(view)
        assert "## 记忆片段" not in rendered.system_prompt
