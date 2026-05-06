"""Phase 8B-2: TrustRootStore policy and verifier stub integration tests.

Verifies that TrustRootStore integrates correctly with verify_signature_stub,
never produces verified/trusted_signed, and doesn't alter CapabilityTrustPolicy.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.capabilities.provenance import (
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNTRUSTED,
    CapabilityTrustPolicy,
    compute_capability_tree_hash,
)
from src.capabilities.signature import (
    CapabilityTrustRoot,
    verify_signature_stub,
    write_signature,
)
from src.capabilities.signature import CapabilitySignature
from src.capabilities.trust_roots import TrustRootStore


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_capability_dir(tmp_path: Path, cap_id: str = "test-cap") -> Path:
    d = tmp_path / cap_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"id": cap_id, "name": "Test"}))
    (d / "CAPABILITY.md").write_text("# Test Capability")
    return d


def _write_signature(cap_dir: Path, tree_hash: str, trust_root_id: str | None = None) -> None:
    write_signature(cap_dir, CapabilitySignature(
        signed_tree_hash=tree_hash,
        trust_root_id=trust_root_id,
    ))


def _make_store_with_root(
    tmp_path: Path,
    trust_root_id: str = "tr-1",
    status: str = "active",
    expires_at: str | None = None,
) -> TrustRootStore:
    store = TrustRootStore(data_dir=tmp_path)
    store.create_trust_root(CapabilityTrustRoot(
        trust_root_id=trust_root_id,
        name=f"Test Root {trust_root_id}",
        key_type="ed25519",
        public_key_fingerprint=f"sha256:fp_{trust_root_id}",
        status=status,
        expires_at=expires_at,
    ))
    return store


# ── Verifier Stub Integration ────────────────────────────────────────────

class TestVerifierStubWithTrustRootStore:
    """verify_signature_stub accepts a TrustRootStore via duck-typing."""

    def test_store_passed_directly_to_stub(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1", status="active")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.code == "hash_consistent_unverified"

    def test_active_root_with_matching_hash_present_unverified(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-active")
        store = _make_store_with_root(tmp_path, "tr-active", status="active")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.allowed is True

    def test_disabled_root_returns_invalid(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-disabled")
        store = _make_store_with_root(tmp_path, "tr-disabled", status="disabled")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "trust_root_disabled"
        assert result.allowed is False

    def test_revoked_root_returns_invalid(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-revoked")
        store = _make_store_with_root(tmp_path, "tr-revoked", status="revoked")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "trust_root_revoked"
        assert result.allowed is False

    def test_expired_root_returns_invalid(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-expired")
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        store = _make_store_with_root(tmp_path, "tr-expired", status="active", expires_at=past)

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "trust_root_expired"
        assert result.allowed is False

    def test_missing_trust_root_returns_unknown(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-missing")
        store = TrustRootStore(data_dir=tmp_path)  # empty store

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.code == "unknown_trust_root"
        assert result.allowed is True

    def test_empty_store_does_not_break_verification(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="some-root")
        store = TrustRootStore(data_dir=tmp_path)

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.code == "unknown_trust_root"

    def test_no_signature_file_with_store_still_not_present(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        store = _make_store_with_root(tmp_path, "tr-1")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_NOT_PRESENT
        assert result.code == "no_signature"

    def test_hash_mismatch_with_store_returns_invalid(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        _write_signature(cap_dir, "wrong-hash", trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "tree_hash_mismatch"


# ── Never Verified / Never Trusted Signed ────────────────────────────────

class TestNeverVerified:
    """Hard constraints: no verified, no trusted_signed in any path."""

    def test_active_root_never_produces_verified(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1", status="active")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status != "verified"

    def test_active_root_never_produces_trusted_signed(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1", status="active")

        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.trust_level_recommendation != TRUST_TRUSTED_SIGNED

    def test_all_result_codes_never_verified(self, tmp_path):
        """Every possible trust root state is tested — none produce verified."""
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)

        # Test active, disabled, revoked, expired, missing — all via store
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="active-root", name="A", key_type="ed25519",
            public_key_fingerprint="sha256:aaa", status="active",
        ))
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="disabled-root", name="D", key_type="ed25519",
            public_key_fingerprint="sha256:ddd", status="disabled",
        ))
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="revoked-root", name="R", key_type="ed25519",
            public_key_fingerprint="sha256:rrr", status="revoked",
        ))
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="expired-root", name="E", key_type="ed25519",
            public_key_fingerprint="sha256:eee", status="active",
            expires_at=past,
        ))

        for root_id in ["active-root", "disabled-root", "revoked-root", "expired-root", "missing-root"]:
            _write_signature(cap_dir, tree_hash, trust_root_id=root_id)
            # Re-write signature for each iteration (idempotent)
            result = verify_signature_stub(cap_dir, trust_roots=store)
            assert result.signature_status != "verified", f"{root_id} produced verified"
            assert result.trust_level_recommendation != TRUST_TRUSTED_SIGNED, \
                f"{root_id} produced trusted_signed"


# ── TrustPolicy Isolation ────────────────────────────────────────────────

class TestTrustPolicyIsolation:
    """TrustRootStore must not alter CapabilityTrustPolicy behavior."""

    def test_policy_does_not_import_trust_roots(self):
        """CapabilityTrustPolicy has no knowledge of TrustRootStore."""
        import inspect
        source = inspect.getsource(CapabilityTrustPolicy)
        assert "TrustRootStore" not in source
        assert "trust_roots" not in source

    def test_policy_unaffected_by_store_existence(self, tmp_path):
        """Creating a TrustRootStore doesn't change policy decisions."""
        from src.capabilities.provenance import CapabilityProvenance

        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="tr-1", name="TR", key_type="ed25519",
            public_key_fingerprint="sha256:abc", status="active",
        ))

        policy = CapabilityTrustPolicy()
        provenance = CapabilityProvenance(
            provenance_id="prov-1",
            capability_id="cap-1",
            trust_level=TRUST_UNTRUSTED,
        )
        decision = policy.evaluate_provenance(provenance)
        assert decision.allowed is True
        assert decision.code == "provenance_evaluated"


