"""Capability provenance and integrity foundation.

Phase 8A-1: provenance.json, deterministic tree hashing, and trust policy
analysis. No runtime behavior changes. No run_capability, no execution,
no network, no signature verification yet.

Data model:
  CapabilityProvenance  - serializable provenance record
  TrustDecision         - structured trust analysis decision
  CapabilityTrustPolicy - analytical policy (returns decisions, never gates)

Tree hash:
  compute_capability_tree_hash(directory)     -> str (SHA256 hex)
  compute_package_hash(directory)             -> str (SHA256 hex, alias)
  verify_content_hash_against_provenance(...) -> bool

I/O:
  write_provenance(directory, **kwargs)       -> CapabilityProvenance
  read_provenance(directory)                  -> CapabilityProvenance | None
  update_provenance_integrity_status(...)     -> CapabilityProvenance | None
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Constants ────────────────────────────────────────────────────────────────

# Allowed values for provenance fields
PROVENANCE_SOURCE_TYPES: frozenset[str] = frozenset({
    "local_package", "manual_draft", "curator_proposal",
    "quarantine_activation", "unknown",
})

PROVENANCE_TRUST_LEVELS: frozenset[str] = frozenset({
    "unknown", "untrusted", "reviewed", "trusted_local", "trusted_signed",
})

PROVENANCE_INTEGRITY_STATUSES: frozenset[str] = frozenset({
    "unknown", "verified", "mismatch",
})

PROVENANCE_SIGNATURE_STATUSES: frozenset[str] = frozenset({
    "not_present", "present_unverified", "verified", "invalid",
})

# Convenience constants
SOURCE_LOCAL_PACKAGE = "local_package"
SOURCE_MANUAL_DRAFT = "manual_draft"
SOURCE_CURATOR_PROPOSAL = "curator_proposal"
SOURCE_QUARANTINE_ACTIVATION = "quarantine_activation"
SOURCE_UNKNOWN = "unknown"

TRUST_UNKNOWN = "unknown"
TRUST_UNTRUSTED = "untrusted"
TRUST_REVIEWED = "reviewed"
TRUST_TRUSTED_LOCAL = "trusted_local"
TRUST_TRUSTED_SIGNED = "trusted_signed"

INTEGRITY_UNKNOWN = "unknown"
INTEGRITY_VERIFIED = "verified"
INTEGRITY_MISMATCH = "mismatch"

SIGNATURE_NOT_PRESENT = "not_present"
SIGNATURE_PRESENT_UNVERIFIED = "present_unverified"
SIGNATURE_VERIFIED = "verified"
SIGNATURE_INVALID = "invalid"

# Directories excluded from tree hashing (volatile / post-hoc artifacts)
_TREE_HASH_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "evals",
    "traces",
    "versions",
    "quarantine_audit_reports",
    "quarantine_reviews",
    "quarantine_transition_requests",
    "quarantine_activation_plans",
    "quarantine_activation_reports",
    "provenance_verification_logs",
})

# Files excluded from tree hashing (alongside provenance.json itself)
_TREE_HASH_EXCLUDED_FILES: frozenset[str] = frozenset({
    "provenance.json",
    "signature.json",
    "import_report.json",
    "activation_report.json",
    ".gitkeep",
})

# File extensions excluded from tree hashing (database/index/cache files)
_TREE_HASH_EXCLUDED_SUFFIXES: tuple[str, ...] = (".sqlite", ".db", ".pyc", ".pyo")

# Fields to strip from manifest.json before hashing (self-referential, per
# existing compute_content_hash rules in hashing.py).
_MANIFEST_COMPUTED_FIELDS: frozenset[str] = frozenset({
    "content_hash", "created_at", "updated_at",
})


# ── CapabilityProvenance ─────────────────────────────────────────────────────


@dataclass
class CapabilityProvenance:
    """Serializable provenance record for a capability.

    Stored as provenance.json alongside manifest.json in the capability
    directory (quarantine or active scope).
    """

    provenance_id: str
    capability_id: str
    source_type: str = SOURCE_UNKNOWN
    source_path_hash: str | None = None
    source_content_hash: str = ""
    imported_at: str | None = None
    imported_by: str | None = None
    activated_at: str | None = None
    activated_by: str | None = None
    parent_provenance_id: str | None = None
    origin_capability_id: str | None = None
    origin_scope: str | None = None
    trust_level: str = TRUST_UNTRUSTED
    integrity_status: str = INTEGRITY_UNKNOWN
    signature_status: str = SIGNATURE_NOT_PRESENT
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance_id": self.provenance_id,
            "capability_id": self.capability_id,
            "source_type": self.source_type,
            "source_path_hash": self.source_path_hash,
            "source_content_hash": self.source_content_hash,
            "imported_at": self.imported_at,
            "imported_by": self.imported_by,
            "activated_at": self.activated_at,
            "activated_by": self.activated_by,
            "parent_provenance_id": self.parent_provenance_id,
            "origin_capability_id": self.origin_capability_id,
            "origin_scope": self.origin_scope,
            "trust_level": self.trust_level,
            "integrity_status": self.integrity_status,
            "signature_status": self.signature_status,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityProvenance":
        return cls(
            provenance_id=str(data.get("provenance_id", "")),
            capability_id=str(data.get("capability_id", "")),
            source_type=str(data.get("source_type", SOURCE_UNKNOWN)),
            source_path_hash=data.get("source_path_hash"),
            source_content_hash=str(data.get("source_content_hash", "")),
            imported_at=data.get("imported_at"),
            imported_by=data.get("imported_by"),
            activated_at=data.get("activated_at"),
            activated_by=data.get("activated_by"),
            parent_provenance_id=data.get("parent_provenance_id"),
            origin_capability_id=data.get("origin_capability_id"),
            origin_scope=data.get("origin_scope"),
            trust_level=str(data.get("trust_level", TRUST_UNTRUSTED)),
            integrity_status=str(data.get("integrity_status", INTEGRITY_UNKNOWN)),
            signature_status=str(data.get("signature_status", SIGNATURE_NOT_PRESENT)),
            metadata=data.get("metadata", {}),
        )


# ── TrustDecision ────────────────────────────────────────────────────────────


@dataclass
class TrustDecision:
    """Structured trust analysis decision. No gating behavior — caller decides
    whether to act on the decision.

    Mirrors PolicyDecision: allowed, severity, code, message, details.
    """

    allowed: bool
    severity: str = "info"
    code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, code: str = "trust_allow", message: str = "", **details) -> "TrustDecision":
        return cls(allowed=True, severity="info", code=code, message=message, details=details)

    @classmethod
    def warn(cls, code: str, message: str, **details) -> "TrustDecision":
        return cls(allowed=True, severity="warning", code=code, message=message, details=details)

    @classmethod
    def deny(cls, code: str, message: str, **details) -> "TrustDecision":
        return cls(allowed=False, severity="error", code=code, message=message, details=details)


# ── Tree hash helpers ────────────────────────────────────────────────────────


def _should_include_in_tree_hash(file_path: Path, root_dir: Path) -> bool:
    """Determine whether a file should be included in the tree hash.

    Excludes:
    - Files named in _TREE_HASH_EXCLUDED_FILES
    - Files with excluded suffixes (.sqlite, .db, .pyc, .pyo)
    - Files whose relative path traverses an excluded directory
    - Files whose path contains a segment starting with '.'
    - .gitkeep files
    """
    name = file_path.name
    if name in _TREE_HASH_EXCLUDED_FILES or name == ".gitkeep":
        return False
    if name.endswith(_TREE_HASH_EXCLUDED_SUFFIXES):
        return False

    try:
        rel = file_path.relative_to(root_dir)
    except ValueError:
        return False

    for part in rel.parts:
        if part.startswith("."):
            return False
        if part in _TREE_HASH_EXCLUDED_DIRS:
            return False
    return True


def _normalize_manifest_bytes(path: Path) -> bytes:
    """Read manifest.json, strip computed fields, return canonical JSON bytes.

    This ensures the tree hash is stable even when content_hash/created_at/
    updated_at change due to re-computation — matching the existing
    compute_content_hash behavior in hashing.py.
    """
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw
    for field in _MANIFEST_COMPUTED_FIELDS:
        data.pop(field, None)
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")


def _file_bytes_for_hash(path: Path) -> bytes:
    """Return the bytes to hash for a file. For manifest.json, normalizes
    by stripping computed fields. For all other files, returns raw bytes."""
    if path.name == "manifest.json":
        return _normalize_manifest_bytes(path)
    return path.read_bytes()


def compute_capability_tree_hash(directory: Path) -> str:
    """Compute a deterministic content hash over a capability directory tree.

    Includes: CAPABILITY.md, manifest.json, scripts/, tests/, examples/
    Excludes: evals/, traces/, versions/, quarantine artifacts, caches, hidden files

    Algorithm ("sha256_path_sorted"):
      1. Walk directory tree, filter to included regular files.
      2. For each file: SHA256(relative_path_bytes + b":" + file_bytes).
         manifest.json is normalized (computed fields stripped).
      3. Sort per-file hashes by relative path.
      4. Final: SHA256("||".join("relpath=hash" for each)).

    Returns empty string for non-existent or empty directories.
    """
    directory = directory.resolve()
    if not directory.is_dir():
        return ""

    per_file: list[tuple[str, str]] = []

    for file_path in sorted(directory.rglob("*")):
        if not file_path.is_file():
            continue
        # Reject symlinks — never follow them
        if file_path.is_symlink():
            continue
        if not _should_include_in_tree_hash(file_path, directory):
            continue
        try:
            rel = file_path.relative_to(directory)
            content = _file_bytes_for_hash(file_path)
            combined = str(rel).encode("utf-8") + b":" + content
            h = hashlib.sha256(combined).hexdigest()
            per_file.append((str(rel), h))
        except (OSError, ValueError):
            continue

    if not per_file:
        return hashlib.sha256(b"").hexdigest()

    per_file.sort(key=lambda x: x[0])
    joined = "||".join(f"{rel}={h}" for rel, h in per_file)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def compute_package_hash(directory: Path) -> str:
    """Alias for compute_capability_tree_hash — used during import for clarity."""
    return compute_capability_tree_hash(directory)


def verify_content_hash_against_provenance(
    capability_dir: Path,
    provenance: CapabilityProvenance,
) -> bool:
    """Check whether current directory tree hash matches provenance record."""
    current = compute_capability_tree_hash(capability_dir)
    if not current or not provenance.source_content_hash:
        return False
    return current == provenance.source_content_hash


# ── I/O functions ────────────────────────────────────────────────────────────


def _generate_provenance_id() -> str:
    return f"prov_{uuid.uuid4().hex[:12]}"


def write_provenance(
    directory: Path,
    *,
    capability_id: str,
    source_type: str = SOURCE_UNKNOWN,
    source_path_hash: str | None = None,
    source_content_hash: str = "",
    imported_at: str | None = None,
    imported_by: str | None = None,
    activated_at: str | None = None,
    activated_by: str | None = None,
    parent_provenance_id: str | None = None,
    origin_capability_id: str | None = None,
    origin_scope: str | None = None,
    trust_level: str = TRUST_UNTRUSTED,
    integrity_status: str = INTEGRITY_UNKNOWN,
    signature_status: str = SIGNATURE_NOT_PRESENT,
    metadata: dict[str, Any] | None = None,
) -> CapabilityProvenance:
    """Build a provenance record and write provenance.json to directory."""
    now = datetime.now(timezone.utc)

    provenance = CapabilityProvenance(
        provenance_id=_generate_provenance_id(),
        capability_id=capability_id,
        source_type=source_type,
        source_path_hash=source_path_hash,
        source_content_hash=source_content_hash,
        imported_at=imported_at,
        imported_by=imported_by,
        activated_at=activated_at,
        activated_by=activated_by,
        parent_provenance_id=parent_provenance_id,
        origin_capability_id=origin_capability_id,
        origin_scope=origin_scope,
        trust_level=trust_level,
        integrity_status=integrity_status,
        signature_status=signature_status,
        metadata=metadata or {},
    )

    prov_path = directory / "provenance.json"
    prov_path.write_text(
        json.dumps(provenance.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return provenance


def read_provenance(directory: Path) -> CapabilityProvenance | None:
    """Read provenance.json from a capability directory. Returns None if
    missing or unparseable. Never raises."""
    prov_path = directory / "provenance.json"
    if not prov_path.is_file():
        return None
    try:
        data = json.loads(prov_path.read_text(encoding="utf-8"))
        return CapabilityProvenance.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def update_provenance_integrity_status(
    directory: Path,
    integrity_status: str,
) -> CapabilityProvenance | None:
    """Update integrity_status on an existing provenance.json. Returns None if
    no provenance exists or the status value is invalid."""
    if integrity_status not in PROVENANCE_INTEGRITY_STATUSES:
        return None
    prov = read_provenance(directory)
    if prov is None:
        return None
    prov.integrity_status = integrity_status
    prov_path = directory / "provenance.json"
    prov_path.write_text(
        json.dumps(prov.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return prov


# ── Trust policy (analytical, non-gating) ────────────────────────────────────


class CapabilityTrustPolicy:
    """Analytical trust policy. Evaluates provenance data and returns
    TrustDecision objects. Does NOT gate any import/activation/execution
    paths in Phase 8A-1.

    All methods are pure: they accept data and return decisions.
    No store mutation, no LLM calls, no script execution.
    """

    def evaluate_provenance(
        self,
        provenance: CapabilityProvenance | None,
    ) -> TrustDecision:
        """Evaluate trustworthiness from provenance data alone."""
        if provenance is None:
            return TrustDecision.warn(
                "provenance_missing",
                "No provenance record found; trust defaults to untrusted.",
                trust_level=TRUST_UNTRUSTED,
                integrity_status=INTEGRITY_UNKNOWN,
            )

        trust = provenance.trust_level
        if trust not in PROVENANCE_TRUST_LEVELS:
            trust = TRUST_UNTRUSTED

        integrity = provenance.integrity_status
        if integrity not in PROVENANCE_INTEGRITY_STATUSES:
            integrity = INTEGRITY_UNKNOWN

        sig = provenance.signature_status
        if sig not in PROVENANCE_SIGNATURE_STATUSES:
            sig = SIGNATURE_NOT_PRESENT

        if trust == TRUST_TRUSTED_SIGNED and sig != SIGNATURE_VERIFIED:
            return TrustDecision.warn(
                "trusted_signed_without_verified_signature",
                "Provenance claims trusted_signed but signature is not verified.",
                trust_level=trust,
                integrity_status=integrity,
                signature_status=sig,
            )

        return TrustDecision.allow(
            "provenance_evaluated",
            f"Provenance trust={trust}, integrity={integrity}, signature={sig}.",
            trust_level=trust,
            integrity_status=integrity,
            signature_status=sig,
        )

    def can_activate_from_quarantine(
        self,
        provenance: CapabilityProvenance | None,
        audit_result: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
    ) -> TrustDecision:
        """Check whether trust state supports activation from quarantine."""
        if provenance is None:
            return TrustDecision.warn(
                "activate_no_provenance",
                "No provenance record; activation allowed but untrusted.",
                trust_level=TRUST_UNTRUSTED,
            )

        integrity = provenance.integrity_status
        if integrity == INTEGRITY_MISMATCH:
            return TrustDecision.deny(
                "activate_integrity_mismatch",
                "Cannot activate: integrity mismatch detected.",
                trust_level=provenance.trust_level,
                integrity_status=integrity,
            )

        sig = provenance.signature_status
        if sig == SIGNATURE_INVALID:
            return TrustDecision.deny(
                "activate_signature_invalid",
                "Cannot activate: provenance signature is invalid.",
                trust_level=provenance.trust_level,
                signature_status=sig,
            )

        trust = provenance.trust_level
        if trust == TRUST_UNTRUSTED:
            # Warn but allow — review/audit may override
            has_review = review is not None and review.get("review_status") == "approved_for_testing"
            has_audit = audit_result is not None and audit_result.get("passed", False)
            if has_review and has_audit:
                return TrustDecision.allow(
                    "activate_untrusted_with_review",
                    "Activation allowed: untrusted provenance but review+audit passed.",
                    trust_level=trust,
                )
            return TrustDecision.warn(
                "activate_untrusted",
                "Activation from untrusted provenance; recommend review+audit first.",
                trust_level=trust,
            )

        return TrustDecision.allow(
            "activate_allowed",
            f"Activation allowed with trust_level={trust}.",
            trust_level=trust,
            integrity_status=integrity,
        )

    def can_retrieve(
        self,
        capability_manifest: Any,
        provenance: CapabilityProvenance | None = None,
    ) -> TrustDecision:
        """Check whether a capability may be returned in retrieval results.

        Phase 8A-1: always allows retrieval. Returns warning if provenance
        is missing or untrusted, but never denies.
        """
        if provenance is None:
            return TrustDecision.warn(
                "retrieve_no_provenance",
                "Retrieving capability without provenance record.",
                trust_level=TRUST_UNTRUSTED,
            )

        trust = provenance.trust_level
        integrity = provenance.integrity_status

        if integrity == INTEGRITY_MISMATCH:
            return TrustDecision.warn(
                "retrieve_integrity_mismatch",
                "Retrieving capability with integrity mismatch; results may be unreliable.",
                trust_level=trust,
                integrity_status=integrity,
            )

        return TrustDecision.allow(
            "retrieve_allowed",
            f"Retrieval allowed with trust_level={trust}.",
            trust_level=trust,
            integrity_status=integrity,
        )

    def can_promote_to_stable(
        self,
        capability_manifest: Any,
        provenance: CapabilityProvenance | None = None,
        eval_record: Any | None = None,
        *,
        risk_level: str | None = None,
        approval: Any | None = None,
    ) -> TrustDecision:
        """Check whether trust state supports promotion to stable.

        Risk-specific rules (Phase 8C-1):

        Low risk:
          - reviewed provenance + verified integrity allowed (warns).
          - Missing provenance for legacy/manual warns, does not block.

        Medium risk:
          - reviewed or trusted_local provenance required.
          - Missing provenance blocks (cannot assess trust).

        High risk:
          - trusted_local or trusted_signed required.
          - reviewed provenance blocks.
          - Missing provenance blocks.

        Hard blocks (any risk):
          - integrity mismatch
          - invalid signature
          - untrusted / unknown trust level
        """
        risk = risk_level or "low"

        # ── No provenance: risk-dependent handling ─────────────────────────
        if provenance is None:
            if risk == "low":
                return TrustDecision.warn(
                    "stable_no_provenance_low_risk",
                    "No provenance record; allowing stable promotion for low-risk "
                    "capability (legacy/manual exception). "
                    "Consider creating provenance for audit trail.",
                    risk_level=risk,
                )
            return TrustDecision.deny(
                "stable_no_provenance",
                f"Cannot promote to stable: no provenance record "
                f"(risk_level={risk}). Provenance with at minimum "
                "reviewed or trusted_local trust is required.",
                risk_level=risk,
            )

        trust = provenance.trust_level
        integrity = provenance.integrity_status

        # ── Hard blocks: integrity / signature ─────────────────────────────
        if integrity == INTEGRITY_MISMATCH:
            return TrustDecision.deny(
                "stable_integrity_mismatch",
                "Cannot promote to stable: integrity mismatch detected.",
                trust_level=trust,
                integrity_status=integrity,
                risk_level=risk,
            )

        sig = provenance.signature_status
        if sig == SIGNATURE_INVALID:
            return TrustDecision.deny(
                "stable_signature_invalid",
                "Cannot promote to stable: provenance signature is invalid.",
                trust_level=trust,
                signature_status=sig,
                risk_level=risk,
            )

        # ── Trust level gating ─────────────────────────────────────────────
        if trust in (TRUST_UNTRUSTED, TRUST_UNKNOWN):
            return TrustDecision.deny(
                "stable_trust_insufficient",
                f"Cannot promote to stable: trust_level={trust} is insufficient. "
                "Minimum reviewed or trusted_local required.",
                trust_level=trust,
                risk_level=risk,
            )

        if trust == TRUST_REVIEWED:
            if risk == "high":
                return TrustDecision.deny(
                    "stable_reviewed_insufficient_high_risk",
                    "Cannot promote to stable: reviewed trust is insufficient "
                    "for high-risk capabilities. trusted_local or trusted_signed required.",
                    trust_level=trust,
                    risk_level=risk,
                )
            return TrustDecision.warn(
                "stable_reviewed_minimum",
                "Stable promotion with reviewed trust requires passing eval record.",
                trust_level=trust,
                risk_level=risk,
            )

        # trusted_local or trusted_signed
        return TrustDecision.allow(
            "stable_trust_sufficient",
            f"Stable promotion allowed with trust_level={trust}.",
            trust_level=trust,
            integrity_status=integrity,
            risk_level=risk,
        )
