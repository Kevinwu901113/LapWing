"""Integration test — TrajectoryStore appends forward to Dispatcher."""

from __future__ import annotations

import asyncio

import pytest

from src.core.dispatcher import Dispatcher
from src.core.trajectory_store import TrajectoryEntryType, TrajectoryStore
from src.logging.state_mutation_log import StateMutationLog
from src.app.container import _wire_trajectory_to_dispatcher


@pytest.mark.asyncio
async def test_trajectory_append_fans_out_to_dispatcher(tmp_path):
    mutation_log = StateMutationLog(db_path=tmp_path / "m.db")
    await mutation_log.init()
    store = TrajectoryStore(db_path=tmp_path / "t.db", mutation_log=mutation_log)
    await store.init()

    dispatcher = Dispatcher()
    queue: asyncio.Queue = asyncio.Queue()
    dispatcher.subscribe_all(queue)

    _wire_trajectory_to_dispatcher(store, dispatcher)

    await store.append(
        entry_type=TrajectoryEntryType.ASSISTANT_TEXT,
        source_chat_id="desktop:kevin",
        actor="lapwing",
        content={"text": "ok"},
    )

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.event_type == "trajectory_appended"
    assert event.payload["kind"] == "assistant_text"
    assert event.payload["content"] == "ok"
    assert event.payload["metadata"]["actor"] == "lapwing"
    assert event.payload["id"].startswith("traj_")

    await store.close()
    await mutation_log.close()
