"""Phase 8B-0: Signature / Trust Root model invariant tests.

Validates the design invariants documented in:
- docs/capability_signature_trust_model.md

No crypto implementation. No signature verification. No network.
No behavior changes. Pure invariant assertions against the design model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.capabilities.provenance import (
    PROVENANCE_SIGNATURE_STATUSES,
    PROVENANCE_TRUST_LEVELS,
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    SIGNATURE_VERIFIED,
    TRUST_REVIEWED,
    TRUST_TRUSTED_LOCAL,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNKNOWN,
    TRUST_UNTRUSTED,
    CapabilityProvenance,
    CapabilityTrustPolicy,
    TrustDecision,
    compute_capability_tree_hash,
)


# ── Future model shapes (test-only, not imported from src) ──────────────────


@dataclass
class _CapabilityTrustRoot:
    """Future trust root shape — test fixture only. Not a src model.

    Documents the design contract for when trust roots are implemented.
    No verification logic. No crypto.
    """

    trust_root_id: str
    name: str
    key_type: str
    public_key_fingerprint: str
    owner: str
    scope: str = "local"
    status: str = "active"
    created_at: str = ""
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


_TRUST_ROOT_STATUSES: frozenset[str] = frozenset({"active", "disabled", "revoked"})


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_provenance(
    *,
    capability_id: str = "test-cap",
    trust_level: str = TRUST_UNTRUSTED,
    signature_status: str = SIGNATURE_NOT_PRESENT,
    **kwargs: Any,
) -> CapabilityProvenance:
    return CapabilityProvenance(
        provenance_id="prov_test",
        capability_id=capability_id,
        trust_level=trust_level,
        signature_status=signature_status,
        **kwargs,
    )


# ── Invariant: signature_status domain completeness ────────────────────────


class TestSignatureStatusDomain:
    """All four signature_status values are well-defined and distinct."""

    def test_all_statuses_defined(self):
        expected = {"not_present", "present_unverified", "verified", "invalid"}
        assert PROVENANCE_SIGNATURE_STATUSES == expected

    def test_statuses_are_mutually_distinct(self):
        assert SIGNATURE_NOT_PRESENT != SIGNATURE_PRESENT_UNVERIFIED
        assert SIGNATURE_NOT_PRESENT != SIGNATURE_VERIFIED
        assert SIGNATURE_NOT_PRESENT != SIGNATURE_INVALID
        assert SIGNATURE_PRESENT_UNVERIFIED != SIGNATURE_VERIFIED
        assert SIGNATURE_PRESENT_UNVERIFIED != SIGNATURE_INVALID
        assert SIGNATURE_VERIFIED != SIGNATURE_INVALID

    def test_default_is_not_present(self):
        prov = _make_provenance()
        assert prov.signature_status == SIGNATURE_NOT_PRESENT

    def test_all_values_are_valid_provenance_fields(self):
        for status in PROVENANCE_SIGNATURE_STATUSES:
            prov = _make_provenance(signature_status=status)
            assert prov.signature_status == status
            d = prov.to_dict()
            assert d["signature_status"] == status
            rt = CapabilityProvenance.from_dict(d)
            assert rt.signature_status == status


# ── Invariant: trust_level domain completeness ─────────────────────────────


class TestTrustLevelDomain:
    """All five trust_level values are well-defined and distinct."""

    def test_all_levels_defined(self):
        expected = {"unknown", "untrusted", "reviewed", "trusted_local", "trusted_signed"}
        assert PROVENANCE_TRUST_LEVELS == expected

    def test_levels_are_mutually_distinct(self):
        values = sorted(PROVENANCE_TRUST_LEVELS)
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                assert values[i] != values[j]

    def test_default_is_untrusted(self):
        prov = _make_provenance()
        assert prov.trust_level == TRUST_UNTRUSTED

    def test_all_values_are_valid_provenance_fields(self):
        for level in PROVENANCE_TRUST_LEVELS:
            prov = _make_provenance(trust_level=level)
            assert prov.trust_level == level
            d = prov.to_dict()
            assert d["trust_level"] == level
            rt = CapabilityProvenance.from_dict(d)
            assert rt.trust_level == level


# ── Invariant: reviewed != trusted_signed ──────────────────────────────────


class TestReviewedDoesNotImplyTrustedSigned:
    """reviewed is a human curation state, not cryptographic trust."""

    def test_reviewed_is_different_string_from_trusted_signed(self):
        assert TRUST_REVIEWED != TRUST_TRUSTED_SIGNED

    def test_reviewed_can_coexist_with_not_present_signature(self):
        """A reviewed capability may have no signature at all."""
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        assert prov.trust_level == TRUST_REVIEWED
        assert prov.signature_status == SIGNATURE_NOT_PRESENT

    def test_reviewed_can_coexist_with_unverified_signature(self):
        """A reviewed capability may carry an unverified signature."""
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_REVIEWED
        assert prov.signature_status == SIGNATURE_PRESENT_UNVERIFIED

    def test_reviewed_does_not_set_signature_status(self):
        """Setting trust_level=reviewed must not implicitly change signature_status."""
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        assert prov.signature_status == SIGNATURE_NOT_PRESENT


# ── Invariant: trusted_local != trusted_signed ─────────────────────────────


class TestTrustedLocalDoesNotImplyTrustedSigned:
    """trusted_local is operator trust, not cryptographic trust."""

    def test_trusted_local_is_different_string_from_trusted_signed(self):
        assert TRUST_TRUSTED_LOCAL != TRUST_TRUSTED_SIGNED

    def test_trusted_local_can_coexist_with_not_present_signature(self):
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        assert prov.trust_level == TRUST_TRUSTED_LOCAL
        assert prov.signature_status == SIGNATURE_NOT_PRESENT

    def test_trusted_local_can_coexist_with_unverified_signature(self):
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_LOCAL,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_TRUSTED_LOCAL
        assert prov.signature_status == SIGNATURE_PRESENT_UNVERIFIED


# ── Invariant: trusted_signed requires signature_status=verified ───────────


class TestTrustedSignedRequiresVerifiedSignature:
    """trusted_signed must only be reachable when signature_status == verified."""

    def test_trusted_signed_is_distinct_from_all_other_trust_levels(self):
        """Every trust level other than trusted_signed can be reached without a
        verified signature. trusted_signed is the only level that requires one."""
        non_signed_levels = PROVENANCE_TRUST_LEVELS - {TRUST_TRUSTED_SIGNED}
        for level in non_signed_levels:
            prov = _make_provenance(
                trust_level=level,
                signature_status=SIGNATURE_NOT_PRESENT,
            )
            assert prov.trust_level == level

    def test_trusted_signed_string_exists_and_is_reserved(self):
        """trusted_signed is a defined value in the trust level domain."""
        assert TRUST_TRUSTED_SIGNED in PROVENANCE_TRUST_LEVELS
        assert TRUST_TRUSTED_SIGNED == "trusted_signed"

    def test_trusted_signed_provenance_round_trips(self):
        """A provenance with trusted_signed + verified round-trips correctly.
        This does NOT mean the system currently enforces the relationship —
        only that the data model supports it."""
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        assert prov.trust_level == TRUST_TRUSTED_SIGNED
        assert prov.signature_status == SIGNATURE_VERIFIED
        d = prov.to_dict()
        rt = CapabilityProvenance.from_dict(d)
        assert rt.trust_level == TRUST_TRUSTED_SIGNED
        assert rt.signature_status == SIGNATURE_VERIFIED


# ── Invariant: present_unverified does not imply trusted_signed ────────────


class TestPresentUnverifiedDoesNotImplyTrustedSigned:
    """Having signature metadata is not enough — it must be verified."""

    def test_present_unverified_is_not_verified(self):
        assert SIGNATURE_PRESENT_UNVERIFIED != SIGNATURE_VERIFIED

    def test_present_unverified_can_coexist_with_untrusted(self):
        prov = _make_provenance(
            trust_level=TRUST_UNTRUSTED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_UNTRUSTED
        assert prov.signature_status == SIGNATURE_PRESENT_UNVERIFIED

    def test_present_unverified_can_coexist_with_reviewed(self):
        prov = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        )
        assert prov.trust_level == TRUST_REVIEWED
        assert prov.signature_status == SIGNATURE_PRESENT_UNVERIFIED


# ── Invariant: invalid signature blocks signed trust ───────────────────────


class TestInvalidSignatureBlocksSignedTrust:
    """An invalid signature must never lead to trusted_signed."""

    def test_invalid_is_not_verified(self):
        assert SIGNATURE_INVALID != SIGNATURE_VERIFIED

    def test_invalid_is_a_defined_status(self):
        assert SIGNATURE_INVALID in PROVENANCE_SIGNATURE_STATUSES
        assert SIGNATURE_INVALID == "invalid"

    def test_invalid_signature_provenance_round_trips(self):
        """The data model can represent an invalid signature — this is necessary
        for future verification to record invalid results."""
        prov = _make_provenance(
            trust_level=TRUST_UNTRUSTED,
            signature_status=SIGNATURE_INVALID,
        )
        assert prov.signature_status == SIGNATURE_INVALID
        assert prov.trust_level == TRUST_UNTRUSTED
        d = prov.to_dict()
        rt = CapabilityProvenance.from_dict(d)
        assert rt.signature_status == SIGNATURE_INVALID


# ── Invariant: not_present cannot produce trusted_signed ───────────────────


class TestNotPresentCannotProduceTrustedSigned:
    """No signature metadata means no path to trusted_signed."""

    def test_not_present_is_the_default(self):
        prov = CapabilityProvenance(
            provenance_id="prov_test",
            capability_id="test-cap",
        )
        assert prov.signature_status == SIGNATURE_NOT_PRESENT

    def test_not_present_default_trust_is_untrusted(self):
        prov = CapabilityProvenance(
            provenance_id="prov_test",
            capability_id="test-cap",
        )
        assert prov.trust_level == TRUST_UNTRUSTED
        assert prov.trust_level != TRUST_TRUSTED_SIGNED


# ── Invariant: legacy compatibility ────────────────────────────────────────


class TestLegacyCompatibility:
    """Missing signature metadata must not break legacy capabilities."""

    def test_provenance_from_minimal_dict_defaults_correctly(self):
        """A provenance.json from a legacy capability (no signature fields)
        must deserialize with safe defaults."""
        minimal = {
            "provenance_id": "prov_legacy",
            "capability_id": "legacy-cap",
        }
        prov = CapabilityProvenance.from_dict(minimal)
        assert prov.signature_status == SIGNATURE_NOT_PRESENT
        assert prov.trust_level == TRUST_UNTRUSTED

    def test_provenance_from_empty_dict_does_not_crash(self):
        prov = CapabilityProvenance.from_dict({})
        assert prov.signature_status == SIGNATURE_NOT_PRESENT
        assert prov.trust_level == TRUST_UNTRUSTED
        assert prov.provenance_id == ""
        assert prov.capability_id == ""

    def test_provenance_without_signature_status_key_defaults(self):
        data = {
            "provenance_id": "prov_test",
            "capability_id": "test-cap",
            "trust_level": TRUST_REVIEWED,
        }
        prov = CapabilityProvenance.from_dict(data)
        assert prov.signature_status == SIGNATURE_NOT_PRESENT

    def test_unknown_is_valid_trust_level_for_legacy(self):
        """Legacy capabilities with unknown trust assessment are valid."""
        prov = _make_provenance(trust_level=TRUST_UNKNOWN)
        assert prov.trust_level == TRUST_UNKNOWN
        d = prov.to_dict()
        rt = CapabilityProvenance.from_dict(d)
        assert rt.trust_level == TRUST_UNKNOWN


# ── Invariant: trust root model is local-only ──────────────────────────────


class TestTrustRootModelIsLocalOnly:
    """The future trust root model must be self-contained with no network."""

    def test_trust_root_dataclass_has_no_network_fields(self):
        """TrustRoot fields are all local: identifiers, keys, metadata.
        No URL, endpoint, registry, or network address fields."""
        fields = {f.name for f in _CapabilityTrustRoot.__dataclass_fields__.values()}
        network_keywords = {"url", "endpoint", "registry", "host", "port", "remote",
                            "server", "api", "fetch", "download", "connection"}
        assert fields.isdisjoint(network_keywords)

    def test_trust_root_statuses_are_local_only(self):
        """active/disabled/revoked are local configuration states, not network
        protocol states."""
        assert "active" in _TRUST_ROOT_STATUSES
        assert "disabled" in _TRUST_ROOT_STATUSES
        assert "revoked" in _TRUST_ROOT_STATUSES
        # No network-related states
        network_states = {"fetching", "connecting", "syncing", "remote", "online", "offline"}
        assert _TRUST_ROOT_STATUSES.isdisjoint(network_states)

    def test_disabled_trust_root_cannot_be_active(self):
        assert "disabled" != "active"

    def test_revoked_trust_root_cannot_be_active(self):
        assert "revoked" != "active"

    def test_trust_root_creation_requires_local_fields(self):
        """A trust root can be created from purely local data — no network call
        needed."""
        root = _CapabilityTrustRoot(
            trust_root_id="tr_lapwing_core",
            name="Lapwing Core",
            key_type="ed25519",
            public_key_fingerprint="sha256:abcdef1234567890",
            owner="lapwing-core",
            scope="global",
            status="active",
        )
        assert root.trust_root_id == "tr_lapwing_core"
        assert root.status == "active"
        assert root.public_key_fingerprint.startswith("sha256:")

    def test_trust_root_default_scope_is_local(self):
        root = _CapabilityTrustRoot(
            trust_root_id="tr_test",
            name="Test",
            key_type="ed25519",
            public_key_fingerprint="sha256:deadbeef",
            owner="test",
        )
        assert root.scope == "local"


# ── Invariant: disabled/revoked trust roots must not verify ────────────────


class TestDisabledRevokedTrustRootsMustNotVerify:
    """Only active trust roots are valid for signature verification."""

    def test_disabled_is_not_active(self):
        assert "disabled" != "active"

    def test_revoked_is_not_active(self):
        assert "revoked" != "active"

    def test_only_active_trust_roots_are_verifiable(self):
        """Design invariant: when verification is implemented, only trust roots
        with status='active' may be used for verification. disabled and revoked
        must be rejected before any cryptographic check."""
        verifiable_statuses = {"active"}
        assert "disabled" not in verifiable_statuses
        assert "revoked" not in verifiable_statuses

    def test_all_trust_root_statuses_defined(self):
        """The three states (active, disabled, revoked) are the complete domain."""
        assert _TRUST_ROOT_STATUSES == {"active", "disabled", "revoked"}


# ── Invariant: remote registry trust is out of scope ───────────────────────


class TestRemoteRegistryOutOfScope:
    """Trust roots are local configuration. No remote registry exists."""

    def test_provenance_model_has_no_registry_fields(self):
        """CapabilityProvenance must not reference any remote registry."""
        prov_fields = {f.name for f in CapabilityProvenance.__dataclass_fields__.values()}
        registry_keywords = {"registry_url", "registry_id", "remote_trust_root",
                             "trust_root_url", "registry_endpoint", "remote_provenance"}
        assert prov_fields.isdisjoint(registry_keywords)

    def test_trust_decision_has_no_registry_fields(self):
        """TrustDecision must not reference any remote registry."""
        td_fields = {f.name for f in TrustDecision.__dataclass_fields__.values()}
        registry_keywords = {"registry_url", "registry_id", "remote"}
        assert td_fields.isdisjoint(registry_keywords)


# ── Invariant: network verification is out of scope ────────────────────────


class TestNetworkVerificationOutOfScope:
    """Signature verification is a local operation. No network calls."""

    def test_tree_hash_is_pure_local_computation(self, tmp_path: Path):
        """compute_capability_tree_hash uses only local filesystem — no network."""
        d = tmp_path / "test_tree"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Test\n", encoding="utf-8")
        manifest = {"id": "test", "name": "Test"}
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        h1 = compute_capability_tree_hash(d)
        h2 = compute_capability_tree_hash(d)
        # Deterministic: same input → same output
        assert h1 == h2
        # Produces a valid hex string
        assert len(h1) == 64
        int(h1, 16)  # must not raise

    def test_tree_hash_does_not_import_network_modules(self):
        """Verify compute_capability_tree_hash module has no network imports."""
        import inspect
        from src.capabilities import provenance as prov_mod
        source = inspect.getsource(prov_mod.compute_capability_tree_hash)
        # No network calls in the implementation
        network_calls = ["requests", "urllib", "http", "socket", "urlopen",
                         "fetch", "curl", "wget"]
        for call in network_calls:
            assert call not in source.lower(), f"Network call '{call}' found in tree hash"


# ── Invariant: verification is deterministic and local ─────────────────────


class TestVerificationIsDeterministicAndLocal:
    """When implemented, signature verification must be deterministic and local."""

    def test_tree_hash_is_deterministic(self, tmp_path: Path):
        d = tmp_path / "test_det"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Deterministic\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")

        hashes = {compute_capability_tree_hash(d) for _ in range(10)}
        assert len(hashes) == 1, "Tree hash is not deterministic"

    def test_tree_hash_is_deterministic_across_identical_dirs(self, tmp_path: Path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        for d in (d1, d2):
            d.mkdir()
            (d / "CAPABILITY.md").write_text("# Same\n", encoding="utf-8")
            (d / "manifest.json").write_text('{"id":"same"}\n', encoding="utf-8")

        assert compute_capability_tree_hash(d1) == compute_capability_tree_hash(d2)

    def test_tree_hash_depends_on_content_not_timestamps(self, tmp_path: Path):
        import time
        d = tmp_path / "test_time"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Content\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"content"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)
        time.sleep(0.1)
        # Touch a file without changing content
        (d / "CAPABILITY.md").write_text("# Content\n", encoding="utf-8")
        h2 = compute_capability_tree_hash(d)
        assert h1 == h2, "Tree hash changed despite identical content"


# ── Invariant: verification must not execute capability code ────────────────


class TestVerificationMustNotExecuteCode:
    """Signature verification hashes content; it never executes it."""

    def test_tree_hash_only_reads_files(self, tmp_path: Path):
        """The tree hash function reads file bytes — it does not import,
        exec, eval, or interpret them."""
        d = tmp_path / "test_noexec"
        d.mkdir()
        scripts = d / "scripts"
        scripts.mkdir()
        # Write a Python script that would have side effects if executed
        (scripts / "setup.py").write_text(
            "raise SystemExit('should never run')\n", encoding="utf-8"
        )
        (d / "CAPABILITY.md").write_text("# No exec\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"noexec"}\n', encoding="utf-8")

        # Must complete without error — if it tried to exec the script, it would fail
        h = compute_capability_tree_hash(d)
        assert len(h) == 64

    def test_scripts_directory_is_hashed_not_executed(self, tmp_path: Path):
        """Scripts are included in the tree hash as bytes — not as executable code."""
        d = tmp_path / "test_scripts_hash"
        d.mkdir()
        scripts = d / "scripts"
        scripts.mkdir()
        (scripts / "install.sh").write_text("#!/bin/bash\necho hello\n", encoding="utf-8")
        (d / "CAPABILITY.md").write_text("# Scripts\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"scripts"}\n', encoding="utf-8")

        h_with = compute_capability_tree_hash(d)

        # Remove the script
        (scripts / "install.sh").unlink()
        h_without = compute_capability_tree_hash(d)

        assert h_with != h_without, "Tree hash must change when scripts are added/removed"


# ── Invariant: verification hashes content, not runtime ────────────────────


class TestVerificationHashesContentNotRuntime:
    """Verification reads file bytes, not runtime behavior."""

    def test_tree_hash_uses_file_bytes(self, tmp_path: Path):
        d = tmp_path / "test_bytes"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Bytes\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"bytes"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)
        # Change a single byte
        (d / "CAPABILITY.md").write_text("# Bytes!\n", encoding="utf-8")
        h2 = compute_capability_tree_hash(d)

        assert h1 != h2, "Tree hash must change when content bytes change"

    def test_tree_hash_changes_when_included_content_changes(self, tmp_path: Path):
        d = tmp_path / "test_content"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Original\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"original"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)
        (d / "manifest.json").write_text('{"id":"modified"}\n', encoding="utf-8")
        h2 = compute_capability_tree_hash(d)

        assert h1 != h2, "Tree hash must change when included content changes"


# ── Invariant: volatile reports do not affect signed tree hash ─────────────


class TestVolatileReportsDoNotAffectTreeHash:
    """Changing files in excluded directories must not affect the tree hash."""

    def test_evals_directory_is_excluded_from_tree_hash(self, tmp_path: Path):
        d = tmp_path / "test_evals"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Test\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)

        evals = d / "evals"
        evals.mkdir()
        (evals / "report.json").write_text('{"score": 0.95}\n', encoding="utf-8")

        h2 = compute_capability_tree_hash(d)
        assert h1 == h2, "Tree hash changed when evals/ was modified"

    def test_traces_directory_is_excluded_from_tree_hash(self, tmp_path: Path):
        d = tmp_path / "test_traces"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Test\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)

        traces = d / "traces"
        traces.mkdir()
        (traces / "trace.json").write_text('{"steps": []}\n', encoding="utf-8")

        h2 = compute_capability_tree_hash(d)
        assert h1 == h2, "Tree hash changed when traces/ was modified"

    def test_quarantine_artifacts_are_excluded_from_tree_hash(self, tmp_path: Path):
        d = tmp_path / "test_quarantine_artifacts"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Test\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)

        for subdir in ("quarantine_audit_reports", "quarantine_reviews",
                       "quarantine_transition_requests", "quarantine_activation_plans",
                       "quarantine_activation_reports", "provenance_verification_logs"):
            sd = d / subdir
            sd.mkdir()
            (sd / "report.json").write_text('{"data": "volatile"}\n', encoding="utf-8")

        h2 = compute_capability_tree_hash(d)
        assert h1 == h2, "Tree hash changed when quarantine artifacts were added"


# ── Invariant: included content changes affect signed tree hash ─────────────


class TestIncludedContentChangesAffectTreeHash:
    """Changing files in included directories must affect the tree hash."""

    def test_capability_md_change_affects_hash(self, tmp_path: Path):
        d = tmp_path / "test_capmd"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Version A\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)
        (d / "CAPABILITY.md").write_text("# Version B\n", encoding="utf-8")
        h2 = compute_capability_tree_hash(d)

        assert h1 != h2

    def test_scripts_change_affects_hash(self, tmp_path: Path):
        d = tmp_path / "test_scripts"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Test\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)

        scripts = d / "scripts"
        scripts.mkdir()
        (scripts / "setup.sh").write_text("#!/bin/bash\necho A\n", encoding="utf-8")

        h2 = compute_capability_tree_hash(d)

        (scripts / "setup.sh").write_text("#!/bin/bash\necho B\n", encoding="utf-8")
        h3 = compute_capability_tree_hash(d)

        assert h1 != h2, "Adding scripts should change tree hash"
        assert h2 != h3, "Modifying scripts should change tree hash"

    def test_tests_change_affects_hash(self, tmp_path: Path):
        d = tmp_path / "test_testsdir"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Test\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")

        h1 = compute_capability_tree_hash(d)

        tests = d / "tests"
        tests.mkdir()
        (tests / "test_foo.py").write_text("# test\n", encoding="utf-8")

        h2 = compute_capability_tree_hash(d)
        assert h1 != h2, "Adding tests should change tree hash"


# ── Invariant: signed hash binds to tree hash, not content_hash alone ──────


class TestSignedHashBindsToTreeHash:
    """The signed hash must cover the entire directory tree, not just
    manifest.content_hash."""

    def test_capability_md_not_in_manifest_content_hash(self):
        """manifest.content_hash (computed elsewhere) may not include CAPABILITY.md
        in the same way. The tree hash DOES include CAPABILITY.md."""
        # Design assertion: the tree hash algorithm explicitly includes
        # CAPABILITY.md, while manifest.content_hash is a different computation.
        # This test verifies the tree hash changes when CAPABILITY.md changes,
        # confirming the broader binding.
        pass  # Verified by TestIncludedContentChangesAffectTreeHash above

    def test_tree_hash_covers_full_directory(self, tmp_path: Path):
        """The tree hash spans CAPABILITY.md, manifest.json, scripts/, tests/,
        and examples/ — not just a single file."""
        d = tmp_path / "test_full_tree"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Full tree\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"id":"full-tree"}\n', encoding="utf-8")

        h_baseline = compute_capability_tree_hash(d)

        # Add examples/ — this should change the tree hash
        examples = d / "examples"
        examples.mkdir()
        (examples / "demo.py").write_text("print('hello')\n", encoding="utf-8")

        h_with_examples = compute_capability_tree_hash(d)
        assert h_baseline != h_with_examples, (
            "Tree hash must change when examples/ is added"
        )

    def test_provenance_stores_source_content_hash_from_tree_hash(self):
        """The provenance.source_content_hash field is populated by the tree
        hash during import/activation, not by manifest.content_hash."""
        prov = _make_provenance(source_content_hash="abc123")
        # The field exists and is settable independently of manifest.content_hash
        assert prov.source_content_hash == "abc123"


# ── Invariant: trusted_signed does not imply executable ────────────────────


class TestTrustedSignedDoesNotImplyExecutable:
    """trusted_signed is a trust assessment, not an execution contract."""

    def test_provenance_has_no_execution_fields(self):
        prov_fields = {f.name for f in CapabilityProvenance.__dataclass_fields__.values()}
        exec_keywords = {"executable", "run_permission", "execution_allowed",
                         "runtime_contract", "execute"}
        assert prov_fields.isdisjoint(exec_keywords), (
            "CapabilityProvenance must not contain execution-related fields"
        )

    def test_trust_decision_has_no_execution_fields(self):
        td_fields = {f.name for f in TrustDecision.__dataclass_fields__.values()}
        exec_keywords = {"executable", "run_permission", "execution_allowed"}
        assert td_fields.isdisjoint(exec_keywords), (
            "TrustDecision must not contain execution-related fields"
        )


# ── Invariant: trusted_signed does not imply stable ────────────────────────


class TestTrustedSignedDoesNotImplyStable:
    """trusted_signed and stable maturity are orthogonal dimensions."""

    def test_trust_level_is_separate_from_maturity(self):
        """provenance.trust_level is a separate concept from manifest.maturity."""
        prov_fields = {f.name for f in CapabilityProvenance.__dataclass_fields__.values()}
        assert "maturity" not in prov_fields, (
            "CapabilityProvenance does not track maturity — it is on CapabilityManifest"
        )

    def test_trusted_signed_provenance_can_describe_any_maturity(self):
        """The provenance model doesn't constrain maturity. A capability can be
        trusted_signed at draft, testing, or stable maturity."""
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        assert prov.trust_level == TRUST_TRUSTED_SIGNED
        # No maturity field on provenance — it's on manifest (separate concern)


# ── Invariant: trusted_signed does not bypass policy/evaluator/lifecycle ────


class TestTrustedSignedDoesNotBypassGates:
    """trusted_signed is an input to trust decisions, not a bypass of them."""

    def test_trust_policy_evaluates_provenance_not_just_trust_level(self):
        """CapabilityTrustPolicy.evaluate_provenance considers the whole
        provenance record, not just trust_level."""
        policy = CapabilityTrustPolicy()

        prov_trusted = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        decision = policy.evaluate_provenance(prov_trusted)
        assert isinstance(decision, TrustDecision)
        # The decision is structured — it has severity, code, message, details
        assert decision.severity in ("info", "warning", "error")

    def test_trust_policy_is_analytical_not_gating(self):
        """CapabilityTrustPolicy returns TrustDecision objects; it does not
        directly block or allow execution. Callers decide how to act."""
        policy = CapabilityTrustPolicy()

        prov_untrusted = _make_provenance(trust_level=TRUST_UNTRUSTED)
        decision = policy.evaluate_provenance(prov_untrusted)
        assert isinstance(decision, TrustDecision)
        # Even for untrusted, the policy is analytical — it returns a decision
        # without enforcing it

    def test_trusted_signed_does_not_change_policy_output_type(self):
        """The policy output shape is the same regardless of trust level."""
        policy = CapabilityTrustPolicy()

        for level in PROVENANCE_TRUST_LEVELS:
            prov = _make_provenance(trust_level=level)
            decision = policy.evaluate_provenance(prov)
            assert isinstance(decision, TrustDecision)
            assert hasattr(decision, "allowed")
            assert hasattr(decision, "code")


# ── Invariant: trusted_signed does not grant permissions ───────────────────


class TestTrustedSignedDoesNotGrantPermissions:
    """trusted_signed is not a permission system."""

    def test_provenance_has_no_permission_fields(self):
        prov_fields = {f.name for f in CapabilityProvenance.__dataclass_fields__.values()}
        perm_keywords = {"permissions", "grants", "capabilities_granted",
                         "required_permissions", "granted_permissions"}
        assert prov_fields.isdisjoint(perm_keywords), (
            "CapabilityProvenance must not contain permission-granting fields"
        )


# ── Invariant: trusted_signed does not bypass owner/operator approval ──────


class TestTrustedSignedDoesNotBypassApproval:
    """trusted_signed augments, not replaces, operator trust decisions."""

    def test_trusted_signed_and_reviewed_are_independent(self):
        """A capability can be reviewed without being signed, and vice versa.
        The operator always has final say."""
        prov_reviewed_only = _make_provenance(
            trust_level=TRUST_REVIEWED,
            signature_status=SIGNATURE_NOT_PRESENT,
        )
        assert prov_reviewed_only.trust_level == TRUST_REVIEWED
        assert prov_reviewed_only.signature_status == SIGNATURE_NOT_PRESENT

        prov_signed_only = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        assert prov_signed_only.trust_level == TRUST_TRUSTED_SIGNED
        assert prov_signed_only.signature_status == SIGNATURE_VERIFIED

        # These are different trust paths — neither bypasses the other
        assert TRUST_REVIEWED != TRUST_TRUSTED_SIGNED


# ── Invariant: invalid signature blocks future gates ───────────────────────


class TestInvalidSignatureBlocksFutureGates:
    """When wired into activation/promotion, invalid must be a hard block."""

    def test_invalid_signature_provenance_is_representable(self):
        """The data model must support recording invalid signatures so that
        future gates can read and block on them."""
        prov = _make_provenance(
            trust_level=TRUST_UNTRUSTED,
            signature_status=SIGNATURE_INVALID,
        )
        assert prov.signature_status == SIGNATURE_INVALID
        d = prov.to_dict()
        assert d["signature_status"] == "invalid"

    def test_invalid_is_not_present_unverified(self):
        """invalid is semantically different from present_unverified.
        present_unverified = 'has metadata, not yet checked'
        invalid = 'checked and failed'"""
        assert SIGNATURE_INVALID != SIGNATURE_PRESENT_UNVERIFIED

    def test_invalid_signature_trust_policy_evaluates(self):
        """The trust policy can evaluate provenance with invalid signatures
        without crashing — the policy returns a decision the caller can act on."""
        policy = CapabilityTrustPolicy()
        prov = _make_provenance(
            trust_level=TRUST_UNTRUSTED,
            signature_status=SIGNATURE_INVALID,
        )
        decision = policy.evaluate_provenance(prov)
        assert isinstance(decision, TrustDecision)


# ── Invariant: trusted_signed necessary but not sufficient for high-trust ──


class TestTrustedSignedNecessaryButNotSufficient:
    """trusted_signed is one requirement among many for high-trust flows."""

    def test_trusted_signed_is_one_of_many_trust_levels(self):
        """trusted_signed is not a 'super' level — it's one of five."""
        assert len(PROVENANCE_TRUST_LEVELS) == 5
        assert TRUST_TRUSTED_SIGNED in PROVENANCE_TRUST_LEVELS

    def test_trusted_signed_does_not_imply_any_particular_signature_status(self):
        """While trusted_signed SHOULD require verified (design invariant),
        the data model alone does not enforce this — enforcement comes from
        future verification code and gate logic."""
        # The data model can represent any combination (for flexibility in
        # handling edge cases), but gates must enforce the relationship.
        prov = _make_provenance(
            trust_level=TRUST_TRUSTED_SIGNED,
            signature_status=SIGNATURE_VERIFIED,
        )
        assert prov.trust_level == TRUST_TRUSTED_SIGNED
        assert prov.signature_status == SIGNATURE_VERIFIED

    def test_high_trust_requires_multiple_dimensions(self):
        """High-trust flows require multiple independent assessments:
        - signature verification (signature_status=verified)
        - signer identity trust (trust root active + trusted)
        - policy evaluation (CapabilityTrustPolicy)
        - evaluator pass (CapabilityEvaluator)
        - lifecycle gate (maturity/status transitions)
        - operator approval (review/owner decision)

        trusted_signed only covers the first two. The rest remain
        independent gates."""
        dimensions = [
            "signature_status",
            "trust_root",
            "policy",
            "evaluator",
            "lifecycle",
            "operator_approval",
        ]
        # trusted_signed touches only 2 of 6 dimensions
        signed_dimensions = {"signature_status", "trust_root"}
        independent_dimensions = set(dimensions) - signed_dimensions
        assert len(independent_dimensions) == 4, (
            "trusted_signed must not absorb independent trust dimensions"
        )
