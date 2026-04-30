"""Stable content hashing for capability documents.

The hash is computed over the meaningful fields of the manifest, explicitly
excluding fields that would create self-referential churn (content_hash itself,
created_at, updated_at).

Hash inputs are sorted and serialised deterministically so the same logical
document always produces the same hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.capabilities.schema import COMPUTED_FIELDS


def _normalise(value: Any) -> str:
    """Convert a value to a deterministic string for hashing."""
    if isinstance(value, dict):
        items = sorted(
            (k, _normalise(v)) for k, v in value.items()
            if k not in COMPUTED_FIELDS
        )
        return "{" + ",".join(f"{json.dumps(k)}:{v}" for k, v in items) + "}"
    if isinstance(value, (list, tuple, set, frozenset)):
        return "[" + ",".join(_normalise(v) for v in sorted(value, key=str)) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if value is None:
        return "null"
    return json.dumps(str(value))


def compute_content_hash(manifest_data: dict[str, Any], *, body: str = "") -> str:
    """Compute a stable SHA256 hash over the manifest + body content.

    Fields listed in COMPUTED_FIELDS are stripped before hashing so the
    hash does not depend on itself, creation time, or update time.
    """
    # Filter out computed fields
    filtered = {k: v for k, v in manifest_data.items() if k not in COMPUTED_FIELDS}

    normalised = _normalise(filtered)
    if body:
        normalised += "||" + _normalise(body)

    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
