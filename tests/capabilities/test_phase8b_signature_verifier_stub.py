"""Phase 8B-1: Verifier stub tests.

Tests for verify_signature_stub covering all decision branches.
No real crypto. No network. No script execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.capabilities.signature import (
    CapabilitySignature,
    CapabilityTrustRoot,
    parse_signature_dict,
    parse_trust_root_dict,
    verify_signature_stub,
    write_signature,
)
from src.capabilities.provenance import (
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    SIGNATURE_VERIFIED,
    TRUST_TRUSTED_SIGNED,
    TRUST_UNTRUSTED,
    compute_capability_tree_hash,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_capability_dir(tmp_path: Path, *, cap_id: str = "test-cap") -> Path:
    """Create a minimal capability directory with manifest.json and CAPABILITY.md."""
    d = tmp_path / cap_id
    d.mkdir()
    (d / "CAPABILITY.md").write_text(f"# {cap_id}\n", encoding="utf-8")
    manifest = {"id": cap_id, "name": cap_id.capitalize(), "version": "1.0.0"}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _make_trust_roots(**overrides) -> dict[str, CapabilityTrustRoot]:
    """Create a dict of CapabilityTrustRoot keyed by trust_root_id."""
    root = parse_trust_root_dict({
        "trust_root_id": "tr_test",
        "name": "Test Root",
        "key_type": "ed25519",
        "public_key_fingerprint": "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "status": overrides.pop("status", "active"),
        **overrides,
    })
    return {root.trust_root_id: root}


# ── No signature → not_present ──────────────────────────────────────────────


class TestNoSignatureNotPresent:
    """When signature.json is missing, result is not_present, allowed."""

    def test_no_signature_file_returns_not_present(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_NOT_PRESENT
        assert result.allowed is True
        assert result.code == "no_signature"

    def test_no_signature_trust_level_is_untrusted(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        result = verify_signature_stub(d)
        assert result.trust_level_recommendation == TRUST_UNTRUSTED

    def test_no_signature_capability_id_from_manifest(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path, cap_id="my-capability")
        result = verify_signature_stub(d)
        assert result.capability_id == "my-capability"


# ── Malformed signature → invalid ───────────────────────────────────────────


class TestMalformedSignatureInvalid:
    """Unparseable signature.json produces invalid, not allowed."""

    def test_invalid_json_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text("this is not json", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.allowed is False
        assert result.code == "malformed_signature"

    def test_non_dict_json_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text("[1, 2, 3]", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.allowed is False

    def test_empty_file_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text("", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.allowed is False


# ── Missing signed_tree_hash → invalid ──────────────────────────────────────


class TestMissingTreeHashInvalid:
    """signature.json without signed_tree_hash produces invalid."""

    def test_empty_dict_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text("{}", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.allowed is False
        assert result.code == "missing_tree_hash"

    def test_only_algorithm_no_tree_hash_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text('{"algorithm": "ed25519"}', encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "missing_tree_hash"


# ── Tree hash mismatch → invalid ────────────────────────────────────────────


class TestTreeHashMismatchInvalid:
    """When signed_tree_hash != computed tree hash, result is invalid."""

    def test_wrong_hash_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        wrong_hash = "0000000000000000000000000000000000000000000000000000000000000000"
        (d / "signature.json").write_text(
            json.dumps({"signed_tree_hash": wrong_hash}), encoding="utf-8"
        )
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.allowed is False
        assert result.code == "tree_hash_mismatch"

    def test_tree_hash_mismatch_includes_details(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        wrong_hash = "0000000000000000000000000000000000000000000000000000000000000000"
        (d / "signature.json").write_text(
            json.dumps({"signed_tree_hash": wrong_hash}), encoding="utf-8"
        )
        result = verify_signature_stub(d)
        assert result.details["signed_tree_hash"] == wrong_hash
        assert "computed_tree_hash" in result.details

    def test_content_change_causes_mismatch(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(
            json.dumps({"signed_tree_hash": current_hash}), encoding="utf-8"
        )

        # Before content change: hash matches
        result = verify_signature_stub(d)
        assert result.signature_status != SIGNATURE_INVALID

        # After content change: mismatch
        (d / "CAPABILITY.md").write_text("# Modified\n", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "tree_hash_mismatch"


# ── Unknown trust_root_id → present_unverified ──────────────────────────────


class TestUnknownTrustRootPresentUnverified:
    """When trust_root_id is not found, result is present_unverified."""

    def test_unknown_trust_root_returns_present_unverified(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_nonexistent",
        }))
        result = verify_signature_stub(d, trust_roots=_make_trust_roots())
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.allowed is True
        assert result.code == "unknown_trust_root"

    def test_unknown_trust_root_includes_details(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_missing",
        }))
        result = verify_signature_stub(d, trust_roots=_make_trust_roots())
        assert result.details["trust_root_id"] == "tr_missing"


# ── Disabled trust root → invalid ───────────────────────────────────────────


class TestDisabledTrustRootInvalid:
    """When the trust root is disabled, result is invalid."""

    def test_disabled_trust_root_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_disabled",
        }))
        roots = {
            "tr_disabled": parse_trust_root_dict({
                "trust_root_id": "tr_disabled",
                "name": "Disabled Root",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:bbb",
                "status": "disabled",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.allowed is False
        assert result.code == "trust_root_disabled"

    def test_disabled_trust_root_includes_details(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_disabled",
        }))
        roots = {
            "tr_disabled": parse_trust_root_dict({
                "trust_root_id": "tr_disabled",
                "name": "Disabled Root",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:bbb",
                "status": "disabled",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.details["trust_root_status"] == "disabled"


# ── Revoked trust root → invalid ────────────────────────────────────────────


class TestRevokedTrustRootInvalid:
    """When the trust root is revoked, result is invalid."""

    def test_revoked_trust_root_returns_invalid(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_revoked",
        }))
        roots = {
            "tr_revoked": parse_trust_root_dict({
                "trust_root_id": "tr_revoked",
                "name": "Revoked Root",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:ccc",
                "status": "revoked",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.allowed is False
        assert result.code == "trust_root_revoked"


# ── Active trust root + matching hash → present_unverified ──────────────────


class TestActiveTrustRootHashMatch:
    """Active trust root with matching tree hash → present_unverified (NOT verified)."""

    def test_active_trust_root_returns_present_unverified(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_active",
        }))
        roots = {
            "tr_active": parse_trust_root_dict({
                "trust_root_id": "tr_active",
                "name": "Active Root",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:aaa",
                "status": "active",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.allowed is True
        assert result.code == "hash_consistent_unverified"

    def test_active_trust_root_not_verified(self, tmp_path: Path):
        """The stub must NEVER return verified, even with active trust root + match."""
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_active",
        }))
        roots = {
            "tr_active": parse_trust_root_dict({
                "trust_root_id": "tr_active",
                "name": "Active Root",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:aaa",
                "status": "active",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.signature_status != SIGNATURE_VERIFIED

    def test_no_trust_root_id_hash_match_returns_present_unverified(self, tmp_path: Path):
        """When signature has no trust_root_id but hash matches, result is present_unverified."""
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
        }))
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.allowed is True

    def test_no_trust_roots_provided_hash_match_returns_present_unverified(self, tmp_path: Path):
        """When no trust_roots dict is provided but hash matches, present_unverified."""
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_active",
        }))
        result = verify_signature_stub(d, trust_roots={})
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED
        assert result.code == "unknown_trust_root"


# ── Never returns verified ──────────────────────────────────────────────────


class TestNeverReturnsVerified:
    """The verifier stub must never return signature_status=verified."""

    def test_all_paths_never_return_verified(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)

        # No signature
        result = verify_signature_stub(d)
        assert result.signature_status != SIGNATURE_VERIFIED

        # Malformed
        (d / "signature.json").write_text("bad json", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status != SIGNATURE_VERIFIED

        # Missing tree hash
        (d / "signature.json").write_text("{}", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.signature_status != SIGNATURE_VERIFIED

        # Mismatch
        (d / "signature.json").write_text(
            json.dumps({"signed_tree_hash": "0" * 64}), encoding="utf-8"
        )
        result = verify_signature_stub(d)
        assert result.signature_status != SIGNATURE_VERIFIED

        # Hash match + active trust root
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_active",
        }))
        roots = {
            "tr_active": parse_trust_root_dict({
                "trust_root_id": "tr_active",
                "name": "Active",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:aaa",
                "status": "active",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.signature_status != SIGNATURE_VERIFIED


# ── Never recommends trusted_signed ─────────────────────────────────────────


class TestNeverRecommendsTrustedSigned:
    """The verifier stub must never recommend trusted_signed."""

    def test_all_paths_never_recommend_trusted_signed(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)

        # No signature
        result = verify_signature_stub(d)
        assert result.trust_level_recommendation != TRUST_TRUSTED_SIGNED

        # Malformed
        (d / "signature.json").write_text("bad", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.trust_level_recommendation != TRUST_TRUSTED_SIGNED

        # Hash match + active trust root
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_active",
        }))
        roots = {
            "tr_active": parse_trust_root_dict({
                "trust_root_id": "tr_active",
                "name": "Active",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:aaa",
                "status": "active",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.trust_level_recommendation != TRUST_TRUSTED_SIGNED

    def test_trust_level_recommendation_is_always_untrusted(self, tmp_path: Path):
        """In Phase 8B-1, trust_level_recommendation is always untrusted."""
        d = _make_capability_dir(tmp_path)

        scenarios = [
            ("no_file", None),
            ("malformed", "not json"),
            ("empty", "{}"),
            ("mismatch", json.dumps({"signed_tree_hash": "0" * 64})),
        ]

        for name, content in scenarios:
            if content is not None:
                (d / "signature.json").write_text(content, encoding="utf-8")
            result = verify_signature_stub(d)
            assert result.trust_level_recommendation == TRUST_UNTRUSTED, (
                f"Scenario {name}: expected untrusted, got {result.trust_level_recommendation}"
            )


# ── Deterministic ───────────────────────────────────────────────────────────


class TestVerifierStubDeterministic:
    """Same inputs always produce the same outputs."""

    def test_same_input_same_output(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_active",
        }))
        roots = {
            "tr_active": parse_trust_root_dict({
                "trust_root_id": "tr_active",
                "name": "Active",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:aaa",
                "status": "active",
            }),
        }

        result1 = verify_signature_stub(d, trust_roots=roots)
        result2 = verify_signature_stub(d, trust_roots=roots)

        assert result1.signature_status == result2.signature_status
        assert result1.code == result2.code
        assert result1.allowed == result2.allowed
        assert result1.message == result2.message

    def test_deterministic_across_identical_dirs(self, tmp_path: Path):
        """Identical content in different directories produces identical results."""
        import shutil
        d1 = _make_capability_dir(tmp_path, cap_id="same-cap")
        h1 = compute_capability_tree_hash(d1)
        (d1 / "signature.json").write_text(json.dumps({"signed_tree_hash": h1}))

        # Copy to a second location
        d2 = tmp_path / "copy"
        shutil.copytree(d1, d2)

        r1 = verify_signature_stub(d1)
        r2 = verify_signature_stub(d2)
        assert r1.signature_status == r2.signature_status
        assert r1.code == r2.code


# ── Non-mutating ─────────────────────────────────────────────────────────────


class TestVerifierStubNonMutating:
    """verify_signature_stub does not modify the capability directory."""

    def test_verify_does_not_create_files(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        files_before = set(p.name for p in d.rglob("*") if p.is_file())
        verify_signature_stub(d)
        files_after = set(p.name for p in d.rglob("*") if p.is_file())
        assert files_before == files_after

    def test_verify_does_not_modify_existing_files(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text(
            json.dumps({"signed_tree_hash": compute_capability_tree_hash(d)}),
            encoding="utf-8",
        )
        sig_mtime = (d / "signature.json").stat().st_mtime
        manifest_mtime = (d / "manifest.json").stat().st_mtime

        verify_signature_stub(d)

        assert (d / "signature.json").stat().st_mtime == sig_mtime
        assert (d / "manifest.json").stat().st_mtime == manifest_mtime


# ── No crypto dependency ────────────────────────────────────────────────────


class TestNoCryptoDependency:
    """The signature module does not import cryptographic libraries."""

    def test_no_crypto_imports_in_module(self):
        import inspect
        from src.capabilities import signature as sig_mod

        source = inspect.getsource(sig_mod)
        crypto_imports = ["cryptography", "PyNaCl", "nacl", "Crypto",
                          "ecdsa", "ed25519", "rsa", "OpenSSL"]
        for imp in crypto_imports:
            # Allow "ed25519" and "rsa" as string literals (field values),
            # not as imports
            import_patterns = [
                f"import {imp}",
                f"from {imp}",
            ]
            for pattern in import_patterns:
                assert pattern not in source, (
                    f"Crypto import '{pattern}' found in signature module"
                )


# ── No network ──────────────────────────────────────────────────────────────


class TestNoNetwork:
    """The verifier stub has no network calls."""

    def test_no_network_calls(self):
        import inspect
        from src.capabilities import signature as sig_mod

        source = inspect.getsource(sig_mod.verify_signature_stub)
        network_calls = ["requests", "urllib", "http", "socket", "urlopen",
                         "fetch", "curl", "wget"]
        for call in network_calls:
            assert call not in source.lower(), (
                f"Network call '{call}' found in verify_signature_stub"
            )


# ── No script execution ────────────────────────────────────────────────────


class TestVerifierStubNoScriptExecution:
    """The verifier stub hashes scripts as bytes; never executes them."""

    def test_scripts_are_not_executed_during_verification(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        scripts = d / "scripts"
        scripts.mkdir()
        (scripts / "malicious.py").write_text(
            "raise SystemExit('should never run')\n", encoding="utf-8"
        )
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
        }))
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED

    def test_verification_still_works_with_scripts_present(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        scripts = d / "scripts"
        scripts.mkdir()
        (scripts / "setup.sh").write_text("#!/bin/bash\necho hello\n", encoding="utf-8")
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
        }))
        result = verify_signature_stub(d)
        assert result.signature_status != SIGNATURE_INVALID


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestVerifierStubEdgeCases:
    """Edge case handling for the verifier stub."""

    def test_signature_json_with_extra_fields_still_works(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "extra_field": "should be ignored",
            "another_extra": 42,
        }))
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_PRESENT_UNVERIFIED

    def test_empty_trust_roots_dict_same_as_none(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_active",
        }))
        r1 = verify_signature_stub(d, trust_roots={})
        r2 = verify_signature_stub(d, trust_roots=None)
        assert r1.code == r2.code
        assert r1.signature_status == r2.signature_status

    def test_capability_id_fallback_to_dirname(self, tmp_path: Path):
        """If manifest.json has no id/capability_id, fall back to directory name."""
        d = tmp_path / "fallback-cap"
        d.mkdir()
        (d / "CAPABILITY.md").write_text("# Test\n", encoding="utf-8")
        manifest = {"name": "No ID"}  # no id field
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.capability_id == "fallback-cap"

    def test_manifest_unparseable_falls_back_to_dirname(self, tmp_path: Path):
        d = tmp_path / "bad-manifest-cap"
        d.mkdir()
        (d / "manifest.json").write_text("not json", encoding="utf-8")
        result = verify_signature_stub(d)
        assert result.capability_id == "bad-manifest-cap"

    def test_result_to_dict(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        result = verify_signature_stub(d)
        d_result = result.to_dict()
        assert d_result["capability_id"] == result.capability_id
        assert d_result["signature_status"] == SIGNATURE_NOT_PRESENT
        assert d_result["allowed"] is True
        assert d_result["code"] == "no_signature"

    def test_trust_root_expired_returns_invalid(self, tmp_path: Path):
        """Phase 8B-2: expired active trust roots are treated as invalid."""
        d = _make_capability_dir(tmp_path)
        current_hash = compute_capability_tree_hash(d)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": current_hash,
            "trust_root_id": "tr_expired",
        }))
        roots = {
            "tr_expired": parse_trust_root_dict({
                "trust_root_id": "tr_expired",
                "name": "Expired",
                "key_type": "ed25519",
                "public_key_fingerprint": "sha256:ddd",
                "status": "active",
                "expires_at": "2020-01-01T00:00:00Z",
            }),
        }
        result = verify_signature_stub(d, trust_roots=roots)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "trust_root_expired"
        assert result.allowed is False


# ── Hardening: secret-containing signatures are malformed ───────────────────


class TestVerifierStubSecretRejection:
    """Secret-containing signature.json files are treated as malformed."""

    def test_private_key_in_signature_json_treated_as_malformed(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": compute_capability_tree_hash(d),
            "algorithm": "-----BEGIN PRIVATE KEY-----",
        }))
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "malformed_signature"

    def test_api_key_in_signature_json_treated_as_malformed(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": compute_capability_tree_hash(d),
            "signer": "sk-proj-api-key",
        }))
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "malformed_signature"

    def test_secret_field_name_in_signature_json_treated_as_malformed(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": compute_capability_tree_hash(d),
            "private_key": "some-value",
        }))
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "malformed_signature"

    def test_long_field_in_signature_json_treated_as_malformed(self, tmp_path: Path):
        d = _make_capability_dir(tmp_path)
        long_val = "x" * 2_000_000
        (d / "signature.json").write_text(json.dumps({
            "signed_tree_hash": long_val,
        }))
        result = verify_signature_stub(d)
        assert result.signature_status == SIGNATURE_INVALID
        assert result.code == "malformed_signature"


# ── Hardening: comprehensive never-verified audit ───────────────────────────


class TestVerifierStubNeverVerifiedHardening:
    """Exhaustive check that no code path returns verified."""

    def test_every_return_path_checked(self, tmp_path: Path):
        """Programmatic check: scan verify_signature_stub for any return of
        SIGNATURE_VERIFIED, excluding the docstring."""
        import inspect
        from src.capabilities import signature as sig_mod

        source = inspect.getsource(sig_mod.verify_signature_stub)
        # Strip the docstring — only check executable lines
        lines = source.split("\n")
        in_docstring = False
        exec_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if in_docstring or (stripped.count('"""') == 1 and stripped.count("'''") == 0) or (stripped.count("'''") == 1 and stripped.count('"""') == 0):
                    in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            exec_lines.append(line)
        exec_source = "\n".join(exec_lines)

        # SIGNATURE_VERIFIED must never appear in executable code
        assert "SIGNATURE_VERIFIED" not in exec_source, (
            "SIGNATURE_VERIFIED found in executable code of verify_signature_stub"
        )

    def test_trust_level_never_trusted_signed(self, tmp_path: Path):
        """Programmatic check: no TRUST_TRUSTED_SIGNED in executable code."""
        import inspect
        from src.capabilities import signature as sig_mod

        source = inspect.getsource(sig_mod.verify_signature_stub)
        lines = source.split("\n")
        in_docstring = False
        exec_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if in_docstring or (stripped.count('"""') == 1 and stripped.count("'''") == 0) or (stripped.count("'''") == 1 and stripped.count('"""') == 0):
                    in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            exec_lines.append(line)
        exec_source = "\n".join(exec_lines)

        assert "TRUST_TRUSTED_SIGNED" not in exec_source, (
            "TRUST_TRUSTED_SIGNED found in executable code of verify_signature_stub"
        )
