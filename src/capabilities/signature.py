"""Local signature metadata parser and deterministic verifier stub.

Phase 8B-1: signature.json I/O, trust root parsing, and stub verification.
No real cryptographic verification. No crypto dependencies. No network.

Data model:
  CapabilitySignature          - signature metadata from signature.json
  CapabilityTrustRoot          - local trust root configuration
  SignatureVerificationResult  - structured verifier stub output

Functions:
  read_signature(capability_dir)              -> CapabilitySignature | None
  write_signature(capability_dir, signature)  -> None
  parse_signature_dict(data)                  -> CapabilitySignature
  parse_trust_root_dict(data)                 -> CapabilityTrustRoot
  verify_signature_stub(capability_dir, ...)  -> SignatureVerificationResult
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.capabilities.provenance import (
    SIGNATURE_INVALID,
    SIGNATURE_NOT_PRESENT,
    SIGNATURE_PRESENT_UNVERIFIED,
    TRUST_UNTRUSTED,
    compute_capability_tree_hash,
)


# ── Constants ────────────────────────────────────────────────────────────────

_VALID_TRUST_ROOT_STATUSES: frozenset[str] = frozenset({"active", "disabled", "revoked"})

# Patterns that indicate private key material in field values (case-insensitive)
_PRIVATE_KEY_MARKERS: tuple[str, ...] = (
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN EC PRIVATE KEY",
    "BEGIN DSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
    "BEGIN ENCRYPTED PRIVATE KEY",
)

# Field names that indicate secret material — rejected regardless of value
_SECRET_FIELD_NAMES: frozenset[str] = frozenset({
    "private_key", "secret_key", "api_key", "password",
    "secret", "passphrase", "token", "access_token",
    "bearer_token", "refresh_token", "client_secret",
    "signing_key", "key_material", "privatekey", "secretkey", "apikey",
})

# Patterns in field values that look like API keys or bearer tokens
_API_KEY_PATTERNS: tuple[str, ...] = (
    "sk-",           # OpenAI / common API key prefix
    "sk_",           # Alternative API key format
    "Bearer ",       # Bearer token in Authorization header
    "bearer ",       # lowercase variant
)

# Maximum length for any string field in signature or trust root (1 MiB)
_MAX_STRING_FIELD_LENGTH: int = 1_048_576


# ── CapabilitySignature ──────────────────────────────────────────────────────


@dataclass
class CapabilitySignature:
    """Signature metadata stored in signature.json inside a capability directory.

    All fields except metadata are optional — this is a metadata container,
    not a cryptographic artifact. Real verification is out of scope for
    Phase 8B-1.
    """

    algorithm: str | None = None
    key_id: str | None = None
    signer: str | None = None
    signature: str | None = None
    signed_tree_hash: str | None = None
    signed_at: str | None = None
    trust_root_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"metadata": self.metadata}
        if self.algorithm is not None:
            result["algorithm"] = self.algorithm
        if self.key_id is not None:
            result["key_id"] = self.key_id
        if self.signer is not None:
            result["signer"] = self.signer
        if self.signature is not None:
            result["signature"] = self.signature
        if self.signed_tree_hash is not None:
            result["signed_tree_hash"] = self.signed_tree_hash
        if self.signed_at is not None:
            result["signed_at"] = self.signed_at
        if self.trust_root_id is not None:
            result["trust_root_id"] = self.trust_root_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilitySignature":
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(
            algorithm=_optional_str(data.get("algorithm")),
            key_id=_optional_str(data.get("key_id")),
            signer=_optional_str(data.get("signer")),
            signature=_optional_str(data.get("signature")),
            signed_tree_hash=_optional_str(data.get("signed_tree_hash")),
            signed_at=_optional_str(data.get("signed_at")),
            trust_root_id=_optional_str(data.get("trust_root_id")),
            metadata=metadata,
        )


# ── CapabilityTrustRoot ──────────────────────────────────────────────────────


@dataclass
class CapabilityTrustRoot:
    """Local trust root configuration. No network, no remote registry.

    Stored as local configuration only. The trust_root_id links a
    CapabilitySignature to its verifying trust root.
    """

    trust_root_id: str
    name: str
    key_type: str
    public_key_fingerprint: str
    owner: str | None = None
    scope: str | None = None
    status: str = "active"
    created_at: str = ""
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _VALID_TRUST_ROOT_STATUSES:
            raise ValueError(
                f"Invalid trust root status {self.status!r}; "
                f"must be one of {sorted(_VALID_TRUST_ROOT_STATUSES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "trust_root_id": self.trust_root_id,
            "name": self.name,
            "key_type": self.key_type,
            "public_key_fingerprint": self.public_key_fingerprint,
            "status": self.status,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
        if self.owner is not None:
            result["owner"] = self.owner
        if self.scope is not None:
            result["scope"] = self.scope
        if self.expires_at is not None:
            result["expires_at"] = self.expires_at
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityTrustRoot":
        return cls(
            trust_root_id=str(data.get("trust_root_id", "")),
            name=str(data.get("name", "")),
            key_type=str(data.get("key_type", "")),
            public_key_fingerprint=str(data.get("public_key_fingerprint", "")),
            owner=_optional_str(data.get("owner")),
            scope=_optional_str(data.get("scope")),
            status=str(data.get("status", "active")),
            created_at=str(data.get("created_at", "")),
            expires_at=_optional_str(data.get("expires_at")),
            metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
        )


# ── SignatureVerificationResult ──────────────────────────────────────────────


@dataclass
class SignatureVerificationResult:
    """Structured result from the verifier stub.

    Phase 8B-1 guarantees:
    - signature_status is never "verified" (no real crypto).
    - trust_level_recommendation is never "trusted_signed".
    """

    capability_id: str
    signature_status: str = SIGNATURE_NOT_PRESENT
    trust_level_recommendation: str = TRUST_UNTRUSTED
    allowed: bool = True
    code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "signature_status": self.signature_status,
            "trust_level_recommendation": self.trust_level_recommendation,
            "allowed": self.allowed,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


# ── Private key / secret detection ────────────────────────────────────────────


def _contains_private_key_material(value: str) -> bool:
    """Return True if value looks like it contains private key material."""
    upper = value.upper()
    for marker in _PRIVATE_KEY_MARKERS:
        if marker in upper:
            return True
    return False


def _contains_api_key_pattern(value: str) -> bool:
    """Return True if value looks like an API key or bearer token."""
    for pattern in _API_KEY_PATTERNS:
        if pattern in value:
            return True
    return False


def _is_secret_field_name(name: str) -> bool:
    """Return True if the field name indicates secret material."""
    return name.lower() in _SECRET_FIELD_NAMES


def _validate_field_length(field_name: str, value: str) -> None:
    """Raise ValueError if a string field exceeds the max length."""
    if len(value) > _MAX_STRING_FIELD_LENGTH:
        raise ValueError(
            f"Field {field_name!r} exceeds maximum length "
            f"({len(value)} > {_MAX_STRING_FIELD_LENGTH})"
        )


def _validate_no_secrets(data: dict[str, Any]) -> None:
    """Scan a dict for secret field names, private key material, and API key
    patterns. Raises ValueError on detection. Metadata key is skipped."""
    for key, value in data.items():
        if key == "metadata":
            continue
        # Reject secret field names regardless of value
        if _is_secret_field_name(key):
            raise ValueError(
                f"Refusing to parse: field {key!r} is a secret field name"
            )
        if isinstance(value, str):
            _validate_field_length(key, value)
            if _contains_private_key_material(value):
                raise ValueError(
                    f"Refusing to parse: field {key!r} contains private key material"
                )
            if _contains_api_key_pattern(value):
                raise ValueError(
                    f"Refusing to parse: field {key!r} contains an API key or bearer token pattern"
                )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _optional_str(value: Any) -> str | None:
    """Convert a value to str, or return None if it's None."""
    if value is None:
        return None
    return str(value)


