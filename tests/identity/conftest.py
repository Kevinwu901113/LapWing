import json
import pytest
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from src.identity.auth import create_kevin_auth, create_system_auth, create_lapwing_auth, AuthContext
from src.identity.models import (
    IdentityClaim, ClaimRevision, ClaimType, ClaimOwner, ClaimStatus,
    Sensitivity, RevisionAction, GateEvent, GateOutcome, GatePassReason,
    GateLevel, ConflictEvent, ConflictType, RetrievalTrace, InjectionTrace,
    AuditLogEntry, AuditAction, OverrideToken, ClaimEvidence,
    ClaimSourceMapping, ContextProfile,
    compute_raw_block_id, compute_claim_id, compute_claim_id_from_key,
)
from src.identity.flags import IdentityFlags


@pytest.fixture
async def store(tmp_path):
    """Provides a fresh IdentityStore backed by a temp DB."""
    from src.identity.store import IdentityStore
    s = IdentityStore(db_path=tmp_path / "identity.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def kevin_auth():
    return create_kevin_auth(session_id="test-session")


@pytest.fixture
def system_auth():
    return create_system_auth(session_id="test-system")


@pytest.fixture
def lapwing_auth():
    return create_lapwing_auth(session_id="test-lapwing")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_test_claim(
    *,
    id: str = "c1",
    text: str = "Test claim",
    claim_type: ClaimType = ClaimType.VALUE,
    owner: ClaimOwner = ClaimOwner.LAPWING,
    source_file: str = "soul.md",
    stable_block_key: str = "test_key",
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    status: ClaimStatus = ClaimStatus.ACTIVE,
    confidence: float = 0.8,
    tags: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    **kwargs,
) -> IdentityClaim:
    """Factory for IdentityClaim with sensible defaults."""
    raw_block_id = compute_raw_block_id(source_file, stable_block_key)
    claim_local_key = kwargs.get("claim_local_key", "claim_0")
    claim_id = kwargs.get("claim_id", id) if "claim_id" in kwargs else id
    now = _now_iso()
    return IdentityClaim(
        claim_id=claim_id,
        raw_block_id=raw_block_id,
        claim_local_key=claim_local_key,
        source_file=source_file,
        stable_block_key=stable_block_key,
        claim_type=claim_type,
        owner=owner,
        predicate="has_value" if claim_type == ClaimType.VALUE else "has_trait",
        object_val=text,
        confidence=confidence,
        sensitivity=sensitivity,
        status=status,
        tags=tags or [],
        evidence_ids=evidence_ids or [],
        created_at=now,
        updated_at=now,
    )


def _make_create_revision(
    claim: IdentityClaim,
    *,
    actor: str = "kevin",
    reason: str = "initial creation",
) -> ClaimRevision:
    """Factory for CREATE ClaimRevision from a claim."""
    return ClaimRevision(
        revision_id=str(uuid4()),
        claim_id=claim.claim_id,
        action=RevisionAction.CREATED,
        old_snapshot=None,
        new_snapshot={
            "claim_id": claim.claim_id,
            "claim_type": claim.claim_type.value if isinstance(claim.claim_type, ClaimType) else claim.claim_type,
            "owner": claim.owner.value if isinstance(claim.owner, ClaimOwner) else claim.owner,
            "predicate": claim.predicate,
            "object_val": claim.object_val,
            "confidence": claim.confidence,
            "sensitivity": claim.sensitivity.value if isinstance(claim.sensitivity, Sensitivity) else claim.sensitivity,
            "status": claim.status.value if isinstance(claim.status, ClaimStatus) else claim.status,
            "tags": claim.tags,
            "source_file": claim.source_file,
            "stable_block_key": claim.stable_block_key,
            "raw_block_id": claim.raw_block_id,
            "claim_local_key": claim.claim_local_key,
        },
        actor=actor,
        reason=reason,
        created_at=_now_iso(),
    )


def _make_update_revision(
    old_claim: IdentityClaim,
    new_claim: IdentityClaim,
    *,
    actor: str = "kevin",
    reason: str = "updated",
) -> ClaimRevision:
    """Factory for UPDATE ClaimRevision."""
    return ClaimRevision(
        revision_id=str(uuid4()),
        claim_id=new_claim.claim_id,
        action=RevisionAction.UPDATED,
        old_snapshot={
            "object_val": old_claim.object_val,
            "confidence": old_claim.confidence,
        },
        new_snapshot={
            "claim_id": new_claim.claim_id,
            "claim_type": new_claim.claim_type.value if isinstance(new_claim.claim_type, ClaimType) else new_claim.claim_type,
            "owner": new_claim.owner.value if isinstance(new_claim.owner, ClaimOwner) else new_claim.owner,
            "predicate": new_claim.predicate,
            "object_val": new_claim.object_val,
            "confidence": new_claim.confidence,
            "sensitivity": new_claim.sensitivity.value if isinstance(new_claim.sensitivity, Sensitivity) else new_claim.sensitivity,
            "status": new_claim.status.value if isinstance(new_claim.status, ClaimStatus) else new_claim.status,
            "tags": new_claim.tags,
            "source_file": new_claim.source_file,
            "stable_block_key": new_claim.stable_block_key,
            "raw_block_id": new_claim.raw_block_id,
            "claim_local_key": new_claim.claim_local_key,
        },
        actor=actor,
        reason=reason,
        created_at=_now_iso(),
    )


def _make_gate_event(
    *,
    claim_id: str = "c1",
    outcome: GateOutcome = GateOutcome.PASSED,
    pass_reason: GatePassReason = GatePassReason.NORMAL,
    gate_level: GateLevel = GateLevel.LOG,
    context_profile: ContextProfile | None = None,
) -> GateEvent:
    return GateEvent(
        event_id=str(uuid4()),
        claim_id=claim_id,
        outcome=outcome,
        pass_reason=pass_reason,
        gate_level=gate_level,
        context_profile=context_profile,
        signals={},
        created_at=_now_iso(),
    )


def _make_retrieval_trace(
    *,
    query: str = "test query",
    candidate_ids: list[str] | None = None,
    selected_ids: list[str] | None = None,
    redacted_ids: list[str] | None = None,
    context_profile: ContextProfile | None = None,
) -> RetrievalTrace:
    return RetrievalTrace(
        trace_id=str(uuid4()),
        query=query,
        context_profile=context_profile,
        candidate_ids=candidate_ids or ["c1", "c2"],
        selected_ids=selected_ids or ["c1"],
        redacted_ids=redacted_ids or [],
        latency_ms=5.0,
        created_at=_now_iso(),
    )


def _make_injection_trace(
    *,
    retrieval_trace_id: str = "rt1",
    claim_ids: list[str] | None = None,
    token_count: int = 100,
    budget_total: int = 500,
) -> InjectionTrace:
    return InjectionTrace(
        trace_id=str(uuid4()),
        retrieval_trace_id=retrieval_trace_id,
        claim_ids=claim_ids or ["c1"],
        token_count=token_count,
        budget_total=budget_total,
        created_at=_now_iso(),
    )


def _make_audit_entry(
    *,
    action: AuditAction = AuditAction.CLAIM_CREATED,
    claim_id: str | None = "c1",
    actor: str = "kevin",
    details: dict | None = None,
) -> AuditLogEntry:
    return AuditLogEntry(
        entry_id=str(uuid4()),
        action=action,
        claim_id=claim_id,
        actor=actor,
        details=details or {},
        created_at=_now_iso(),
    )


def _make_conflict_event(
    *,
    claim_id_a: str = "c1",
    claim_id_b: str = "c2",
    conflict_type: ConflictType = ConflictType.CONTRADICTS,
) -> ConflictEvent:
    return ConflictEvent(
        event_id=str(uuid4()),
        claim_id_a=claim_id_a,
        claim_id_b=claim_id_b,
        conflict_type=conflict_type,
        resolution=None,
        resolved=False,
        created_at=_now_iso(),
    )


def _make_evidence(
    *,
    claim_id: str = "c1",
    evidence_type: str = "episode",
    content: str = "Supporting evidence text",
    source_ref: str | None = None,
) -> ClaimEvidence:
    return ClaimEvidence(
        evidence_id=str(uuid4()),
        claim_id=claim_id,
        evidence_type=evidence_type,
        content=content,
        source=source_ref,
        created_at=_now_iso(),
    )


def _make_override_token(
    *,
    claim_id: str = "c1",
    issuer: str = "kevin",
    reason: str = "manual override for testing",
    action_payload_hash: str = "hash123",
    expires_minutes: int = 60,
) -> OverrideToken:
    expires = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).isoformat()
    return OverrideToken(
        token_id=str(uuid4()),
        claim_id=claim_id,
        issuer=issuer,
        reason=reason,
        action_payload_hash=action_payload_hash,
        expires_at=expires,
        created_at=_now_iso(),
    )


@pytest.fixture
async def populated_store(store):
    """Store with 5 mixed-type active claims for retriever tests."""
    auth = create_kevin_auth(session_id="populate")
    types = [ClaimType.BELIEF, ClaimType.PREFERENCE, ClaimType.TRAIT, ClaimType.VALUE, ClaimType.RELATIONSHIP]
    for i, ct in enumerate(types):
        claim = _make_test_claim(
            id=f"pop_{i}",
            text=f"Claim {i} of type {ct.value}",
            claim_type=ct,
            stable_block_key=f"pop_key_{i}",
        )
        await store.append_revision(_make_create_revision(claim), auth)
    return store


@pytest.fixture
async def populated_store_with_50_claims(store):
    """Store with 50 claims for performance testing."""
    auth = create_kevin_auth(session_id="perf-populate")
    types = list(ClaimType)
    for i in range(50):
        ct = types[i % len(types)]
        claim = _make_test_claim(
            id=f"perf_{i}",
            text=f"Performance test claim {i}",
            claim_type=ct,
            stable_block_key=f"perf_key_{i}",
        )
        await store.append_revision(_make_create_revision(claim), auth)
    return store
