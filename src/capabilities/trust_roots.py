"""Local filesystem-backed TrustRootStore for Phase 8B-2.

Manages trust root metadata only. No crypto verification. No network.
No capability elevation to trusted_signed. No signature_status=verified.

Storage: <data_dir>/trust_roots/<trust_root_id>.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.capabilities.signature import (
    CapabilityTrustRoot,
    _validate_no_secrets,
)


def _validate_trust_root_id(trust_root_id: str) -> None:
    """Validate that a trust_root_id is path-safe.

    Rejects empty, path separators, traversal, and non-filesystem-safe chars.
    """
    if not trust_root_id or not trust_root_id.strip():
        raise ValueError("trust_root_id must be non-empty")
    if "/" in trust_root_id or "\\" in trust_root_id:
        raise ValueError(f"trust_root_id must not contain path separators: {trust_root_id!r}")
    if ".." in trust_root_id:
        raise ValueError(f"trust_root_id must not contain '..': {trust_root_id!r}")
    # Ensure the id resolves to itself as a filename (no traversal via other means)
    if Path(trust_root_id).name != trust_root_id:
        raise ValueError(f"trust_root_id is not a valid filename: {trust_root_id!r}")


class TrustRootStore:
    """Local filesystem-backed store for CapabilityTrustRoot metadata.

    No crypto. No network. No remote registry. No key storage.
    Trust roots are stored as JSON files in <data_dir>/trust_roots/.

    Disabled and revoked roots remain stored but are not active.
    """

    def __init__(self, data_dir: str | Path = "data/capabilities") -> None:
        self._data_dir = Path(data_dir)
        self._roots_dir = self._data_dir / "trust_roots"

    @property
    def roots_dir(self) -> Path:
        return self._roots_dir

    def _root_path(self, trust_root_id: str) -> Path:
        """Return the JSON file path for a trust root, with path-safety
        validation."""
        _validate_trust_root_id(trust_root_id)
        return self._roots_dir / f"{trust_root_id}.json"

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        """Write data to path atomically via a temp file + os.replace."""
        self._roots_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    # ── CRUD ─────────────────────────────────────────────────────────────

    def create_trust_root(self, trust_root: CapabilityTrustRoot) -> CapabilityTrustRoot:
        """Persist a new trust root. Raises ValueError on duplicate id or
        secret material."""
        _validate_trust_root_id(trust_root.trust_root_id)

        path = self._root_path(trust_root.trust_root_id)
        if path.exists():
            raise ValueError(
                f"Trust root {trust_root.trust_root_id!r} already exists; "
                f"use disable/revoke instead of recreating"
            )

        data = trust_root.to_dict()
        _validate_no_secrets(data)

        # Auto-populate created_at if empty
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc).isoformat()

        self._atomic_write(path, data)
        return CapabilityTrustRoot.from_dict(data)

    def get_trust_root(self, trust_root_id: str) -> CapabilityTrustRoot | None:
        """Read a single trust root by id. Returns None if missing or corrupt."""
        try:
            path = self._root_path(trust_root_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            _validate_no_secrets(data)
            return CapabilityTrustRoot.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return None

    def list_trust_roots(
        self,
        status: str | None = None,
        scope: str | None = None,
    ) -> list[CapabilityTrustRoot]:
        """List all stored trust roots, optionally filtered by status and/or
        scope. Corrupt files are silently skipped."""
        if not self._roots_dir.is_dir():
            return []

        results: list[CapabilityTrustRoot] = []
        for file_path in sorted(self._roots_dir.glob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                _validate_no_secrets(data)
                root = CapabilityTrustRoot.from_dict(data)
                if status is not None and root.status != status:
                    continue
                if scope is not None and root.scope != scope:
                    continue
                results.append(root)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                continue

        return results

    def disable_trust_root(
        self, trust_root_id: str, reason: str | None = None
    ) -> CapabilityTrustRoot | None:
        """Set a trust root's status to 'disabled'. Returns None if not found."""
        return self._update_status(trust_root_id, "disabled", reason)

    def revoke_trust_root(
        self, trust_root_id: str, reason: str | None = None
    ) -> CapabilityTrustRoot | None:
        """Set a trust root's status to 'revoked'. Returns None if not found."""
        return self._update_status(trust_root_id, "revoked", reason)

    def _update_status(
        self, trust_root_id: str, new_status: str, reason: str | None
    ) -> CapabilityTrustRoot | None:
        root = self.get_trust_root(trust_root_id)
        if root is None:
            return None
        root.status = new_status
        if reason:
            root.metadata = {**root.metadata, f"{new_status}_reason": reason}
        data = root.to_dict()
        _validate_no_secrets(data)
        self._atomic_write(self._root_path(trust_root_id), data)
        return root

    # ── Queries ───────────────────────────────────────────────────────────

    def is_trust_root_active(
        self, trust_root_id: str, at_time: datetime | None = None
    ) -> bool:
        """Return True if the trust root exists, is active, and is not expired."""
        root = self.get_trust_root(trust_root_id)
        if root is None:
            return False
        if root.status != "active":
            return False
        if root.expires_at:
            try:
                now = at_time or datetime.now(timezone.utc)
                expires = datetime.fromisoformat(root.expires_at)
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= now:
                    return False
            except (ValueError, TypeError):
                # Unparseable expiry — treat as expired
                return False
        return True

    def as_verifier_dict(self) -> dict[str, CapabilityTrustRoot]:
        """Return all stored trust roots as a dict for verify_signature_stub.

        Includes disabled, revoked, and expired roots so the verifier stub
        can return proper invalid decisions for them.
        """
        result: dict[str, CapabilityTrustRoot] = {}
        for root in self.list_trust_roots():
            result[root.trust_root_id] = root
        return result
