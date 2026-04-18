"""Unit tests for LapwingBrain._load_history read-path switch (Step 2g).

Verifies the contract:
  - When trajectory_store is wired, _load_history queries trajectory +
    compat shim and ignores ConversationMemory.get.
  - When trajectory_store is None (unit tests, phase-0), falls back to
    ConversationMemory.get.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType


def _mk_entry(id_, entry_type, source_chat_id, actor, text):
    return TrajectoryEntry(
        id=id_, timestamp=float(id_), entry_type=entry_type.value,
        source_chat_id=source_chat_id, actor=actor,
        content={"text": text},
        related_commitment_id=None, related_iteration_id=None,
        related_tool_call_id=None,
    )


@pytest.fixture
def brain(tmp_path):
    # Patch the heavy deps so Brain() can instantiate without a full app.
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.build_default_tool_registry"), \
         patch("src.core.brain.ConversationMemory"), \
         patch("src.core.brain.TaskRuntime"):
        from src.core.brain import LapwingBrain
        b = LapwingBrain(db_path=tmp_path / "x.db")
    return b


class TestLoadHistoryPrefersTrajectoryWhenWired:
    async def test_wired_trajectory_is_queried(self, brain):
        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[
            _mk_entry(1, TrajectoryEntryType.USER_MESSAGE, "c1", "user", "hi"),
            _mk_entry(2, TrajectoryEntryType.ASSISTANT_TEXT, "c1", "lapwing", "yo"),
        ])
        brain.memory.get = AsyncMock(return_value=[{"role": "user", "content": "LEGACY"}])

        out = await brain._load_history("c1")

        assert out == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ]
        brain.memory.get.assert_not_awaited()
        brain.trajectory_store.relevant_to_chat.assert_awaited_once()
        kwargs = brain.trajectory_store.relevant_to_chat.call_args.kwargs
        assert kwargs.get("include_inner") is False

    async def test_empty_trajectory_returns_empty_list(self, brain):
        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])
        brain.memory.get = AsyncMock(return_value=[{"role": "user", "content": "LEGACY"}])

        out = await brain._load_history("c1")
        assert out == []
        brain.memory.get.assert_not_awaited()


class TestLoadHistoryFallsBackWhenNoTrajectory:
    async def test_no_trajectory_uses_memory_get(self, brain):
        assert brain.trajectory_store is None  # default
        brain.memory.get = AsyncMock(return_value=[
            {"role": "user", "content": "from-legacy"},
        ])

        out = await brain._load_history("c1")
        assert out == [{"role": "user", "content": "from-legacy"}]
        brain.memory.get.assert_awaited_once_with("c1")


class TestLoadHistoryMaxTurnsCap:
    async def test_n_argument_matches_max_history_turns_times_two(self, brain):
        from config.settings import MAX_HISTORY_TURNS

        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])

        await brain._load_history("c1")
        kwargs = brain.trajectory_store.relevant_to_chat.call_args.kwargs
        args = brain.trajectory_store.relevant_to_chat.call_args.args
        # Accept either positional or kw
        n = kwargs.get("n") or (args[1] if len(args) > 1 else None)
        assert n == MAX_HISTORY_TURNS * 2
