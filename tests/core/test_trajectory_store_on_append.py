"""Tests for the TrajectoryStore on-append listener hook."""

from __future__ import annotations

import pytest

from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType, TrajectoryStore
from src.logging.state_mutation_log import StateMutationLog


@pytest.fixture
async def store(tmp_path):
    mutation_log = StateMutationLog(db_path=tmp_path / "m.db")
    await mutation_log.init()
    s = TrajectoryStore(db_path=tmp_path / "t.db", mutation_log=mutation_log)
    await s.init()
    yield s
    await s.close()
    await mutation_log.close()


@pytest.mark.asyncio
async def test_on_append_called_after_insert(store: TrajectoryStore):
    seen: list[TrajectoryEntry] = []

    async def listener(entry: TrajectoryEntry) -> None:
        seen.append(entry)

    store.add_on_append_listener(listener)
    entry_id = await store.append(
        entry_type=TrajectoryEntryType.USER_MESSAGE,
        source_chat_id="c1",
        actor="user",
        content={"text": "hi"},
    )

    assert len(seen) == 1
    assert seen[0].id == entry_id
    assert seen[0].entry_type == "user_message"
    assert seen[0].content == {"text": "hi"}


@pytest.mark.asyncio
async def test_listener_exception_is_swallowed(store: TrajectoryStore):
    async def bad_listener(entry):
        raise RuntimeError("boom")

    store.add_on_append_listener(bad_listener)

    # Should not raise.
    await store.append(
        entry_type=TrajectoryEntryType.USER_MESSAGE,
        source_chat_id="c1",
        actor="user",
        content={"text": "hi"},
    )


@pytest.mark.asyncio
async def test_sync_listener_also_supported(store):
    calls = []

    def listener(entry):
        calls.append(entry.id)

    store.add_on_append_listener(listener)
    await store.append(
        entry_type=TrajectoryEntryType.USER_MESSAGE,
        source_chat_id="c1",
        actor="user",
        content={"text": "hi"},
    )
    assert len(calls) == 1
