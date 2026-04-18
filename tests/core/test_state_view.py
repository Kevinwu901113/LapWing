"""Unit tests for src.core.state_view — construction + frozen semantics.

Blueprint v2.0 Step 3 §1. These tests lock in the invariant that StateView
and its sub-records cannot be mutated after construction. The serializer
(next module) relies on that to stay a pure function; if these tests
regress, the serializer's "same input → same output" guarantee breaks.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from src.core.state_view import (
    AttentionContext,
    CommitmentView,
    IdentityDocs,
    MemorySnippet,
    MemorySnippets,
    SerializedPrompt,
    StateView,
    TrajectoryTurn,
    TrajectoryWindow,
)


def _minimal_state() -> StateView:
    """Build the smallest legal StateView for structural tests."""
    return StateView(
        identity_docs=IdentityDocs(soul="", constitution="", voice=""),
        attention_context=AttentionContext(
            channel="desktop",
            actor_id=None,
            actor_name=None,
            auth_level=3,
            group_id=None,
            current_conversation=None,
            mode="idle",
            now=datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
            offline_hours=None,
        ),
        trajectory_window=TrajectoryWindow(turns=()),
        memory_snippets=MemorySnippets(snippets=()),
        commitments_active=(),
    )


class TestStateViewConstruction:
    def test_minimal(self):
        sv = _minimal_state()
        assert sv.identity_docs.soul == ""
        assert sv.attention_context.mode == "idle"
        assert sv.trajectory_window.turns == ()
        assert sv.memory_snippets.snippets == ()
        assert sv.commitments_active == ()

    def test_full(self):
        turn = TrajectoryTurn(role="user", content="hi")
        snippet = MemorySnippet(note_id="n1", content="Kevin likes tea", score=0.8)
        com = CommitmentView(
            id="c1",
            description="remind kevin at 3pm",
            status="open",
            kind="reminder",
            due_at="2026-04-18T15:00:00+08:00",
        )
        sv = StateView(
            identity_docs=IdentityDocs(soul="SOUL", constitution="CONST", voice="VOICE"),
            attention_context=AttentionContext(
                channel="qq_group",
                actor_id="u1",
                actor_name="Alice",
                auth_level=2,
                group_id="g42",
                current_conversation="qq_group:g42",
                mode="conversing",
                now=datetime(2026, 4, 18, 14, 30, tzinfo=timezone.utc),
                offline_hours=6.3,
            ),
            trajectory_window=TrajectoryWindow(turns=(turn,)),
            memory_snippets=MemorySnippets(snippets=(snippet,)),
            commitments_active=(com,),
        )
        assert sv.identity_docs.soul == "SOUL"
        assert sv.attention_context.offline_hours == pytest.approx(6.3)
        assert sv.trajectory_window.turns[0].content == "hi"
        assert sv.memory_snippets.snippets[0].score == pytest.approx(0.8)
        assert sv.commitments_active[0].kind == "reminder"


class TestFrozenSemantics:
    def test_state_view_immutable(self):
        sv = _minimal_state()
        with pytest.raises(FrozenInstanceError):
            sv.commitments_active = ()  # type: ignore[misc]

    def test_identity_docs_immutable(self):
        docs = IdentityDocs(soul="a", constitution="b", voice="c")
        with pytest.raises(FrozenInstanceError):
            docs.soul = "changed"  # type: ignore[misc]

    def test_attention_context_immutable(self):
        sv = _minimal_state()
        with pytest.raises(FrozenInstanceError):
            sv.attention_context.mode = "conversing"  # type: ignore[misc]

    def test_trajectory_turn_immutable(self):
        turn = TrajectoryTurn(role="user", content="x")
        with pytest.raises(FrozenInstanceError):
            turn.role = "assistant"  # type: ignore[misc]

    def test_trajectory_window_immutable(self):
        win = TrajectoryWindow(turns=())
        with pytest.raises(FrozenInstanceError):
            win.turns = (TrajectoryTurn(role="user", content="y"),)  # type: ignore[misc]

    def test_memory_snippet_immutable(self):
        s = MemorySnippet(note_id="n", content="c", score=0.1)
        with pytest.raises(FrozenInstanceError):
            s.score = 0.9  # type: ignore[misc]

    def test_commitment_view_immutable(self):
        c = CommitmentView(
            id="c", description="d", status="open", kind="promise", due_at=None
        )
        with pytest.raises(FrozenInstanceError):
            c.status = "fulfilled"  # type: ignore[misc]

    def test_trajectory_window_uses_tuple(self):
        """Turns must be a tuple so the container is hashable and
        cannot be extended in place. A list here would let callers
        mutate state through the frozen façade."""
        win = TrajectoryWindow(turns=(TrajectoryTurn(role="user", content="hi"),))
        assert isinstance(win.turns, tuple)

    def test_commitments_tuple_not_list(self):
        sv = _minimal_state()
        assert isinstance(sv.commitments_active, tuple)


class TestSerializedPrompt:
    def test_basic_construction(self):
        sp = SerializedPrompt(
            system_prompt="you are lapwing",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert sp.system_prompt == "you are lapwing"
        assert sp.messages == [{"role": "user", "content": "hi"}]

    def test_serialized_prompt_frozen(self):
        sp = SerializedPrompt(system_prompt="sys", messages=[])
        with pytest.raises(FrozenInstanceError):
            sp.system_prompt = "changed"  # type: ignore[misc]
