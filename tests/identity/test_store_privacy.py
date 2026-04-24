import pytest
from src.identity.auth import create_kevin_auth
from tests.identity.conftest import (
    _make_test_claim,
    _make_create_revision,
    _make_evidence,
)


# ---------------------------------------------------------------------------
# Task 8: Privacy (Redact / Erase) + Tombstones
# ---------------------------------------------------------------------------


async def test_redact_requires_source_redaction(store, tmp_path):
    """per Addendum P0.2: redact returns requires_source_redaction when markdown source exists"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", source_file=str(tmp_path / "soul.md"))
    await store.append_revision(_make_create_revision(claim), auth)
    await store.add_evidence(
        _make_evidence(
            claim_id="c1",
            evidence_type="markdown_span",
            source_ref=str(tmp_path / "soul.md"),
        ),
        auth,
    )
    result = await store.redact_claim(
        "c1", auth, "privacy request", source_already_redacted=False
    )
    assert result.requires_source_redaction is True
    assert result.success is False


async def test_redact_proceeds_when_source_redacted(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.add_evidence(
        _make_evidence(claim_id="c1", evidence_type="markdown_span"), auth
    )
    result = await store.redact_claim(
        "c1", auth, "privacy", source_already_redacted=True
    )
    assert result.success is True
    redacted = await store.get_claim("c1", auth)
    assert redacted.status == "redacted"


async def test_erase_keeps_tombstone_row(store):
    """per Addendum P1.1: ERASED claim keeps projection row with cleared fields"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", text="sensitive info")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "gdpr request")
    erased = await store.get_claim("c1", auth)
    assert erased is not None
    assert erased.status == "erased"
    assert erased.object_val == ""
    assert erased.predicate == "[ERASED]"


async def test_erase_deletes_evidence_and_relations(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.add_evidence(_make_evidence(claim_id="c1"), auth)
    await store.erase_claim("c1", auth, "cleanup")
    evidence = await store.get_evidence("c1", auth)
    assert len(evidence) == 0


async def test_erase_writes_tombstone(store):
    """per Addendum P0.2: erase writes to identity_redaction_tombstones"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(
        id="c1", source_file="soul.md", stable_block_key="key1"
    )
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "reason")
    tombstones = await store._list_tombstones()
    assert len(tombstones) == 1
    assert tombstones[0]["source_file"] == "soul.md"
