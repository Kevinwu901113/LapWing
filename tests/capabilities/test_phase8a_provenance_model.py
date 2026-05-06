"""Phase 8A-1: CapabilityProvenance model tests — serialization, validation, enums."""

from __future__ import annotations

import json

import pytest

from src.capabilities.provenance import (
    PROVENANCE_INTEGRITY_STATUSES,
    PROVENANCE_SIGNATURE_STATUSES,
    PROVENANCE_SOURCE_TYPES,
    PROVENANCE_TRUST_LEVELS,
    INTEGRITY_MISMATCH,
    INTEGRITY_UNKNOWN,
    INTEGRITY_VERIFIED,
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    SIGNATURE_VERIFIED,
    SOURCE_LOCAL_PACKAGE,
    SOURCE_MANUAL_DRAFT,
    SOURCE_QUARANTINE_ACTIVATION,
    SOURCE_UNKNOWN,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNKNOWN,
    TRUST_UNTRUSTED,
    CapabilityProvenance,
    TrustDecision,
)


class TestCapabilityProvenanceModel:
    """CapabilityProvenance dataclass: defaults, serialization, round-trip."""

    def test_defaults(self):
        p = CapabilityProvenance(provenance_id="prov_abc", capability_id="test-1")
        assert p.provenance_id == "prov_abc"
        assert p.capability_id == "test-1"
        assert p.source_type == SOURCE_UNKNOWN
        assert p.source_path_hash is None
        assert p.source_content_hash == ""
        assert p.imported_at is None
        assert p.imported_by is None
        assert p.activated_at is None
        assert p.activated_by is None
        assert p.parent_provenance_id is None
        assert p.origin_capability_id is None
        assert p.origin_scope is None
        assert p.trust_level == TRUST_UNTRUSTED
        assert p.integrity_status == INTEGRITY_UNKNOWN
        assert p.signature_status == SIGNATURE_NOT_PRESENT
        assert p.metadata == {}

    def test_to_dict_contains_all_keys(self):
        p = CapabilityProvenance(
            provenance_id="prov_test1",
            capability_id="cap-1",
            source_type=SOURCE_LOCAL_PACKAGE,
            source_path_hash="abc123",
            source_content_hash="def456",
            trust_level=TRUST_UNTRUSTED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        d = p.to_dict()
        expected_keys = {
            "provenance_id", "capability_id", "source_type", "source_path_hash",
            "source_content_hash", "imported_at", "imported_by",
            "activated_at", "activated_by", "parent_provenance_id",
            "origin_capability_id", "origin_scope", "trust_level",
            "integrity_status", "signature_status", "metadata",
        }
        assert set(d.keys()) == expected_keys

    def test_round_trip_to_dict_from_dict(self):
        p1 = CapabilityProvenance(
            provenance_id="prov_r1",
            capability_id="cap-r1",
            source_type=SOURCE_QUARANTINE_ACTIVATION,
            source_path_hash="hash123",
            source_content_hash="content456",
            imported_at="2026-05-01T00:00:00+00:00",
            imported_by="test-user",
            activated_at="2026-05-02T00:00:00+00:00",
            activated_by="test-user",
            parent_provenance_id="prov_parent",
            origin_capability_id="cap-origin",
            origin_scope="quarantine",
            trust_level=TRUST_REVIEWED,
            integrity_status=INTEGRITY_VERIFIED,
            signature_status=SIGNATURE_NOT_PRESENT,
            metadata={"plan_id": "qap_abc", "request_id": "qtr_def"},
        )
        p2 = CapabilityProvenance.from_dict(p1.to_dict())
        assert p2.provenance_id == p1.provenance_id
        assert p2.capability_id == p1.capability_id
        assert p2.source_type == p1.source_type
        assert p2.source_path_hash == p1.source_path_hash
        assert p2.source_content_hash == p1.source_content_hash
        assert p2.trust_level == p1.trust_level
        assert p2.integrity_status == p1.integrity_status
        assert p2.signature_status == p1.signature_status
        assert p2.parent_provenance_id == p1.parent_provenance_id
        assert p2.origin_capability_id == p1.origin_capability_id
        assert p2.origin_scope == p1.origin_scope
        assert p2.metadata == p1.metadata

    def test_from_dict_missing_keys_use_defaults(self):
        p = CapabilityProvenance.from_dict({"provenance_id": "prov_min", "capability_id": "cap-min"})
        assert p.provenance_id == "prov_min"
        assert p.capability_id == "cap-min"
        assert p.source_type == SOURCE_UNKNOWN
        assert p.trust_level == TRUST_UNTRUSTED
        assert p.integrity_status == INTEGRITY_UNKNOWN
        assert p.signature_status == SIGNATURE_NOT_PRESENT
        assert p.metadata == {}

    def test_metadata_round_trip(self):
        p1 = CapabilityProvenance(
            provenance_id="prov_meta",
            capability_id="cap-meta",
            metadata={"nested": {"deep": [1, 2, 3]}, "flag": True, "count": 42},
        )
        p2 = CapabilityProvenance.from_dict(p1.to_dict())
        assert p2.metadata == {"nested": {"deep": [1, 2, 3]}, "flag": True, "count": 42}

    def test_to_dict_json_serializable(self):
        p = CapabilityProvenance(provenance_id="prov_json", capability_id="cap-json")
        json_str = json.dumps(p.to_dict())
        assert isinstance(json_str, str)
        reloaded = json.loads(json_str)
        assert reloaded["provenance_id"] == "prov_json"

    def test_provenance_id_is_path_safe(self):
        """provenance_id format prov_{hex12} is safe as a filename component."""
        import re
        for _ in range(20):
            p = CapabilityProvenance(provenance_id="", capability_id="test")
            pid = p.provenance_id
            if pid:
                assert re.match(r"^prov_[a-f0-9]{12}$", pid), f"Bad id: {pid}"
                assert "/" not in pid
                assert "\\" not in pid
                assert ".." not in pid


class TestProvenanceEnums:
    """Enum constant validation."""

    def test_source_types(self):
        assert SOURCE_LOCAL_PACKAGE == "local_package"
        assert SOURCE_MANUAL_DRAFT == "manual_draft"
        assert SOURCE_QUARANTINE_ACTIVATION == "quarantine_activation"
        assert SOURCE_UNKNOWN == "unknown"
        assert "curator_proposal" in PROVENANCE_SOURCE_TYPES
        assert len(PROVENANCE_SOURCE_TYPES) == 5

    def test_trust_levels(self):
        assert TRUST_UNKNOWN == "unknown"
        assert TRUST_UNTRUSTED == "untrusted"
        assert TRUST_REVIEWED == "reviewed"
        assert TRUST_TRUSTED_LOCAL == "trusted_local"
        assert TRUST_TRUSTED_SIGNED == "trusted_signed"
        assert len(PROVENANCE_TRUST_LEVELS) == 5

    def test_integrity_statuses(self):
        assert INTEGRITY_UNKNOWN == "unknown"
        assert INTEGRITY_VERIFIED == "verified"
        assert INTEGRITY_MISMATCH == "mismatch"
        assert len(PROVENANCE_INTEGRITY_STATUSES) == 3

    def test_signature_statuses(self):
        assert SIGNATURE_NOT_PRESENT == "not_present"
        assert SIGNATURE_PRESENT_UNVERIFIED == "present_unverified"
        assert SIGNATURE_VERIFIED == "verified"
        assert SIGNATURE_INVALID == "invalid"
        assert len(PROVENANCE_SIGNATURE_STATUSES) == 4

    def test_trust_levels_distinct_from_maturity(self):
        from src.capabilities.schema import ALLOWED_MATURITIES
        for tl in PROVENANCE_TRUST_LEVELS:
            assert tl not in ALLOWED_MATURITIES

    def test_integrity_distinct_from_status(self):
        from src.capabilities.schema import ALLOWED_STATUSES
        for s in PROVENANCE_INTEGRITY_STATUSES:
            assert s not in ALLOWED_STATUSES


class TestTrustDecision:
    """TrustDecision dataclass and factory methods."""

    def test_defaults(self):
        d = TrustDecision(allowed=True)
        assert d.allowed is True
        assert d.severity == "info"
        assert d.code == ""
        assert d.message == ""
        assert d.details == {}

    def test_allow_factory(self):
        d = TrustDecision.allow("code1", "msg1", key="val")
        assert d.allowed is True
        assert d.severity == "info"
        assert d.code == "code1"
        assert d.message == "msg1"
        assert d.details == {"key": "val"}

    def test_warn_factory(self):
        d = TrustDecision.warn("warn1", "warning message", x=1)
        assert d.allowed is True
        assert d.severity == "warning"
        assert d.code == "warn1"
        assert d.details == {"x": 1}

    def test_deny_factory(self):
        d = TrustDecision.deny("deny1", "denied")
        assert d.allowed is False
        assert d.severity == "error"
        assert d.code == "deny1"
