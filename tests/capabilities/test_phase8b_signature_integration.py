"""Phase 8B-1: Signature integration tests.

Validates that signature.json I/O and the verifier stub integrate correctly
with provenance, tree hashing, and CapabilityTrustPolicy. No new behavior
changes to existing systems.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.capabilities.signature import (
    CapabilitySignature,
    parse_signature_dict,
    parse_trust_root_dict,
    read_signature,
    verify_signature_stub,
    write_signature,
)
from src.capabilities.provenance import (
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    SIGNATURE_VERIFIED,
    TRUST_REVIEWED,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNTRUSTED,
    CapabilityProvenance,
    CapabilityTrustPolicy,
    compute_capability_tree_hash,
    read_provenance,
    write_provenance,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_capability_dir(tmp_path: Path, *, cap_id: str = "test-cap") -> Path:
    d = tmp_path / cap_id
    d.mkdir()
    (d / "CAPABILITY.md").write_text(f"# {cap_id}\n", encoding="utf-8")
    manifest = {"id": cap_id, "name": cap_id.capitalize(), "version": "1.0.0"}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


# ── Tree hash excludes signature.json ───────────────────────────────────────


class TestTreeHashExcludesSignatureJson:
    """signature.json is excluded from tree hash to avoid self-reference."""

    def test_adding_signature_json_does_not_change_tree_hash(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        h1 = compute_capability_tree_hash(d)

        # Write signature.json
        sig = CapabilitySignature(signed_tree_hash="abc123", algorithm="ed25519")
        write_signature(d, sig)

        h2 = compute_capability_tree_hash(d)
        assert h1 == h2, "Tree hash changed when signature.json was added"

    def test_modifying_signature_json_does_not_change_tree_hash(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        sig = CapabilitySignature(signed_tree_hash="abc123", algorithm="ed25519")
        write_signature(d, sig)

        h1 = compute_capability_tree_hash(d)

        # Modify signature.json
        sig.signed_tree_hash = "def456"
        write_signature(d, sig)

        h2 = compute_capability_tree_hash(d)
        assert h1 == h2, "Tree hash changed when signature.json was modified"

    def test_deleting_signature_json_does_not_change_tree_hash(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        sig = CapabilitySignature(signed_tree_hash="abc123")
        write_signature(d, sig)

        h1 = compute_capability_tree_hash(d)
        (d / "signature.json").unlink()
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2, "Tree hash changed when signature.json was deleted"

    def test_signature_hash_exclusion_documented(self):
        """Verify the exclusion is explicitly listed in the tree hash config."""
        from src.capabilities.provenance import _TREE_HASH_EXCLUDED_FILES
        assert "signature.json" in _TREE_HASH_EXCLUDED_FILES


# ── Provenance unaffected by signature.json ──────────────────────────────────


class TestProvenanceUnaffectedBySignatureJson:
    """Provenance read/write is independent of signature.json."""

    def test_write_provenance_does_not_create_signature_json(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        write_provenance(d, capability_id="test-cap")
        assert (d / "provenance.json").is_file()
        assert not (d / "signature.json").is_file()

    def test_write_signature_does_not_modify_provenance(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        prov = write_provenance(d, capability_id="test-cap")
        orig_mtime = (d / "provenance.json").stat().st_mtime

        sig = CapabilitySignature(signed_tree_hash="abc123")
        write_signature(d, sig)

        # Provenance unchanged
        assert (d / "provenance.json").stat().st_mtime == orig_mtime
        prov2 = read_provenance(d)
        assert prov2 is not None
        assert prov2.capability_id == prov.capability_id

    def test_provenance_still_reads_when_signature_present(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        write_provenance(d, capability_id="test-cap")
        sig = CapabilitySignature(signed_tree_hash="abc123")
        write_signature(d, sig)

        prov = read_provenance(d)
        assert prov is not None
        assert prov.capability_id == "test-cap"

    def test_signature_still_reads_when_provenance_present(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        write_provenance(d, capability_id="test-cap")
        sig = CapabilitySignature(signed_tree_hash="abc123")
        write_signature(d, sig)

        sig2 = read_signature(d)
        assert sig2 is not None
        assert sig2.signed_tree_hash == "abc123"

    def test_both_files_coexist(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        write_provenance(d, capability_id="test-cap")
        sig = CapabilitySignature(signed_tree_hash="abc123")
        write_signature(d, sig)

        assert (d / "provenance.json").is_file()
        assert (d / "signature.json").is_file()


# ── CapabilityTrustPolicy invariants hold ────────────────────────────────────


class TestTrustPolicyInvariantsHold:
    """Phase 8B-0 invariants remain true with Phase 8B-1 code present."""

    def test_trusted_signed_requires_verified(self):
        """trusted_signed with any non-verified signature_status produces a warning."""
        policy = CapabilityTrustPolicy()

        # trusted_signed + verified → allowed (info)
        prov_ok = CapabilityProvenance(
            provenance_id="prov_ok",
            capability_id="test",
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        decision = policy.evaluate_provenance(prov_ok)
        assert decision.allowed is True
        assert decision.severity == "info"

        # trusted_signed + not_present → warning
        prov_bad = CapabilityProvenance(
            provenance_id="prov_bad",
            capability_id="test",
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        decision = policy.evaluate_provenance(prov_bad)
        assert decision.allowed is True
        assert decision.severity == "warning"

    def test_present_unverified_does_not_imply_trusted_signed(self):
        """present_unverified can coexist with trust_level=reviewed."""
        prov = CapabilityProvenance(
            provenance_id="prov_test",
            capability_id="test",
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_REVIEWED
        assert prov.trust_level != TRUST_TRUSTED_SIGNED
        assert prov.signature_status == SIGNATURE_PRESENT_UNVERIFIED

    def test_trust_policy_handles_all_signature_statuses(self):
        """CapabilityTrustPolicy.evaluate_provenance handles all signature statuses."""
        policy = CapabilityTrustPolicy()
        from src.capabilities.provenance import PROVENANCE_SIGNATURE_STATUSES
        for sig_status in PROVENANCE_SIGNATURE_STATUSES:
            for trust_level in [TRUST_UNTRUSTED, TRUST_REVIEWED]:
                prov = CapabilityProvenance(
                    provenance_id="prov_test",
                    capability_id="test",
                    trust_level=trust_level,
                    signature_status=sig_status,
                )
                decision = policy.evaluate_provenance(prov)
                assert decision.code is not None


# ── Verifier stub + provenance integration ──────────────────────────────────


class TestVerifierStubProvenanceIntegration:
    """The verifier stub and provenance systems coexist peacefully."""

    def test_verifier_does_not_modify_provenance(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        write_provenance(d, capability_id="test-cap")
        orig_prov = read_provenance(d)

        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
        }))
        verify_signature_stub(d)

        prov = read_provenance(d)
        assert prov is not None
        assert prov.to_dict() == orig_prov.to_dict()

    def test_verifier_result_can_inform_provenance_update(self, tmp_path: Path):
        """Even though the verifier stub doesn't write provenance, its result
        can be used by callers to update provenance fields."""
        d = _make_capability_dir(tmp_path)
        write_provenance(d, capability_id="test-cap")

        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": "0" * 64,  # deliberate mismatch
        }))

        result = verify_signature_stub(d)
        assert result.signature_status == "invalid"

        # A caller could use this to update provenance (not done automatically)
        prov = read_provenance(d)
        assert prov is not None
        # Provenance still has original values — verifier is non-mutating
        assert prov.signature_status == SIGNATURE_NOT_PRESENT


# ── signature.json is local-only ─────────────────────────────────────────────


class TestSignatureIsLocalOnly:
    """signature.json is a local file. No remote fetching."""

    def test_signature_has_no_network_fields(self):
        sig_fields = {f.name for f in CapabilitySignature.__dataclass_fields__.values()}
        network_keywords = {"url", "endpoint", "registry", "remote", "fetch", "download"}
        assert sig_fields.isdisjoint(network_keywords)

    def test_trust_root_has_no_network_fields(self):
        from src.capabilities.signature import CapabilityTrustRoot
        root_fields = {f.name for f in CapabilityTrustRoot.__dataclass_fields__.values()}
        network_keywords = {"url", "endpoint", "registry", "remote", "fetch", "download"}
        assert root_fields.isdisjoint(network_keywords)

    def test_verify_signature_stub_has_no_network_calls(self):
        import inspect
        from src.capabilities import signature as sig_mod
        source = inspect.getsource(sig_mod.verify_signature_stub)
        network_calls = ["requests", "urllib", "urlopen", "http:", "https:",
                         "socket.connect", "ftplib"]
        for call in network_calls:
            assert call not in source.lower(), f"Network call found: {call}"


# ── Phase 8A / 8B-0 tests still pass (smoke tests) ──────────────────────────


class TestPhase8AInvariantsStillHold:
    """Core invariants from Phase 8A provenance still hold."""

    def test_provenance_round_trip(self):
        prov = CapabilityProvenance(
            provenance_id="prov_test",
            capability_id="test-cap",
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        d = prov.to_dict()
        rt = CapabilityProvenance.from_dict(d)
        assert rt.provenance_id == prov.provenance_id
        assert rt.capability_id == prov.capability_id
        assert rt.trust_level == TRUST_REVIEWED
        assert rt.signature_status == SIGNATURE_PRESENT_UNVERIFIED

    def test_provenance_defaults_safe(self):
        prov = CapabilityProvenance(provenance_id="p", capability_id="c")
        assert prov.signature_status == SIGNATURE_NOT_PRESENT
        assert prov.trust_level == TRUST_UNTRUSTED

    def test_tree_hash_deterministic(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        h1 = compute_capability_tree_hash(d)
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2

    def test_volatile_dirs_excluded_from_hash(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        h1 = compute_capability_tree_hash(d)

        evals = d / "evals"
        evals.mkdir()
        (evals / "report.json").write_text('{"score": 0.9}', encoding="utf-8")

        h2 = compute_capability_tree_hash(d)
        assert h1 == h2


class TestPhase8B0InvariantsStillHold:
    """Core invariants from Phase 8B-0 still hold."""

    def test_reviewed_does_not_imply_trusted_signed(self):
        assert TRUST_REVIEWED != TRUST_TRUSTED_SIGNED
        prov = CapabilityProvenance(
            provenance_id="p", capability_id="c",
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        assert prov.trust_level == TRUST_REVIEWED

    def test_trusted_signed_distinct(self):
        from src.capabilities.provenance import PROVENANCE_TRUST_LEVELS
        assert TRUST_TRUSTED_SIGNED in PROVENANCE_TRUST_LEVELS
        assert TRUST_TRUSTED_SIGNED != TRUST_REVIEWED

    def test_verified_distinct_from_present_unverified(self):
        assert SIGNATURE_VERIFIED != SIGNATURE_PRESENT_UNVERIFIED

    def test_trust_policy_analytical_not_gating(self):
        policy = CapabilityTrustPolicy()
        prov = CapabilityProvenance(
            provenance_id="p", capability_id="c",
            trust_level=TRUST_UNTRUSTED,
        )
        decision = policy.evaluate_provenance(prov)
        # Analytical — returns a decision, doesn't throw or block
        assert decision.code is not None
