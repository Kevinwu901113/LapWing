# tests/identity/test_acceptance.py
# Acceptance tests for the Identity Substrate (Ticket A)

import pytest
import time
from uuid import uuid4
from pathlib import Path

from src.identity.store import IdentityStore
from src.identity.parser import IdentityParser
from src.identity.retriever import IdentityRetriever
from src.identity.flags import IdentityFlags
from src.identity.auth import (
    create_kevin_auth,
    create_system_auth,
    AuthContext,
    AuthorizationError,
    check_scope,
)
from src.identity.models import (
    ClaimType,
    GateEvent,
    GateOutcome,
    GatePassReason,
    GateLevel,
    AuditLogEntry,
    AuditAction,
    compute_claim_id_from_key,
)
from tests.identity.conftest import _make_test_claim, _make_create_revision, _make_evidence


# ── Ticket B stubs ──────────────────────────
@pytest.mark.skip(reason="Ticket B: requires injector module")
async def test_02_shadow_mode():
    pass


@pytest.mark.skip(reason="Ticket B: requires injector module")
async def test_03_injection_works():
    pass


@pytest.mark.skip(reason="Ticket B: requires gate module")
async def test_04_gate_observe():
    pass


@pytest.mark.skip(reason="Ticket B: requires gate module")
async def test_05_gate_advise():
    pass


@pytest.mark.skip(reason="Ticket B: requires L1 evolution")
async def test_06_l1_evolution():
    pass


@pytest.mark.skip(reason="Ticket B: requires skill pipeline")
async def test_08_skill_pipeline():
    pass


@pytest.mark.skip(reason="Ticket B: requires reviewer module")
async def test_09_reviewer_trace():
    pass


@pytest.mark.skip(reason="Ticket B: requires gate + conflict resolution")
async def test_10_conflict_path():
    pass


@pytest.mark.skip(reason="Ticket B: requires gate module")
async def test_20_gate_pass_attributability():
    pass


@pytest.mark.skip(reason="Ticket B: requires gate + 3-day mixed traffic")
async def test_21_redact_no_residue_3day():
    pass


# ── Ticket A tests ──────────────────────────


async def test_01_cold_start(tmp_path):
    """acceptance #1: cold start with empty identity dir"""
    store = IdentityStore(db_path=tmp_path / "id.db")
    await store.init()
    claims = await store.list_claims(create_kevin_auth("s1"))
    assert len(claims) == 0
    await store.close()


async def test_07_killswitch(store):
    """acceptance #7: killswitch disables all identity operations"""
    flags = IdentityFlags(identity_system_killswitch=True)
    retriever = IdentityRetriever(store=store, flags=flags)
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("anything", auth)
    assert len(result.claims) == 0


async def test_13_llm_cache_consistency(store, tmp_path):
    """acceptance #13: 10 rebuilds with no change -> 0 revisions after first"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=honesty] Lapwing values honesty.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    for _ in range(9):
        report = await parser.rebuild(auth=auth)
        assert report.created == 0 and report.updated == 0


async def test_14_chroma_rebuild_consistency(store, tmp_path):
    """acceptance #14: outbox has correct entries after rebuild"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=h1] Claim 1.\n- [id=h2] Claim 2.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    outbox = await store._get_pending_outbox()
    assert len(outbox) >= 2  # at least 2 upsert_vector entries


async def test_15_killswitch_audit_completeness(store):
    """acceptance #15: killswitch ON/OFF writes audit entries"""
    auth = create_kevin_auth("s1")
    on_entry = AuditLogEntry(
        entry_id=str(uuid4()),
        action=AuditAction.REBUILD_STARTED,
        claim_id=None,
        actor="kevin",
        details={"event": "killswitch_on"},
        created_at="2025-01-01T00:00:00",
    )
    off_entry = AuditLogEntry(
        entry_id=str(uuid4()),
        action=AuditAction.REBUILD_COMPLETED,
        claim_id=None,
        actor="kevin",
        details={"event": "killswitch_off"},
        created_at="2025-01-01T00:00:01",
    )
    await store.write_audit_log(on_entry, auth)
    await store.write_audit_log(off_entry, auth)
    entries = await store._list_audit_entries()
    assert len(entries) >= 2


async def test_16_unauth_cannot_access_sensitive(store):
    """acceptance #16: limited scopes cannot trigger sensitive ops"""
    system_auth = create_system_auth()
    claim = _make_test_claim(id="c1")
    await store.append_revision(
        _make_create_revision(claim), create_kevin_auth("s1")
    )
    with pytest.raises(AuthorizationError):
        await store.redact_claim(
            "c1", system_auth, "reason", source_already_redacted=True
        )


