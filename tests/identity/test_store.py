import pytest
from src.identity.store import IdentityStore
from src.identity.auth import create_kevin_auth
from tests.identity.conftest import (
    _make_test_claim,
    _make_create_revision,
    _make_update_revision,
    _make_gate_event,
    _make_retrieval_trace,
    _make_injection_trace,
    _make_audit_entry,
    _make_conflict_event,
    _make_override_token,
)


# ---------------------------------------------------------------------------
# Task 5: DB init + basic read/write
# ---------------------------------------------------------------------------


async def test_init_creates_tables(store):
    tables = await store._get_tables()
    assert "identity_claims" in tables
    assert "identity_revisions" in tables
    assert "identity_claim_sources" in tables
    assert "identity_redaction_tombstones" in tables


async def test_migration_version(store):
    version = await store._get_migration_version()
    assert version == 2


async def test_save_auth_context(store):
    auth = create_kevin_auth(session_id="s1")
    ctx_id = await store.save_auth_context(auth)
    assert ctx_id is not None
    assert isinstance(ctx_id, str)


async def test_save_feature_flags_dedup(store):
    """per Addendum P1.3: same flags -> same snapshot_id"""
    flags = {"parser_enabled": True, "gate_enabled": False}
    id1 = await store.save_feature_flags_snapshot(flags)
    id2 = await store.save_feature_flags_snapshot(flags)
    assert id1 == id2


# ---------------------------------------------------------------------------
# Task 6: Event sourcing core
# ---------------------------------------------------------------------------


async def test_append_revision_creates_claim(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", text="Lapwing values honesty")
    revision = _make_create_revision(claim)
    await store.append_revision(revision, auth)
    result = await store.get_claim("c1", auth)
    assert result is not None
    assert result.object_val == "Lapwing values honesty"


async def test_append_revision_transactional(store):
    """revision + projection + outbox in single transaction"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    revision = _make_create_revision(claim)
    await store.append_revision(revision, auth)
    revisions = await store.get_revisions("c1", auth)
    assert len(revisions) == 1
    outbox = await store._get_pending_outbox()
    assert len(outbox) == 1


async def test_rebuild_projection_matches_incremental(store):
    """acceptance #9: rebuild_projection equals incremental materialize"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", text="v1")
    await store.append_revision(_make_create_revision(claim), auth)
    claim2 = _make_test_claim(id="c1", text="v2")
    await store.append_revision(_make_update_revision(claim, claim2), auth)
    incremental = await store.get_claim("c1", auth)
    await store.rebuild_projection(auth)
    rebuilt = await store.get_claim("c1", auth)
    assert incremental.object_val == rebuilt.object_val == "v2"


async def test_list_claims(store):
    auth = create_kevin_auth(session_id="s1")
    for i in range(3):
        claim = _make_test_claim(id=f"c{i}", stable_block_key=f"key_{i}")
        await store.append_revision(_make_create_revision(claim), auth)
    claims = await store.list_claims(auth)
    assert len(claims) == 3


async def test_deprecate_claim(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.deprecate_claim("c1", auth, "no longer relevant")
    result = await store.get_claim("c1", auth)
    assert result.status == "deprecated"


# ---------------------------------------------------------------------------
# Task 7: Trace + Event Writers
# ---------------------------------------------------------------------------


async def test_write_gate_event(store):
    event = _make_gate_event()
    await store.write_gate_event(event)
    events = await store._list_gate_events()
    assert len(events) == 1


async def test_write_retrieval_trace(store):
    trace = _make_retrieval_trace()
    await store.write_retrieval_trace(trace)


async def test_write_injection_trace(store):
    trace = _make_injection_trace()
    await store.write_injection_trace(trace)


async def test_write_audit_log(store):
    auth = create_kevin_auth(session_id="s1")
    entry = _make_audit_entry()
    await store.write_audit_log(entry, auth)


async def test_write_conflict_event(store):
    event = _make_conflict_event()
    await store.write_conflict_event(event)


async def test_create_and_consume_override(store):
    auth = create_kevin_auth(session_id="s1")
    token = _make_override_token()
    await store.create_override_token(token, auth)
    consumed = await store.consume_override_token(token.token_id, token.action_payload_hash, auth)
    assert consumed is True
    consumed2 = await store.consume_override_token(token.token_id, token.action_payload_hash, auth)
    assert consumed2 is False  # already consumed


# ---------------------------------------------------------------------------
# Task 10: Extraction Cache + Gate Cache + Outbox
# ---------------------------------------------------------------------------


async def test_extraction_cache_hit(store):
    await store.set_extraction_cache("key1", {"type": "belief", "confidence": 0.9})
    result = await store.get_extraction_cache("key1")
    assert result["type"] == "belief"


async def test_extraction_cache_miss(store):
    result = await store.get_extraction_cache("nonexistent")
    assert result is None


async def test_clear_extraction_cache(store):
    auth = create_kevin_auth(session_id="s1")
    await store.set_extraction_cache("key1", {"type": "belief"})
    count = await store.clear_extraction_cache("all", None, auth)
    assert count == 1


async def test_gate_cache_hit(store):
    await store.set_gate_cache("gk1", {"outcome": "pass"}, ttl_seconds=3600)
    result = await store.get_gate_cache("gk1")
    assert result["outcome"] == "pass"


async def test_gate_cache_expired(store):
    await store.set_gate_cache("gk1", {"outcome": "pass"}, ttl_seconds=-1)
    result = await store.get_gate_cache("gk1")
    assert result is None


async def test_outbox_enqueue_and_drain(store):
    await store.enqueue_outbox("c1", "upsert_vector")
    pending = await store._get_pending_outbox()
    assert len(pending) == 1
    count = await store.drain_outbox(batch_size=10)
    assert count >= 1
    pending_after = await store._get_pending_outbox()
    assert len(pending_after) == 0
