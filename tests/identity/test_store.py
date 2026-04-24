import pytest
from src.identity.store import IdentityStore
from src.identity.auth import create_kevin_auth
from tests.identity.conftest import (
    _make_test_claim,
    _make_create_revision,
    _make_update_revision,
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
