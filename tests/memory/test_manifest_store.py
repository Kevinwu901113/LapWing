"""ManifestStore unit tests (Phase 1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.memory.manifest_store import ManifestEntry, ManifestStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
async def store(tmp_path):
    s = ManifestStore(
        db_path=tmp_path / "lapwing.db",
        logs_dir=tmp_path / "logs",
    )
    await s.init()
    yield s
    await s.close()


def _entry(**overrides) -> ManifestEntry:
    base = dict(
        source_id="trajectory:abc",
        source_type="trajectory",
        source_path=None,
        source_hash="hash-1",
        source_event_ids=["e1", "e2"],
        processed_at=_now(),
        output_page_ids=["entity.kevin"],
        dirty_entities=["entity.kevin"],
        gate_decision="accept",
        gate_score=0.9,
    )
    base.update(overrides)
    return ManifestEntry(**base)


async def test_record_and_query(store):
    row_id = await store.record_processing(_entry())
    assert row_id > 0
    assert await store.is_processed("trajectory:abc", "hash-1") is True


async def test_idempotent_on_duplicate(store):
    e = _entry()
    rid1 = await store.record_processing(e)
    rid2 = await store.record_processing(e)
    assert rid1 == rid2  # second insert returns existing row


async def test_changed_hash_is_not_processed(store):
    await store.record_processing(_entry(source_hash="hash-1"))
    assert await store.is_processed("trajectory:abc", "hash-2") is False


async def test_dirty_entity_tracking(store):
    await store.record_processing(_entry(dirty_entities=["entity.kevin", "entity.lapwing"]))
    dirty = await store.get_dirty_entities()
    assert sorted(dirty) == ["entity.kevin", "entity.lapwing"]

    await store.mark_entities_compiling(["entity.kevin"])
    remaining = await store.get_dirty_entities()
    assert remaining == ["entity.lapwing"]

    await store.mark_entities_clean(["entity.kevin"])
    remaining = await store.get_dirty_entities()
    assert remaining == ["entity.lapwing"]


async def test_provenance_query(store):
    await store.record_processing(_entry(
        source_id="trajectory:abc",
        output_page_ids=["entity.kevin"],
    ))
    await store.record_processing(_entry(
        source_id="trajectory:def",
        source_hash="hash-2",
        output_page_ids=["entity.kevin", "entity.lapwing"],
    ))
    rows = await store.get_provenance("entity.kevin")
    assert {r.source_id for r in rows} == {"trajectory:abc", "trajectory:def"}


async def test_jsonl_dual_write(tmp_path, store):
    await store.record_processing(_entry())
    logs_dir = tmp_path / "logs"
    files = list(logs_dir.glob("memory_manifest_*.jsonl"))
    assert len(files) == 1, f"expected one jsonl, got {files}"
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[0]
    payload = json.loads(line)
    assert payload["source_id"] == "trajectory:abc"
    assert payload["_row_id"] > 0
