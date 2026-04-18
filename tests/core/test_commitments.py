"""Unit tests for CommitmentStore.

Covers Blueprint v2.0 Step 2 §3 requirements:
  1. create() returns id, writes row, emits COMMITMENT_CREATED
  2. set_status() emits COMMITMENT_STATUS_CHANGED, validates status
  3. get() returns None for unknown id
  4. list_open() only returns pending + in_progress
  5. list_open() chat_id filter
"""

from __future__ import annotations

import pytest

from src.core.commitments import (
    Commitment,
    CommitmentStatus,
    CommitmentStore,
)
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    iteration_context,
    new_iteration_id,
)


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(
        tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs"
    )
    await log.init()
    yield log
    await log.close()


@pytest.fixture
async def store(tmp_path, mutation_log):
    s = CommitmentStore(tmp_path / "lapwing.db", mutation_log)
    await s.init()
    yield s
    await s.close()


class TestCreate:
    async def test_create_returns_hex_id(self, store):
        cid = await store.create("chat1", "I'll check that", source_trajectory_entry_id=42)
        assert isinstance(cid, str)
        assert len(cid) == 32  # uuid4.hex

    async def test_create_persists_defaults(self, store):
        cid = await store.create(
            "chat1", "promise", source_trajectory_entry_id=7, reasoning="user asked for status",
        )
        row = await store.get(cid)
        assert row is not None
        assert row.target_chat_id == "chat1"
        assert row.content == "promise"
        assert row.source_trajectory_entry_id == 7
        assert row.status == CommitmentStatus.PENDING.value
        assert row.status_changed_at == row.created_at
        assert row.fulfilled_by_entry_ids is None
        assert row.reasoning == "user asked for status"

    async def test_create_emits_commitment_created(self, store, mutation_log):
        it_id = new_iteration_id()
        with iteration_context(it_id):
            cid = await store.create(
                "chat1", "content", source_trajectory_entry_id=1, reasoning="r",
            )
        muts = await mutation_log.query_by_type(MutationType.COMMITMENT_CREATED)
        assert len(muts) == 1
        p = muts[0].payload
        assert p["commitment_id"] == cid
        assert p["target_chat_id"] == "chat1"
        assert p["content"] == "content"
        assert p["source_trajectory_entry_id"] == 1
        assert p["reasoning"] == "r"
        assert muts[0].iteration_id == it_id
        assert muts[0].chat_id == "chat1"

    async def test_create_requires_init(self, tmp_path, mutation_log):
        s = CommitmentStore(tmp_path / "u.db", mutation_log)
        with pytest.raises(RuntimeError):
            await s.create("chat1", "x", source_trajectory_entry_id=1)


class TestSetStatus:
    async def test_set_status_updates_row_and_emits_mutation(
        self, store, mutation_log
    ):
        cid = await store.create("chat1", "p", source_trajectory_entry_id=1)
        await store.set_status(
            cid, CommitmentStatus.FULFILLED.value,
            fulfilled_by_entry_ids=[10, 11],
        )
        row = await store.get(cid)
        assert row is not None
        assert row.status == "fulfilled"
        assert row.fulfilled_by_entry_ids == [10, 11]
        assert row.status_changed_at > row.created_at

        muts = await mutation_log.query_by_type(
            MutationType.COMMITMENT_STATUS_CHANGED
        )
        assert len(muts) == 1
        p = muts[0].payload
        assert p["commitment_id"] == cid
        assert p["old_status"] == "pending"
        assert p["new_status"] == "fulfilled"
        assert p["fulfilled_by_entry_ids"] == [10, 11]

    async def test_set_status_rejects_invalid_status(self, store):
        cid = await store.create("chat1", "p", source_trajectory_entry_id=1)
        with pytest.raises(ValueError):
            await store.set_status(cid, "done")

    async def test_set_status_unknown_id_raises(self, store):
        with pytest.raises(KeyError):
            await store.set_status("no-such", CommitmentStatus.ABANDONED.value)

    async def test_status_transitions_through_intermediate(self, store, mutation_log):
        cid = await store.create("chat1", "p", source_trajectory_entry_id=1)
        await store.set_status(cid, CommitmentStatus.IN_PROGRESS.value)
        await store.set_status(cid, CommitmentStatus.FULFILLED.value)
        muts = await mutation_log.query_by_type(
            MutationType.COMMITMENT_STATUS_CHANGED
        )
        # Note: query_by_type returns DESC, so [0] = latest
        assert len(muts) == 2
        assert muts[0].payload["new_status"] == "fulfilled"
        assert muts[1].payload["new_status"] == "in_progress"


class TestGet:
    async def test_get_unknown_returns_none(self, store):
        assert await store.get("no-such") is None


class TestListOpen:
    async def test_list_open_only_pending_and_in_progress(self, store):
        p = await store.create("chat1", "pend", source_trajectory_entry_id=1)
        ip = await store.create("chat1", "inprog", source_trajectory_entry_id=2)
        f = await store.create("chat1", "done", source_trajectory_entry_id=3)
        a = await store.create("chat1", "abnd", source_trajectory_entry_id=4)
        await store.set_status(ip, CommitmentStatus.IN_PROGRESS.value)
        await store.set_status(f, CommitmentStatus.FULFILLED.value)
        await store.set_status(a, CommitmentStatus.ABANDONED.value)

        rows = await store.list_open()
        ids = {r.id for r in rows}
        assert ids == {p, ip}

    async def test_list_open_chat_filter(self, store):
        a = await store.create("chat1", "p", source_trajectory_entry_id=1)
        b = await store.create("chat2", "p", source_trajectory_entry_id=2)
        rows1 = await store.list_open("chat1")
        rows2 = await store.list_open("chat2")
        assert [r.id for r in rows1] == [a]
        assert [r.id for r in rows2] == [b]

    async def test_list_open_empty_before_any_writes(self, store):
        assert await store.list_open() == []
        assert await store.list_open("anywhere") == []

    async def test_list_open_oldest_first(self, store):
        a = await store.create("chat1", "first", source_trajectory_entry_id=1)
        b = await store.create("chat1", "second", source_trajectory_entry_id=2)
        c = await store.create("chat1", "third", source_trajectory_entry_id=3)
        rows = await store.list_open()
        assert [r.id for r in rows] == [a, b, c]
