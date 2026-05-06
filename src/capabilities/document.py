"""CapabilityDocument: parse and validate capability directories.

A capability directory contains:
  CAPABILITY.md  — YAML front matter + Markdown body
  manifest.json  — machine-written metadata (optional; merged with CAPABILITY.md)

The parser:
  1. Parses manifest.json (if present).
  2. Parses CAPABILITY.md with YAML front matter.
  3. Merges them deterministically (manifest.json takes precedence).
  4. Validates required metadata.
  5. Computes a stable content_hash.
  6. Never executes scripts or loads capabilities into runtime state.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.capabilities.errors import (
    InvalidDocumentError,
    InvalidEnumValueError,
    InvalidManifestError,
    MalformedFrontMatterError,
    MissingFieldError,
)
from src.capabilities.hashing import compute_content_hash
from src.capabilities.schema import (
    ALLOWED_MATURITIES,
    ALLOWED_ROLLBACK_MECHANISMS,
    ALLOWED_RISK_LEVELS,
    ALLOWED_SCOPES,
    ALLOWED_SENSITIVE_CONTEXTS,
    ALLOWED_SIDE_EFFECTS,
    ALLOWED_STATUSES,
    ALLOWED_TYPES,
    REQUIRED_METADATA_FIELDS,
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)

# Standard subdirectories in a capability directory (recognised, not executed).
STANDARD_DIRS: frozenset[str] = frozenset({
    "scripts", "tests", "examples", "evals", "traces", "versions",
})

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ── Public document type ────────────────────────────────────────────────


class CapabilityDocument:
    """A parsed and validated capability document.

    Holds the resolved manifest, the Markdown body, the source directory,
    and the stable content hash.
    """

    def __init__(
        self,
        manifest: CapabilityManifest,
        body: str,
        directory: Path,
    ) -> None:
        self.manifest = manifest
        self.body = body
        self.directory = directory
        self.standard_dirs: set[str] = set()

    @property
    def content_hash(self) -> str:
        return self.manifest.content_hash

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def type(self) -> CapabilityType:
        return self.manifest.type

    @property
    def scope(self) -> CapabilityScope:
        return self.manifest.scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.model_dump(),
            "body": self.body,
            "directory": str(self.directory),
            "standard_dirs": sorted(self.standard_dirs),
        }


# ── Parser ──────────────────────────────────────────────────────────────


class CapabilityParser:
    """Parse and validate a capability directory.

    Usage:
        parser = CapabilityParser()
        doc = parser.parse(Path("data/capabilities/workspace/my_cap/"))
    """

    # Known scope directories under data/capabilities/
    SCOPE_DIRS: frozenset[str] = frozenset({"global", "user", "workspace", "session", "archived"})

    def parse(self, directory: Path) -> CapabilityDocument:
        """Parse a single capability directory.

        Returns a CapabilityDocument on success. Raises CapabilityError
        subclasses on failure.
        """
        directory = directory.resolve()

        self._validate_directory(directory)

        # 1. Parse CAPABILITY.md
        cap_md_path = directory / "CAPABILITY.md"
        md_front_matter, body = self._parse_capability_md(cap_md_path)

        # 2. Parse manifest.json (optional)
        manifest_path = directory / "manifest.json"
        json_data: dict[str, Any] = {}
        if manifest_path.exists():
            json_data = self._parse_manifest_json(manifest_path)

        # 3. Merge: manifest.json takes precedence over CAPABILITY.md front matter
        merged = self._merge_metadata(md_front_matter, json_data, str(cap_md_path))

        # 4. Validate required metadata
        self._validate_required_fields(merged, str(cap_md_path))

        # 5. Validate enum values
        self._validate_enums(merged, str(cap_md_path))

        # 6. Build manifest
        manifest = self._build_manifest(merged)

        # 7. Compute stable content hash
        manifest_data = manifest.model_dump(exclude={"content_hash"})
        manifest.content_hash = compute_content_hash(manifest_data, body=body)

        # 8. Build document
        doc = CapabilityDocument(manifest=manifest, body=body, directory=directory)

        # 9. Scan standard directories
        doc.standard_dirs = self._scan_standard_dirs(directory)

        return doc

    # ── Internal helpers ────────────────────────────────────────────

    @staticmethod
    def _validate_directory(directory: Path) -> None:
        if not directory.is_dir():
            raise InvalidDocumentError(f"Not a directory: {directory}")
        cap_md = directory / "CAPABILITY.md"
        if not cap_md.is_file():
            raise InvalidDocumentError(f"Missing CAPABILITY.md in {directory}")

    def _parse_capability_md(self, path: Path) -> tuple[dict[str, Any], str]:
        """Parse CAPABILITY.md, returning (front_matter_dict, markdown_body)."""
        raw = path.read_text(encoding="utf-8")
        m = _FRONT_MATTER_RE.match(raw)
        if not m:
            raise MalformedFrontMatterError("No YAML front matter found (expected '---' delimiters)")
        try:
            front_matter = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            raise MalformedFrontMatterError(str(e)) from e
        if not isinstance(front_matter, dict):
            raise MalformedFrontMatterError("Front matter must be a YAML mapping")
        body = raw[m.end():].strip()
        return dict(front_matter), body

    @staticmethod
    def _parse_manifest_json(path: Path) -> dict[str, Any]:
        """Parse manifest.json into a dict."""
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise InvalidManifestError(f"Invalid JSON in {path}: {e}") from e
        if not isinstance(data, dict):
            raise InvalidManifestError(f"manifest.json must be a JSON object: {path}")
        return dict(data)

    @staticmethod
    def _merge_metadata(
        front_matter: dict[str, Any],
        json_data: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        """Merge: manifest.json overrides CAPABILITY.md front matter.

        Unknown fields from either source are preserved in `extra`.
        """
        merged: dict[str, Any] = dict(front_matter)
        extra: dict[str, Any] = {}

        # Collect unknown fields from front matter into extra
        known = _KNOWN_FIELD_NAMES
        for k, v in front_matter.items():
            if k not in known:
                extra[k] = v

        # Override with manifest.json (higher priority)
        for k, v in json_data.items():
            merged[k] = v
            if k not in known:
                extra[k] = v
            elif k in extra:
                del extra[k]

        merged["extra"] = extra
        return merged

    @staticmethod
    def _validate_required_fields(data: dict[str, Any], source: str) -> None:
        for field in REQUIRED_METADATA_FIELDS:
            if field not in data or data[field] is None:
                raise MissingFieldError(field, source)

    @staticmethod
    def _validate_enums(data: dict[str, Any], source: str) -> None:
        _check_enum("type", data.get("type", ""), ALLOWED_TYPES, source)
        _check_enum("scope", data.get("scope", ""), ALLOWED_SCOPES, source)
        _check_enum("maturity", data.get("maturity", ""), ALLOWED_MATURITIES, source)
        _check_enum("status", data.get("status", ""), ALLOWED_STATUSES, source)
        _check_enum("risk_level", data.get("risk_level", ""), ALLOWED_RISK_LEVELS, source)
        _check_enum_list("sensitive_contexts", data.get("sensitive_contexts", []), ALLOWED_SENSITIVE_CONTEXTS, source)
        _check_enum_list("side_effects", data.get("side_effects", []), ALLOWED_SIDE_EFFECTS, source)
        if data.get("rollback_mechanism") is not None:
            _check_enum("rollback_mechanism", data.get("rollback_mechanism", ""), ALLOWED_ROLLBACK_MECHANISMS, source)

    @staticmethod
    def _build_manifest(data: dict[str, Any]) -> CapabilityManifest:
        now = datetime.now(timezone.utc)
        extra = data.pop("extra", {})
        return CapabilityManifest(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            type=data["type"],
            scope=data["scope"],
            version=data["version"],
            maturity=data["maturity"],
            status=data["status"],
            risk_level=data["risk_level"],
            trust_required=data.get("trust_required", "developer"),
            required_tools=data.get("required_tools", []),
            required_permissions=data.get("required_permissions", []),
            triggers=data.get("triggers", []),
            tags=data.get("tags", []),
            do_not_apply_when=data.get("do_not_apply_when", []),
            sensitive_contexts=data.get("sensitive_contexts", []),
            reuse_boundary=data.get("reuse_boundary"),
            required_preflight_checks=data.get("required_preflight_checks", []),
            side_effects=data.get("side_effects", []),
            rollback_available=data.get("rollback_available"),
            rollback_mechanism=data.get("rollback_mechanism"),
            created_at=now,
            updated_at=now,
            extra=extra,
        )

    @staticmethod
    def _scan_standard_dirs(directory: Path) -> set[str]:
        found: set[str] = set()
        for d in STANDARD_DIRS:
            p = directory / d
            if p.is_dir():
                found.add(d)
        return found


# ── Helpers ─────────────────────────────────────────────────────────────

# All known field names in the v1 schema (not including extra).
_KNOWN_FIELD_NAMES: frozenset[str] = frozenset({
    "id", "name", "description", "type", "scope", "version",
    "maturity", "status", "risk_level", "trust_required",
    "required_tools", "required_permissions", "triggers", "tags",
    "do_not_apply_when", "sensitive_contexts", "reuse_boundary",
    "required_preflight_checks", "side_effects", "rollback_available",
    "rollback_mechanism",
    "created_at", "updated_at", "content_hash",
})


def _check_enum(field: str, value: str, allowed: frozenset[str], source: str) -> None:
    if value not in allowed:
        raise InvalidEnumValueError(field, value, allowed, source)


def _check_enum_list(field: str, values: Any, allowed: frozenset[str], source: str) -> None:
    if values is None:
        return
    if not isinstance(values, list):
        raise InvalidEnumValueError(field, str(values), allowed, source)
    for value in values:
        if value not in allowed:
            raise InvalidEnumValueError(field, str(value), allowed, source)


# ── Convenience ─────────────────────────────────────────────────────────


def parse_capability(directory: Path) -> CapabilityDocument:
    """Parse a capability directory. Convenience wrapper."""
    return CapabilityParser().parse(directory)