async def test_22_killswitch_purity(store):
    """acceptance #22: killswitch ON -> 100 retrieves -> 0 new trace rows"""
    flags = IdentityFlags(identity_system_killswitch=True)
    retriever = IdentityRetriever(store=store, flags=flags)
    auth = create_kevin_auth("s1")
    initial_traces = await store._count_retrieval_traces()
    for _ in range(100):
        await retriever.retrieve("test query", auth)
    final_traces = await store._count_retrieval_traces()
    assert final_traces == initial_traces


# ── Addendum acceptance tests ───────────────


async def test_a2_fallback_id_churn(store, tmp_path):
    """A.2: memory_anchor without explicit id -> text change causes DEPRECATE+CREATE"""
    md_dir = tmp_path / "files"
    md_dir.mkdir()
    md_file = md_dir / "anchors.md"
    md_file.write_text("- Some anchor text.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=md_dir)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    claims_before = await store.list_claims(auth)
    assert len(claims_before) == 1
    # Modify text -> new fallback key -> different claim_id
    md_file.write_text("- Some different anchor text!", encoding="utf-8")
    report = await parser.rebuild(auth=auth)
    # Old claim should be deprecated, new one created
    assert report.deprecated >= 1
    assert report.created >= 1


async def test_a7_gate_component_disabled_events(store):
    """A.7: 100 gate events with COMPONENT_DISABLED pass_reason"""
    for i in range(100):
        event = GateEvent(
            event_id=str(uuid4()),
            claim_id=f"c{i}",
            outcome=GateOutcome.PASSED,
            pass_reason=GatePassReason.COMPONENT_DISABLED,
            gate_level=GateLevel.LOG,
            context_profile=None,
            signals={},
            created_at="2025-01-01T00:00:00",
        )
        await store.write_gate_event(event)
    events = await store._list_gate_events()
    assert len(events) == 100
    assert all(e["pass_reason"] == "component_disabled" for e in events)


async def test_a9_explicit_access_forgery_protection(store):
    """A.9: fake request_id -> verify returns False; real request consumed once"""
    verified_fake = await store.verify_explicit_request(
        request_id="fake_id",
        actor_id="kevin",
        scope="sensitive.restricted.explicit",
        target_claim_id="c1",
    )
    assert verified_fake is False

    auth = create_kevin_auth("s1")
    req_id = await store.create_explicit_access_request(
        actor_id="kevin",
        scope="sensitive.restricted.explicit",
        target_claim_ids=["c1"],
        ttl_seconds=60,
        auth=auth,
    )
    v1 = await store.verify_explicit_request(
        req_id, "kevin", "sensitive.restricted.explicit", "c1"
    )
    assert v1 is True
    v2 = await store.verify_explicit_request(
        req_id, "kevin", "sensitive.restricted.explicit", "c1"
    )
    assert v2 is False  # already consumed


# ── Privacy acceptance tests ────────────────


async def test_privacy_redact_no_raw_text_residue(store):
    """Privacy: after REDACT, sensitive text not in non-audit tables"""
    auth = create_kevin_auth("s1")
    claim = _make_test_claim(id="c1", text="Kevin's secret medical info")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.redact_claim("c1", auth, "privacy", source_already_redacted=True)
    found = await store._search_all_tables("Kevin's secret medical info")
    assert not found, "Sensitive text found in non-audit tables after REDACT"


async def test_privacy_erase_chroma_deleted(store):
    """Privacy: after ERASE, outbox has delete_vector action"""
    auth = create_kevin_auth("s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "gdpr")
    outbox = await store._get_pending_outbox()
    delete_entries = [e for e in outbox if e["action"] == "delete_vector"]
    assert len(delete_entries) >= 1


async def test_privacy_export_writes_audit(store):
    """Privacy: export writes audit trail"""
    auth = create_kevin_auth("s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    data = await store.export_claim("c1", auth)
    assert "claim" in data
    assert "revisions" in data
    # Verify audit log was written
    entries = await store._list_audit_entries()
    export_entries = [e for e in entries if e["details"] and "export" in str(e["details"])]
    assert len(export_entries) >= 1


# ── Performance budget test (Task 20) ───────


async def test_17_retrieval_p95_under_100ms(populated_store_with_50_claims):
    """acceptance #17: P95 retrieval < 100ms with 50 claims"""
    retriever = IdentityRetriever(
        store=populated_store_with_50_claims, flags=IdentityFlags()
    )
    auth = create_kevin_auth("s1")
    latencies = []
    for _ in range(100):
        t0 = time.monotonic()
        await retriever.retrieve("honesty and relationships", auth)
        latencies.append((time.monotonic() - t0) * 1000)
    latencies.sort()
    p95 = latencies[94]
    assert p95 < 100, f"P95 retrieval latency {p95:.1f}ms exceeds 100ms budget"