def _get_capability_id(capability_dir: Path) -> str:
    """Read capability_id from manifest.json, falling back to directory name."""
    manifest_path = capability_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            cap_id = data.get("id") or data.get("capability_id")
            if cap_id:
                return str(cap_id)
        except (json.JSONDecodeError, OSError):
            pass
    return capability_dir.resolve().name


# ── I/O functions ────────────────────────────────────────────────────────────


def read_signature(capability_dir: Path) -> CapabilitySignature | None:
    """Read signature.json from a capability directory.

    Returns None if the file is missing or unparseable. Never raises.
    """
    sig_path = capability_dir / "signature.json"
    if not sig_path.is_file():
        return None
    try:
        data = json.loads(sig_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return parse_signature_dict(data)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def write_signature(capability_dir: Path, signature: CapabilitySignature) -> None:
    """Write signature metadata to signature.json in the capability directory.

    Only writes signature.json. Does not modify provenance.json or any other file.
    Raises ValueError if a path traversal attempt is detected.
    """
    sig_path = capability_dir / "signature.json"
    # Reject path traversal: check both unresolved and resolved forms
    try:
        sig_path.resolve().relative_to(capability_dir.resolve())
    except ValueError:
        raise ValueError(
            f"signature.json path {sig_path} escapes capability directory {capability_dir}"
        )
    # Also check unresolved to catch .. before resolve collapses it
    if ".." in sig_path.parts:
        raise ValueError(
            f"signature.json path {sig_path} contains '..' — path traversal rejected"
        )

    # Validate before writing
    sig_dict = signature.to_dict()
    _validate_no_secrets(sig_dict)

    sig_path.write_text(
        json.dumps(sig_dict, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Parsing functions ────────────────────────────────────────────────────────


def parse_signature_dict(data: dict[str, Any]) -> CapabilitySignature:
    """Parse a dict into a CapabilitySignature.

    Rejects:
    - Non-dict input (TypeError)
    - Secret field names (ValueError)
    - Private key material in values (ValueError)
    - API key / bearer token patterns in values (ValueError)
    - String fields exceeding max length (ValueError)

    Accepts missing optional fields. Unknown fields are silently ignored
    (they are not round-tripped through CapabilitySignature).
    """
    if not isinstance(data, dict):
        raise TypeError(f"Expected dict for signature data, got {type(data).__name__}")

    _validate_no_secrets(data)

    return CapabilitySignature.from_dict(data)


def parse_trust_root_dict(data: dict[str, Any]) -> CapabilityTrustRoot:
    """Parse a dict into a CapabilityTrustRoot.

    Rejects:
    - Non-dict input (TypeError)
    - Missing or empty trust_root_id (ValueError)
    - Missing or empty public_key_fingerprint (ValueError)
    - Invalid status (ValueError, from CapabilityTrustRoot.__post_init__)
    - Secret field names (ValueError)
    - Private key material in any field (ValueError)
    - API key / bearer token patterns in values (ValueError)
    - String fields exceeding max length (ValueError)

    Unknown fields are silently ignored.
    """
    if not isinstance(data, dict):
        raise TypeError(f"Expected dict for trust root data, got {type(data).__name__}")

    trust_root_id = str(data.get("trust_root_id", ""))
    if not trust_root_id.strip():
        raise ValueError("Trust root requires a non-empty trust_root_id")

    fingerprint = str(data.get("public_key_fingerprint", ""))
    if not fingerprint.strip():
        raise ValueError("Trust root requires a non-empty public_key_fingerprint")

    _validate_no_secrets(data)

    return CapabilityTrustRoot.from_dict(data)


# ── Verifier stub ────────────────────────────────────────────────────────────


def _is_trust_root_expired(trust_root: CapabilityTrustRoot) -> bool:
    """Return True if the trust root has an expires_at in the past."""
    if not trust_root.expires_at:
        return False
    try:
        expires = datetime.fromisoformat(trust_root.expires_at)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return True  # Unparseable expiry → treat as expired


def verify_signature_stub(
    capability_dir: Path,
    trust_roots: dict[str, CapabilityTrustRoot] | None = None,
) -> SignatureVerificationResult:
    """Deterministic verification stub — no real cryptographic verification.

    Decision tree:
    1. No signature.json              → not_present, allowed
    2. signature.json unparseable     → invalid, not allowed (malformed)
    3. signed_tree_hash missing       → invalid, not allowed
    4. signed_tree_hash mismatch      → invalid, not allowed
    5. trust_root_id not found        → present_unverified, allowed
    6. trust root disabled/revoked    → invalid, not allowed
    7. trust root expired             → invalid, not allowed
    8. active trust root + hash match → present_unverified, allowed
       (NOT verified — real crypto not implemented)

    Phase 8B-1 guarantees:
    - Never returns signature_status == "verified".
    - Never recommends trust_level == "trusted_signed".
    - Deterministic: same inputs produce same outputs.
    - Non-mutating: does not write to disk.
    - No network, no crypto, no script execution.

    Args:
        capability_dir: Path to the capability directory.
        trust_roots: Optional dict mapping trust_root_id → CapabilityTrustRoot,
                     or a TrustRootStore (any object with an as_verifier_dict()
                     method). If None or empty, trust root checks are skipped
                     and the result is present_unverified (hash matches) or
                     invalid (hash mismatch).

    Returns:
        SignatureVerificationResult with structured status, recommendation,
        and details.
    """
    capability_id = _get_capability_id(capability_dir)
    sig_path = capability_dir / "signature.json"

    # 1. No signature.json
    if not sig_path.is_file():
        return SignatureVerificationResult(
            capability_id=capability_id,
            signature_status=SIGNATURE_NOT_PRESENT,
            trust_level_recommendation=TRUST_UNTRUSTED,
            allowed=True,
            code="no_signature",
            message="No signature.json found in capability directory.",
        )

    # 2. Try to parse
    try:
        raw = json.loads(sig_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SignatureVerificationResult(
            capability_id=capability_id,
            signature_status=SIGNATURE_INVALID,
            trust_level_recommendation=TRUST_UNTRUSTED,
            allowed=False,
            code="malformed_signature",
            message="signature.json is not valid JSON.",
        )

    if not isinstance(raw, dict):
        return SignatureVerificationResult(
            capability_id=capability_id,
            signature_status=SIGNATURE_INVALID,
            trust_level_recommendation=TRUST_UNTRUSTED,
            allowed=False,
            code="malformed_signature",
            message="signature.json must contain a JSON object.",
        )

    try:
        signature = parse_signature_dict(raw)
    except (TypeError, ValueError):
        return SignatureVerificationResult(
            capability_id=capability_id,
            signature_status=SIGNATURE_INVALID,
            trust_level_recommendation=TRUST_UNTRUSTED,
            allowed=False,
            code="malformed_signature",
            message="signature.json contains invalid signature metadata.",
        )

    # Resolve trust_roots: accept a dict or a TrustRootStore (duck-typed via
    # as_verifier_dict). Phase 8B-2 integration without circular imports.
    if trust_roots is None:
        trust_roots = {}
    elif hasattr(trust_roots, "as_verifier_dict"):
        resolved = trust_roots.as_verifier_dict()  # type: ignore[union-attr]
        if isinstance(resolved, dict):
            trust_roots = resolved
        else:
            trust_roots = {}
    elif not isinstance(trust_roots, dict):
        trust_roots = {}

    # 3. signed_tree_hash missing
    if not signature.signed_tree_hash:
        return SignatureVerificationResult(
            capability_id=capability_id,
            signature_status=SIGNATURE_INVALID,
            trust_level_recommendation=TRUST_UNTRUSTED,
            allowed=False,
            code="missing_tree_hash",
            message="Signature metadata is missing signed_tree_hash.",
        )

    # 4. signed_tree_hash mismatch
    current_hash = compute_capability_tree_hash(capability_dir)
    if signature.signed_tree_hash != current_hash:
        return SignatureVerificationResult(
            capability_id=capability_id,
            signature_status=SIGNATURE_INVALID,
            trust_level_recommendation=TRUST_UNTRUSTED,
            allowed=False,
            code="tree_hash_mismatch",
            message="Signed tree hash does not match computed tree hash.",
            details={
                "signed_tree_hash": signature.signed_tree_hash,
                "computed_tree_hash": current_hash,
            },
        )

    # 5-7. Trust root checks (only if trust_root_id is present)
    trust_root_id = signature.trust_root_id
    if trust_root_id:
        trust_root = trust_roots.get(trust_root_id)
        if trust_root is None:
            return SignatureVerificationResult(
                capability_id=capability_id,
                signature_status=SIGNATURE_PRESENT_UNVERIFIED,
                trust_level_recommendation=TRUST_UNTRUSTED,
                allowed=True,
                code="unknown_trust_root",
                message=f"Trust root {trust_root_id!r} not found in configured trust roots.",
                details={"trust_root_id": trust_root_id},
            )

        if trust_root.status == "disabled":
            return SignatureVerificationResult(
                capability_id=capability_id,
                signature_status=SIGNATURE_INVALID,
                trust_level_recommendation=TRUST_UNTRUSTED,
                allowed=False,
                code="trust_root_disabled",
                message=f"Trust root {trust_root_id!r} is disabled.",
                details={"trust_root_id": trust_root_id, "trust_root_status": "disabled"},
            )

        if trust_root.status == "revoked":
            return SignatureVerificationResult(
                capability_id=capability_id,
                signature_status=SIGNATURE_INVALID,
                trust_level_recommendation=TRUST_UNTRUSTED,
                allowed=False,
                code="trust_root_revoked",
                message=f"Trust root {trust_root_id!r} is revoked.",
                details={"trust_root_id": trust_root_id, "trust_root_status": "revoked"},
            )

        # Phase 8B-2: check expiry on active roots
        if _is_trust_root_expired(trust_root):
            return SignatureVerificationResult(
                capability_id=capability_id,
                signature_status=SIGNATURE_INVALID,
                trust_level_recommendation=TRUST_UNTRUSTED,
                allowed=False,
                code="trust_root_expired",
                message=f"Trust root {trust_root_id!r} is expired.",
                details={
                    "trust_root_id": trust_root_id,
                    "trust_root_status": trust_root.status,
                    "expires_at": trust_root.expires_at,
                },
            )

        # Active trust root + matching hash → present_unverified (NOT verified)
        return SignatureVerificationResult(
            capability_id=capability_id,
            signature_status=SIGNATURE_PRESENT_UNVERIFIED,
            trust_level_recommendation=TRUST_UNTRUSTED,
            allowed=True,
            code="hash_consistent_unverified",
            message=(
                "Tree hash matches signed_tree_hash and trust root is active, "
                "but real cryptographic signature verification is not implemented. "
                "Status remains present_unverified."
            ),
            details={
                "trust_root_id": trust_root_id,
                "trust_root_status": "active",
                "signed_tree_hash": signature.signed_tree_hash,
            },
        )

    # No trust_root_id: hash matches, but no trust root to verify against
    return SignatureVerificationResult(
        capability_id=capability_id,
        signature_status=SIGNATURE_PRESENT_UNVERIFIED,
        trust_level_recommendation=TRUST_UNTRUSTED,
        allowed=True,
        code="hash_consistent_unverified",
        message=(
            "Tree hash matches signed_tree_hash, but no trust_root_id in signature "
            "and no trust roots configured. Status remains present_unverified."
        ),
        details={"signed_tree_hash": signature.signed_tree_hash},
    )
