import pytest
from src.identity.models import (
    ClaimType, ClaimOwner, ClaimStatus, Sensitivity, RevisionAction,
    GateOutcome, GatePassReason, AuditAction, ConflictType,
    compute_raw_block_id, compute_claim_id,
)

def test_claim_type_values():
    assert ClaimType.BELIEF == "belief"
    assert ClaimType.MEMORY_ANCHOR == "memory_anchor"
    assert len(ClaimType) == 7

def test_claim_status_includes_redacted_erased():
    assert ClaimStatus.REDACTED == "redacted"
    assert ClaimStatus.ERASED == "erased"

def test_gate_pass_reason_includes_addendum():
    # per Addendum P0.5
    assert GatePassReason.COMPONENT_DISABLED == "component_disabled"
    assert GatePassReason.KILLSWITCH_ON == "killswitch_on"

def test_raw_block_id_deterministic():
    id1 = compute_raw_block_id("soul.md", "honesty_over_comfort")
    id2 = compute_raw_block_id("soul.md", "honesty_over_comfort")
    assert id1 == id2
    assert len(id1) == 16

def test_raw_block_id_differs_by_file():
    id1 = compute_raw_block_id("soul.md", "key1")
    id2 = compute_raw_block_id("voice.md", "key1")
    assert id1 != id2

def test_claim_id_does_not_depend_on_classification():
    """claim_id depends only on raw_block_id + claim_local_key, not type/predicate/etc."""
    raw_id = compute_raw_block_id("soul.md", "honesty")
    cid = compute_claim_id(raw_id, "claim_0")
    assert len(cid) == 16
    # Same raw_block_id + claim_local_key = same claim_id regardless of classification
    cid2 = compute_claim_id(raw_id, "claim_0")
    assert cid == cid2

def test_claim_id_differs_by_local_key():
    raw_id = compute_raw_block_id("soul.md", "honesty")
    cid0 = compute_claim_id(raw_id, "claim_0")
    cid1 = compute_claim_id(raw_id, "claim_1")
    assert cid0 != cid1
