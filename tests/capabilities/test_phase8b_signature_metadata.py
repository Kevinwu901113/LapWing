"""Phase 8B-1: Signature metadata parsing and I/O tests.

Tests for CapabilitySignature and CapabilityTrustRoot parsing, writing,
validation, and safety checks. No crypto, no verification implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.capabilities.signature import (
    CapabilitySignature,
    CapabilityTrustRoot,
    _contains_private_key_material,
    parse_signature_dict,
    parse_trust_root_dict,
    read_signature,
    write_signature,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_signature_dict(**overrides) -> dict:
    data = {
        "algorithm": "ed25519",
        "key_id": "key-001",
        "signer": "lapwing-core",
        "signature": "abcdef1234567890",
        "signed_tree_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "signed_at": "2026-05-04T10:00:00Z",
        "trust_root_id": "tr_lapwing_core",
        "metadata": {"version": 1},
    }
    data.update(overrides)
    return data


def _make_trust_root_dict(**overrides) -> dict:
    data = {
        "trust_root_id": "tr_test",
        "name": "Test Root",
        "key_type": "ed25519",
        "public_key_fingerprint": "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "owner": "test-org",
        "scope": "global",
        "status": "active",
        "created_at": "2026-05-04T10:00:00Z",
        "metadata": {"version": 1},
    }
    data.update(overrides)
    return data


# ── CapabilitySignature parsing ─────────────────────────────────────────────


class TestParseSignatureDict:
    """Valid signature metadata parses correctly."""

    def test_full_signature_parses(self):
        data = _make_signature_dict()
        sig = parse_signature_dict(data)
        assert sig.algorithm == "ed25519"
        assert sig.key_id == "key-001"
        assert sig.signer == "lapwing-core"
        assert sig.signature == "abcdef1234567890"
        assert sig.signed_tree_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert sig.signed_at == "2026-05-04T10:00:00Z"
        assert sig.trust_root_id == "tr_lapwing_core"
        assert sig.metadata == {"version": 1}

    def test_minimal_signature_parses(self):
        """All fields are optional — empty dict is valid (though signed_tree_hash
        will be None, which the verifier stub handles)."""
        sig = parse_signature_dict({})
        assert sig.algorithm is None
        assert sig.key_id is None
        assert sig.signer is None
        assert sig.signature is None
        assert sig.signed_tree_hash is None
        assert sig.signed_at is None
        assert sig.trust_root_id is None
        assert sig.metadata == {}

    def test_optional_fields_missing_allowed(self):
        sig = parse_signature_dict({"signed_tree_hash": "abc123"})
        assert sig.signed_tree_hash == "abc123"
        assert sig.algorithm is None
        assert sig.key_id is None

    def test_unknown_fields_ignored(self):
        """Extra fields in the JSON are silently ignored."""
        sig = parse_signature_dict({"signed_tree_hash": "abc", "extra_field": "ignored"})
        assert sig.signed_tree_hash == "abc"

    def test_metadata_round_trip(self):
        meta = {"version": 2, "tool": "lapwing-sign", "nested": {"a": 1}}
        sig = parse_signature_dict(_make_signature_dict(metadata=meta))
        assert sig.metadata == meta
        d = sig.to_dict()
        assert d["metadata"] == meta
        rt = CapabilitySignature.from_dict(d)
        assert rt.metadata == meta

    def test_round_trip_via_to_from_dict(self):
        data = _make_signature_dict()
        sig = parse_signature_dict(data)
        d = sig.to_dict()
        rt = CapabilitySignature.from_dict(d)
        # None fields are omitted from to_dict, so reconstruct
        assert rt.algorithm == sig.algorithm
        assert rt.signed_tree_hash == sig.signed_tree_hash
        assert rt.metadata == sig.metadata

    def test_non_dict_rejected(self):
        with pytest.raises(TypeError):
            parse_signature_dict("not a dict")
        with pytest.raises(TypeError):
            parse_signature_dict([])

    def test_metadata_not_dict_coerced(self):
        sig = parse_signature_dict({"metadata": "not_a_dict"})
        assert sig.metadata == {}

    def test_none_values_preserved(self):
        sig = parse_signature_dict({"algorithm": None, "signed_tree_hash": "abc"})
        assert sig.algorithm is None
        assert sig.signed_tree_hash == "abc"


class TestPrivateKeyRejection:
    """Private key material in signature fields is rejected."""

    def test_private_key_in_algorithm_rejected(self):
        data = _make_signature_dict(algorithm="-----BEGIN PRIVATE KEY-----\nxxx\n-----END PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)

    def test_private_key_in_key_id_rejected(self):
        data = _make_signature_dict(key_id="-----BEGIN RSA PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)

    def test_private_key_in_signer_rejected(self):
        data = _make_signature_dict(signer="-----BEGIN EC PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)

    def test_private_key_in_signature_field_rejected(self):
        data = _make_signature_dict(signature="-----BEGIN OPENSSH PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)

    def test_private_key_in_signed_at_rejected(self):
        data = _make_signature_dict(signed_at="-----BEGIN ENCRYPTED PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)

    def test_private_key_in_trust_root_id_rejected(self):
        data = _make_signature_dict(trust_root_id="-----BEGIN DSA PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)

    def test_private_key_detection_helper(self):
        assert _contains_private_key_material("-----BEGIN PRIVATE KEY-----")
        assert _contains_private_key_material("some text BEGIN RSA PRIVATE KEY here")
        assert not _contains_private_key_material("just a normal key id")
        assert not _contains_private_key_material("")

    def test_metadata_values_not_scanned_for_private_keys(self):
        """Metadata is not scanned — only top-level string fields."""
        data = _make_signature_dict(metadata={"key": "-----BEGIN PRIVATE KEY-----"})
        sig = parse_signature_dict(data)
        assert sig.metadata == {"key": "-----BEGIN PRIVATE KEY-----"}


# ── CapabilitySignature I/O ──────────────────────────────────────────────────


class TestReadSignature:
    """read_signature reads signature.json from a capability directory."""

    def test_read_signature_returns_parsed_data(self, tmp_path: Path):
        sig_data = _make_signature_dict()
        (tmp_path / "signature.json").write_text(json.dumps(sig_data), encoding="utf-8")
        sig = read_signature(tmp_path)
        assert sig is not None
        assert sig.signed_tree_hash == sig_data["signed_tree_hash"]
        assert sig.algorithm == "ed25519"

    def test_read_signature_missing_returns_none(self, tmp_path: Path):
        assert read_signature(tmp_path) is None

    def test_read_signature_unparseable_json_returns_none(self, tmp_path: Path):
        (tmp_path / "signature.json").write_text("not json", encoding="utf-8")
        assert read_signature(tmp_path) is None

    def test_read_signature_non_dict_returns_none(self, tmp_path: Path):
        (tmp_path / "signature.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert read_signature(tmp_path) is None

    def test_read_signature_minimal_json_parses(self, tmp_path: Path):
        (tmp_path / "signature.json").write_text('{"signed_tree_hash": "abc"}', encoding="utf-8")
        sig = read_signature(tmp_path)
        assert sig is not None
        assert sig.signed_tree_hash == "abc"


class TestWriteSignature:
    """write_signature writes only signature.json."""

    def test_write_signature_creates_file(self, tmp_path: Path):
        sig = parse_signature_dict(_make_signature_dict())
        write_signature(tmp_path, sig)
        assert (tmp_path / "signature.json").is_file()

    def test_write_signature_does_not_create_other_files(self, tmp_path: Path):
        sig = parse_signature_dict(_make_signature_dict())
        write_signature(tmp_path, sig)
        # Only signature.json should exist (plus any pre-existing files)
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "signature.json"

    def test_write_signature_round_trip(self, tmp_path: Path):
        sig = parse_signature_dict(_make_signature_dict())
        write_signature(tmp_path, sig)
        read_back = read_signature(tmp_path)
        assert read_back is not None
        assert read_back.signed_tree_hash == sig.signed_tree_hash
        assert read_back.algorithm == sig.algorithm
        assert read_back.metadata == sig.metadata

    def test_write_signature_with_none_fields_omitted(self, tmp_path: Path):
        sig = CapabilitySignature(signed_tree_hash="abc123")
        write_signature(tmp_path, sig)
        raw = json.loads((tmp_path / "signature.json").read_text(encoding="utf-8"))
        assert "algorithm" not in raw
        assert "signed_tree_hash" in raw

    def test_write_signature_rejects_private_key_material(self, tmp_path: Path):
        sig = parse_signature_dict(_make_signature_dict())
        sig.algorithm = "-----BEGIN PRIVATE KEY-----"
        with pytest.raises(ValueError, match="private key"):
            write_signature(tmp_path, sig)

    def test_write_signature_path_traversal_rejected(self, tmp_path: Path):
        sig = parse_signature_dict(_make_signature_dict())
        d = tmp_path / "cap"
        d.mkdir()
        malicious = d / ".." / "escape"
        with pytest.raises(ValueError):
            write_signature(malicious, sig)


# ── CapabilityTrustRoot parsing ──────────────────────────────────────────────


class TestParseTrustRootDict:
    """CapabilityTrustRoot parses and validates correctly."""

    def test_full_trust_root_parses(self):
        data = _make_trust_root_dict()
        root = parse_trust_root_dict(data)
        assert root.trust_root_id == "tr_test"
        assert root.name == "Test Root"
        assert root.key_type == "ed25519"
        assert root.public_key_fingerprint == "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        assert root.owner == "test-org"
        assert root.scope == "global"
        assert root.status == "active"
        assert root.created_at == "2026-05-04T10:00:00Z"
        assert root.metadata == {"version": 1}

    def test_active_status_accepted(self):
        root = parse_trust_root_dict(_make_trust_root_dict(status="active"))
        assert root.status == "active"

    def test_disabled_status_accepted(self):
        root = parse_trust_root_dict(_make_trust_root_dict(status="disabled"))
        assert root.status == "disabled"

    def test_revoked_status_accepted(self):
        root = parse_trust_root_dict(_make_trust_root_dict(status="revoked"))
        assert root.status == "revoked"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="Invalid trust root status"):
            parse_trust_root_dict(_make_trust_root_dict(status="pending"))

    def test_missing_fingerprint_rejected(self):
        data = _make_trust_root_dict()
        del data["public_key_fingerprint"]
        with pytest.raises(ValueError, match="public_key_fingerprint"):
            parse_trust_root_dict(data)

    def test_empty_fingerprint_rejected(self):
        with pytest.raises(ValueError, match="public_key_fingerprint"):
            parse_trust_root_dict(_make_trust_root_dict(public_key_fingerprint=""))

    def test_whitespace_only_fingerprint_rejected(self):
        with pytest.raises(ValueError, match="public_key_fingerprint"):
            parse_trust_root_dict(_make_trust_root_dict(public_key_fingerprint="   "))

    def test_non_dict_rejected(self):
        with pytest.raises(TypeError):
            parse_trust_root_dict("not a dict")
        with pytest.raises(TypeError):
            parse_trust_root_dict([])

    def test_metadata_round_trip(self):
        meta = {"version": 2, "contact": "admin@example.com"}
        root = parse_trust_root_dict(_make_trust_root_dict(metadata=meta))
        assert root.metadata == meta
        d = root.to_dict()
        assert d["metadata"] == meta
        rt = CapabilityTrustRoot.from_dict(d)
        assert rt.metadata == meta

    def test_round_trip_via_to_from_dict(self):
        data = _make_trust_root_dict()
        root = parse_trust_root_dict(data)
        d = root.to_dict()
        rt = CapabilityTrustRoot.from_dict(d)
        assert rt.trust_root_id == root.trust_root_id
        assert rt.status == root.status
        assert rt.public_key_fingerprint == root.public_key_fingerprint

    def test_expires_at_optional(self):
        root = parse_trust_root_dict(_make_trust_root_dict(expires_at=None))
        assert root.expires_at is None

    def test_expires_at_in_future_accepted(self):
        root = parse_trust_root_dict(_make_trust_root_dict(expires_at="2027-01-01T00:00:00Z"))
        assert root.expires_at == "2027-01-01T00:00:00Z"

    def test_expired_trust_root_still_parses(self):
        """An expired trust root parses successfully — the verifier stub
        handles the active/disabled/revoked check. Expiry is metadata
        for now."""
        root = parse_trust_root_dict(_make_trust_root_dict(expires_at="2020-01-01T00:00:00Z"))
        assert root.status == "active"
        assert root.expires_at == "2020-01-01T00:00:00Z"

    def test_owner_optional(self):
        data = _make_trust_root_dict()
        del data["owner"]
        root = parse_trust_root_dict(data)
        assert root.owner is None

    def test_scope_optional(self):
        data = _make_trust_root_dict()
        del data["scope"]
        root = parse_trust_root_dict(data)
        assert root.scope is None


class TestTrustRootPrivateKeyRejection:
    """Private key material in trust root fields is rejected."""

    def test_private_key_in_key_type_rejected(self):
        data = _make_trust_root_dict(key_type="-----BEGIN PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_trust_root_dict(data)

    def test_private_key_in_fingerprint_rejected(self):
        data = _make_trust_root_dict(public_key_fingerprint="-----BEGIN RSA PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_trust_root_dict(data)

    def test_private_key_in_name_rejected(self):
        data = _make_trust_root_dict(name="-----BEGIN EC PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_trust_root_dict(data)

    def test_private_key_in_owner_rejected(self):
        data = _make_trust_root_dict(owner="-----BEGIN OPENSSH PRIVATE KEY-----")
        with pytest.raises(ValueError, match="private key"):
            parse_trust_root_dict(data)


# ── No script execution ──────────────────────────────────────────────────────


class TestNoScriptExecution:
    """Signature parsing and I/O never executes scripts."""

    def test_read_signature_does_not_execute_scripts(self, tmp_path: Path):
        """A script alongside signature.json is not executed."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "malicious.py").write_text(
            "raise SystemExit('should never run')", encoding="utf-8"
        )
        (tmp_path / "signature.json").write_text(
            json.dumps(_make_signature_dict()), encoding="utf-8"
        )
        sig = read_signature(tmp_path)
        assert sig is not None
        assert sig.algorithm == "ed25519"

    def test_write_signature_does_not_execute_scripts(self, tmp_path: Path):
        """Writing signature.json does not execute existing scripts."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "malicious.py").write_text(
            "raise SystemExit('should never run')", encoding="utf-8"
        )
        sig = parse_signature_dict(_make_signature_dict())
        write_signature(tmp_path, sig)
        assert (tmp_path / "signature.json").is_file()

    def test_parse_signature_does_not_touch_filesystem(self, tmp_path: Path):
        """parse_signature_dict is a pure dict parser — no filesystem access."""
        data = _make_signature_dict()
        sig = parse_signature_dict(data)
        assert sig.signed_tree_hash == data["signed_tree_hash"]


# ── Hardening: secret field names ──────────────────────────────────────────


class TestSecretFieldNameRejection:
    """Field names that indicate secret material are rejected regardless of value."""

    @pytest.mark.parametrize("field_name", [
        "private_key", "secret_key", "api_key", "password",
        "secret", "passphrase", "token", "access_token",
        "bearer_token", "refresh_token", "client_secret",
        "signing_key", "privatekey", "secretkey", "apikey",
    ])
    def test_secret_field_name_rejected_in_signature(self, field_name):
        data = _make_signature_dict()
        data[field_name] = "harmless_value"
        with pytest.raises(ValueError, match="secret field name"):
            parse_signature_dict(data)

    @pytest.mark.parametrize("field_name", [
        "private_key", "secret_key", "api_key", "password",
    ])
    def test_secret_field_name_rejected_in_trust_root(self, field_name):
        data = _make_trust_root_dict()
        data[field_name] = "harmless_value"
        with pytest.raises(ValueError, match="secret field name"):
            parse_trust_root_dict(data)

    def test_case_insensitive_secret_field_names(self):
        data = _make_signature_dict()
        data["PRIVATE_KEY"] = "value"
        with pytest.raises(ValueError, match="secret field name"):
            parse_signature_dict(data)

    def test_metadata_can_contain_secret_field_names(self):
        """The 'metadata' key is exempt from field-name scanning."""
        data = _make_signature_dict()
        data["metadata"] = {"private_key": "this is fine in metadata"}
        sig = parse_signature_dict(data)
        assert sig.metadata == {"private_key": "this is fine in metadata"}


# ── Hardening: API key / bearer token patterns ──────────────────────────────


class TestApiKeyPatternRejection:
    """Values that look like API keys or bearer tokens are rejected."""

    def test_sk_prefix_rejected_in_signature(self):
        data = _make_signature_dict(signer="sk-proj-abc123")
        with pytest.raises(ValueError, match="API key"):
            parse_signature_dict(data)

    def test_sk_underscore_prefix_rejected(self):
        data = _make_signature_dict(signer="sk_abc123")
        with pytest.raises(ValueError, match="API key"):
            parse_signature_dict(data)

    def test_bearer_token_rejected(self):
        data = _make_signature_dict(signer="Bearer eyJhbGciOiJIUzI1NiJ9.xxx")
        with pytest.raises(ValueError, match="API key"):
            parse_signature_dict(data)

    def test_lowercase_bearer_token_rejected(self):
        data = _make_signature_dict(signer="bearer some-token-value")
        with pytest.raises(ValueError, match="API key"):
            parse_signature_dict(data)

    def test_api_key_pattern_rejected_in_trust_root(self):
        data = _make_trust_root_dict(name="sk-proj-key")
        with pytest.raises(ValueError, match="API key"):
            parse_trust_root_dict(data)

    def test_api_key_in_metadata_not_scanned(self):
        """metadata values are exempt from API key scanning."""
        data = _make_signature_dict(metadata={"auth": "Bearer token123"})
        sig = parse_signature_dict(data)
        assert sig.metadata == {"auth": "Bearer token123"}


# ── Hardening: field length limits ──────────────────────────────────────────


class TestFieldLengthLimits:
    """String fields exceeding max length are rejected."""

    def test_long_algorithm_rejected(self):
        long_val = "x" * 2_000_000  # ~2 MiB
        data = _make_signature_dict(algorithm=long_val)
        with pytest.raises(ValueError, match="exceeds maximum length"):
            parse_signature_dict(data)

    def test_long_signed_tree_hash_rejected(self):
        long_val = "x" * 2_000_000
        data = _make_signature_dict(signed_tree_hash=long_val)
        with pytest.raises(ValueError, match="exceeds maximum length"):
            parse_signature_dict(data)

    def test_long_trust_root_name_rejected(self):
        long_val = "x" * 2_000_000
        data = _make_trust_root_dict(name=long_val)
        with pytest.raises(ValueError, match="exceeds maximum length"):
            parse_trust_root_dict(data)

    def test_normal_length_fields_accepted(self):
        """Fields within limits parse correctly."""
        val_1k = "x" * 1000
        data = _make_signature_dict(algorithm=val_1k)
        sig = parse_signature_dict(data)
        assert sig.algorithm == val_1k


# ── Hardening: missing trust_root_id rejected ────────────────────────────────


class TestTrustRootIdRequired:
    """trust_root_id is required for trust roots."""

    def test_missing_trust_root_id_rejected(self):
        data = _make_trust_root_dict()
        del data["trust_root_id"]
        with pytest.raises(ValueError, match="trust_root_id"):
            parse_trust_root_dict(data)

    def test_empty_trust_root_id_rejected(self):
        with pytest.raises(ValueError, match="trust_root_id"):
            parse_trust_root_dict(_make_trust_root_dict(trust_root_id=""))

    def test_whitespace_only_trust_root_id_rejected(self):
        with pytest.raises(ValueError, match="trust_root_id"):
            parse_trust_root_dict(_make_trust_root_dict(trust_root_id="   "))


# ── Hardening: read_signature handles corrupt data safely ───────────────────


class TestReadSignatureHardening:
    """read_signature returns None for secret-containing data."""

    def test_secret_field_in_file_returns_none(self, tmp_path: Path):
        (tmp_path / "signature.json").write_text(
            json.dumps({"private_key": "value", "signed_tree_hash": "abc"}),
            encoding="utf-8",
        )
        assert read_signature(tmp_path) is None

    def test_private_key_material_in_file_returns_none(self, tmp_path: Path):
        (tmp_path / "signature.json").write_text(
            json.dumps({
                "signed_tree_hash": "abc",
                "algorithm": "-----BEGIN PRIVATE KEY-----",
            }),
            encoding="utf-8",
        )
        assert read_signature(tmp_path) is None

    def test_api_key_in_file_returns_none(self, tmp_path: Path):
        (tmp_path / "signature.json").write_text(
            json.dumps({
                "signed_tree_hash": "abc",
                "signer": "sk-proj-secret-key",
            }),
            encoding="utf-8",
        )
        assert read_signature(tmp_path) is None

    def test_valid_signature_still_reads(self, tmp_path: Path):
        """Valid signature without secrets still reads correctly."""
        (tmp_path / "signature.json").write_text(
            json.dumps(_make_signature_dict()),
            encoding="utf-8",
        )
        sig = read_signature(tmp_path)
        assert sig is not None
        assert sig.algorithm == "ed25519"


# ── Hardening: write_signature rejects secrets ───────────────────────────────


class TestWriteSignatureHardening:
    """write_signature rejects data with secret patterns."""

    def test_api_key_in_signature_field_rejected_at_write(self, tmp_path: Path):
        sig = parse_signature_dict(_make_signature_dict())
        sig.signer = "sk-proj-key"
        with pytest.raises(ValueError, match="API key"):
            write_signature(tmp_path, sig)

    def test_secret_field_name_in_metadata_ok_at_write(self, tmp_path: Path):
        """Secret field names in metadata are allowed."""
        sig = parse_signature_dict(_make_signature_dict(metadata={"api_key": "not-scanned"}))
        write_signature(tmp_path, sig)
        assert (tmp_path / "signature.json").is_file()


# ── Hardening: unknown fields silently ignored ──────────────────────────────


class TestUnknownFieldsIgnored:
    """Unknown fields in JSON are silently ignored (not round-tripped)."""

    def test_unknown_fields_in_signature_ignored(self):
        data = _make_signature_dict()
        data["extra_unknown"] = "should be ignored"
        data["another_unknown"] = 42
        sig = parse_signature_dict(data)
        d = sig.to_dict()
        assert "extra_unknown" not in d
        assert "another_unknown" not in d

    def test_unknown_fields_in_trust_root_ignored(self):
        data = _make_trust_root_dict()
        data["version"] = 2
        data["endpoint"] = "https://example.com"
        root = parse_trust_root_dict(data)
        d = root.to_dict()
        assert "version" not in d
        assert "endpoint" not in d


# ── Hardening: comprehensive private key marker coverage ────────────────────


class TestComprehensivePrivateKeyMarkers:
    """All documented private key markers are detected."""

    @pytest.mark.parametrize("marker", [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
    ])
    def test_all_private_key_markers_rejected(self, marker):
        data = _make_signature_dict(algorithm=marker)
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)

    def test_private_key_marker_in_trust_root_rejected(self):
        for field in ["name", "key_type", "owner"]:
            data = _make_trust_root_dict(**{field: "-----BEGIN PRIVATE KEY-----"})
            with pytest.raises(ValueError, match="private key"):
                parse_trust_root_dict(data)

    def test_full_pem_block_rejected(self):
        pem = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"""
        data = _make_signature_dict(signer=pem)
        with pytest.raises(ValueError, match="private key"):
            parse_signature_dict(data)