# ── Deterministic and Non-Mutating ───────────────────────────────────────

class TestDeterministicAndNonMutating:
    def test_same_input_same_output(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1")

        r1 = verify_signature_stub(cap_dir, trust_roots=store)
        r2 = verify_signature_stub(cap_dir, trust_roots=store)
        assert r1.code == r2.code
        assert r1.signature_status == r2.signature_status
        assert r1.allowed == r2.allowed

    def test_verifier_does_not_mutate_store(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1")

        before = store.list_trust_roots()
        verify_signature_stub(cap_dir, trust_roots=store)
        after = store.list_trust_roots()
        assert len(before) == len(after)
        assert before[0].status == after[0].status

    def test_verifier_does_not_write_files(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1")

        before_files = set(store._roots_dir.glob("*.json")) if store._roots_dir.is_dir() else set()
        verify_signature_stub(cap_dir, trust_roots=store)
        after_files = set(store._roots_dir.glob("*.json")) if store._roots_dir.is_dir() else set()
        assert before_files == after_files

    def test_verifier_does_not_update_provenance(self, tmp_path):
        from src.capabilities.provenance import CapabilityProvenance, write_provenance
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1")

        # Write a provenance record
        write_provenance(cap_dir, capability_id="test-cap")
        before = (cap_dir / "provenance.json").read_text(encoding="utf-8")

        verify_signature_stub(cap_dir, trust_roots=store)
        after = (cap_dir / "provenance.json").read_text(encoding="utf-8")
        assert before == after

    def test_verifier_does_not_update_signature_json(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1")

        sig_path = cap_dir / "signature.json"
        before_stat = sig_path.stat()

        verify_signature_stub(cap_dir, trust_roots=store)
        after_stat = sig_path.stat()
        # mtime and size unchanged — no write occurred
        assert before_stat.st_mtime == after_stat.st_mtime
        assert before_stat.st_size == after_stat.st_size

    def test_verifier_does_not_update_trust_root_files(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1")

        root_path = store._roots_dir / "tr-1.json"
        before_stat = root_path.stat()

        verify_signature_stub(cap_dir, trust_roots=store)
        after_stat = root_path.stat()
        assert before_stat.st_mtime == after_stat.st_mtime
        assert before_stat.st_size == after_stat.st_size


# ── Legacy Compatibility ─────────────────────────────────────────────────

class TestLegacyCompatibility:
    def test_no_signature_no_trust_roots_still_works(self, tmp_path):
        """Missing signature and no trust roots = not_present (legacy OK)."""
        cap_dir = _make_capability_dir(tmp_path)
        store = TrustRootStore(data_dir=tmp_path)
        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_NOT_PRESENT
        assert result.allowed is True

    def test_signature_without_trust_root_id_no_store(self, tmp_path):
        """Signature without trust_root_id, no trust roots → present_unverified."""
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id=None)
        store = TrustRootStore(data_dir=tmp_path)
        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.allowed is True

    def test_none_trust_roots_still_works(self, tmp_path):
        """Passing None for trust_roots should still work (Phase 8B-1 compat)."""
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        result = verify_signature_stub(cap_dir, trust_roots=None)
        # None → empty dict → trust_root_id not found → unknown_trust_root
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.code == "unknown_trust_root"

    def test_dict_trust_roots_still_works(self, tmp_path):
        """Passing a plain dict for trust_roots should still work."""
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        roots = {
            "tr-1": CapabilityTrustRoot(
                trust_root_id="tr-1", name="T", key_type="ed25519",
                public_key_fingerprint="sha256:abc", status="active",
            ),
        }
        result = verify_signature_stub(cap_dir, trust_roots=roots)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED


# ── Disabled / Revoked After Store Create ────────────────────────────────

class TestDisabledRevokedCannotVerify:
    def test_disable_then_verify_returns_invalid(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1", status="active")

        # Before disable
        r1 = verify_signature_stub(cap_dir, trust_roots=store)
        assert r1.signature_status == SIGNATURE_PRESENT_UNVERIFIED

        # After disable
        store.disable_trust_root("tr-1")
        r2 = verify_signature_stub(cap_dir, trust_roots=store)
        assert r2.signature_status == SIGNATURE_INVALID
        assert r2.code == "trust_root_disabled"

    def test_revoke_then_verify_returns_invalid(self, tmp_path):
        cap_dir = _make_capability_dir(tmp_path)
        tree_hash = compute_capability_tree_hash(cap_dir)
        _write_signature(cap_dir, tree_hash, trust_root_id="tr-1")
        store = _make_store_with_root(tmp_path, "tr-1", status="active")

        store.revoke_trust_root("tr-1")
        result = verify_signature_stub(cap_dir, trust_roots=store)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "trust_root_revoked"


# ── as_verifier_dict ─────────────────────────────────────────────────────

class TestAsVerifierDictIntegration:
    def test_all_roots_in_verifier_dict(self, tmp_path):
        """All roots returned — verifier needs disabled/revoked/expired for proper invalid decisions."""
        store = TrustRootStore(data_dir=tmp_path)
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="active-1", name="A1", key_type="ed25519",
            public_key_fingerprint="sha256:a1", status="active",
        ))
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="disabled-1", name="D1", key_type="ed25519",
            public_key_fingerprint="sha256:d1", status="disabled",
        ))
        store.create_trust_root(CapabilityTrustRoot(
            trust_root_id="active-expired", name="AE", key_type="ed25519",
            public_key_fingerprint="sha256:ae", status="active",
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        ))

        d = store.as_verifier_dict()
        assert "active-1" in d
        assert "disabled-1" in d
        assert "active-expired" in d
        assert len(d) == 3

    def test_verifier_dict_objects_are_capability_trust_roots(self, tmp_path):
        store = _make_store_with_root(tmp_path, "tr-1")
        d = store.as_verifier_dict()
        for root in d.values():
            assert isinstance(root, CapabilityTrustRoot)
