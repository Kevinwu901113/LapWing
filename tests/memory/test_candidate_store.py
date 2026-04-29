"""CandidateStore unit tests (Phase 2)."""

from __future__ import annotations

import pytest

from src.memory.candidate import MemoryCandidate
from src.memory.candidate_store import CandidateStore
from src.memory.quality_gate import MemoryGateDecision


@pytest.fixture
async def store(tmp_path):
    s = CandidateStore(db_path=tmp_path / "lapwing.db")
    await s.init()
    yield s
    await s.close()


def _accept(source_id: str = "msg-1") -> MemoryGateDecision:
    return MemoryGateDecision(
        source_id=source_id,
        decision="accept",
        salience=0.85,
        stability="long_lived",
        rough_category="preference",
    )


def _candidate(cid: str) -> MemoryCandidate:
    return MemoryCandidate(
        id=cid,
        source_ids=["traj:1"],
        subject="entity.kevin",
        predicate="prefers",
        object="blueprint-first",
        type="preference",
        salience=0.85,
        confidence=0.9,
        stability="long_lived",
        privacy_level="personal",
    )


async def test_enqueue_returns_candidate_id(store):
    cid = await store.enqueue(_accept(), source_ids=["traj:1"], source_hash="h1")
    assert cid.startswith("candidate:")


async def test_enqueue_rejects_non_accept(store):
    decision = MemoryGateDecision(
        source_id="m1", decision="defer", salience=0.5,
        stability="session", rough_category="task",
    )
    with pytest.raises(ValueError):
        await store.enqueue(decision, source_ids=[], source_hash="h")


async def test_enqueue_idempotent_on_same_source(store):
    cid1 = await store.enqueue(_accept("m1"), ["traj:1"], "h1")
    cid2 = await store.enqueue(_accept("m1"), ["traj:1"], "h1")
    assert cid1 == cid2


async def test_get_pending_returns_oldest_first(store):
    await store.enqueue(_accept("m1"), ["traj:1"], "h1")
    await store.enqueue(_accept("m2"), ["traj:2"], "h2")
    pending = await store.get_pending()
    assert [p.id for p in pending] == ["candidate:m1", "candidate:m2"]


async def test_mark_compiling_removes_from_pending(store):
    cid = await store.enqueue(_accept(), ["traj:1"], "h1")
    await store.mark_compiling([cid])
    pending = await store.get_pending()
    assert pending == []


async def test_mark_compiling_is_idempotent(store):
    cid = await store.enqueue(_accept(), ["traj:1"], "h1")
    await store.mark_compiling([cid])
    # second call should not raise even though row is no longer pending
    await store.mark_compiling([cid])


async def test_fill_candidate_persists_json(store):
    cid = await store.enqueue(_accept("m1"), ["traj:1"], "h1")
    await store.fill_candidate(cid, _candidate(cid))
    record = await store.get_by_id(cid)
    assert record is not None
    assert record.candidate_json is not None
    restored = MemoryCandidate.model_validate_json(record.candidate_json)
    assert restored.subject == "entity.kevin"


async def test_mark_compiled_records_output_pages(store):
    cid = await store.enqueue(_accept(), ["traj:1"], "h1")
    await store.mark_compiled(cid, ["entity.kevin"])
    record = await store.get_by_id(cid)
    assert record.status == "compiled"
    assert record.last_error and "entity.kevin" in record.last_error


async def test_mark_failed_stores_error(store):
    cid = await store.enqueue(_accept(), ["traj:1"], "h1")
    await store.mark_failed(cid, "oops")
    record = await store.get_by_id(cid)
    assert record.status == "failed"
    assert record.last_error == "oops"
