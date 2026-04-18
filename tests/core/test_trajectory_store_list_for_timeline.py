"""Tests for TrajectoryStore.list_for_timeline — timeline-oriented paginated read."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.trajectory_store import TrajectoryEntryType, TrajectoryStore
from src.logging.state_mutation_log import StateMutationLog


@pytest.fixture
async def store(tmp_path: Path):
    db_path = tmp_path / "traj.db"
    mutation_log = StateMutationLog(db_path=tmp_path / "mutation.db")
    await mutation_log.init()
    s = TrajectoryStore(db_path=db_path, mutation_log=mutation_log)
    await s.init()
    yield s
    await s.close()
    await mutation_log.close()


async def _seed(store: TrajectoryStore, ts: float, entry_type: TrajectoryEntryType, chat_id: str = "c1") -> int:
    return await store.append(
        entry_type=entry_type,
        source_chat_id=chat_id,
        actor="lapwing" if entry_type != TrajectoryEntryType.USER_MESSAGE else "user",
        content={"text": f"t={ts}"},
        timestamp=ts,
    )


@pytest.mark.asyncio
class TestListForTimeline:
    async def test_returns_newest_first(self, store):
        await _seed(store, 100.0, TrajectoryEntryType.USER_MESSAGE)
        await _seed(store, 200.0, TrajectoryEntryType.ASSISTANT_TEXT)
        await _seed(store, 150.0, TrajectoryEntryType.INNER_THOUGHT)

        rows = await store.list_for_timeline(limit=10)

        assert [r.timestamp for r in rows] == [200.0, 150.0, 100.0]

    async def test_before_ts_is_strict(self, store):
        await _seed(store, 100.0, TrajectoryEntryType.USER_MESSAGE)
        await _seed(store, 200.0, TrajectoryEntryType.ASSISTANT_TEXT)

        rows = await store.list_for_timeline(before_ts=200.0, limit=10)

        # Strict < cutoff; the 200.0 row must not reappear when paging.
        assert [r.timestamp for r in rows] == [100.0]

    async def test_filters_entry_types(self, store):
        await _seed(store, 100.0, TrajectoryEntryType.USER_MESSAGE)
        await _seed(store, 200.0, TrajectoryEntryType.INNER_THOUGHT)
        await _seed(store, 300.0, TrajectoryEntryType.ASSISTANT_TEXT)

        rows = await store.list_for_timeline(
            entry_types=[TrajectoryEntryType.ASSISTANT_TEXT, TrajectoryEntryType.USER_MESSAGE],
            limit=10,
        )

        assert [r.entry_type for r in rows] == ["assistant_text", "user_message"]

    async def test_filters_chat_id(self, store):
        await _seed(store, 100.0, TrajectoryEntryType.USER_MESSAGE, chat_id="c1")
        await _seed(store, 200.0, TrajectoryEntryType.ASSISTANT_TEXT, chat_id="c2")

        rows = await store.list_for_timeline(source_chat_id="c1", limit=10)

        assert [r.source_chat_id for r in rows] == ["c1"]

    async def test_limit_enforced(self, store):
        for i in range(5):
            await _seed(store, float(i), TrajectoryEntryType.USER_MESSAGE)

        rows = await store.list_for_timeline(limit=2)

        assert len(rows) == 2
        assert [r.timestamp for r in rows] == [4.0, 3.0]

    async def test_empty_returns_empty_list(self, store):
        rows = await store.list_for_timeline(limit=10)
        assert rows == []
