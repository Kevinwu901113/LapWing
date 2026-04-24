import pytest
from src.identity.auth import create_kevin_auth
from tests.identity.conftest import (
    _make_test_claim,
    _make_create_revision,
    _make_audit_entry,
    _make_evidence,
)


# ---------------------------------------------------------------------------
# Task 9: Append-Only Triggers + Relations/Evidence + Claim Sources
# ---------------------------------------------------------------------------


async def test_revisions_no_update(store):
    """per Addendum P0.4: identity_revisions rejects UPDATE"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute(
            "UPDATE identity_revisions SET reason='x' WHERE claim_id='c1'"
        )


async def test_revisions_no_delete(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute(
            "DELETE FROM identity_revisions WHERE claim_id='c1'"
        )


async def test_audit_log_no_update(store):
    auth = create_kevin_auth(session_id="s1")
    entry = _make_audit_entry()
    await store.write_audit_log(entry, auth)
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute(
            "UPDATE identity_audit_log SET justification='x'"
        )


async def test_tombstones_no_delete(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", source_file="soul.md", stable_block_key="k1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "reason")
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute(
            "DELETE FROM identity_redaction_tombstones"
        )


async def test_explicit_access_allows_update(store):
    """per Addendum P0.4: UPDATE allowed for consumed marking"""
    auth = create_kevin_auth(session_id="s1")
    req_id = await store.create_explicit_access_request(
        actor_id="kevin",
        scope="sensitive.restricted.explicit",
        target_claim_ids=["c1"],
        ttl_seconds=60,
        auth=auth,
    )
    await store._raw_execute(
        f"UPDATE identity_explicit_access_requests SET consumed=1 WHERE request_id='{req_id}'"
    )


async def test_explicit_access_no_delete(store):
    auth = create_kevin_auth(session_id="s1")
    req_id = await store.create_explicit_access_request(
        actor_id="kevin",
        scope="sensitive.restricted.explicit",
        target_claim_ids=["c1"],
        ttl_seconds=60,
        auth=auth,
    )
    with pytest.raises(Exception, match="delete-protected"):
        await store._raw_execute(
            f"DELETE FROM identity_explicit_access_requests WHERE request_id='{req_id}'"
        )


async def test_add_and_get_evidence(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    ev = _make_evidence(claim_id="c1")
    await store.add_evidence(ev, auth)
    result = await store.get_evidence("c1", auth)
    assert len(result) == 1


async def test_add_and_get_relation(store):
    auth = create_kevin_auth(session_id="s1")
    for cid in ["c1", "c2"]:
        await store.append_revision(
            _make_create_revision(
                _make_test_claim(id=cid, stable_block_key=f"key_{cid}")
            ),
            auth,
        )
    await store.add_relation("c1", "c2", "supports", 0.8, auth)
    neighbors = await store.get_neighbors("c1", auth)
    assert len(neighbors) == 1


async def test_upsert_claim_sources(store):
    """per Addendum P0.3: provenance in separate table"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", source_file="soul.md")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.upsert_claim_source("c1", "soul.md", 0, 100, "sha1", "key1")
    sources = await store.get_claim_sources("c1")
    assert len(sources) == 1
    assert sources[0]["source_span_start"] == 0
