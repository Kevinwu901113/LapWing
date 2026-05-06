"""Capability ID generation and validation.

IDs follow the pattern: {scope_prefix}_{short_uuid}
e.g. workspace_a1b2c3d4, global_e5f6g7h8, user_i9j0k1l2
"""

from __future__ import annotations

import re
import uuid

_SCOPE_PREFIX: dict[str, str] = {
    "global": "global",
    "user": "user",
    "workspace": "workspace",
    "session": "session",
}

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def generate_capability_id(scope: str) -> str:
    """Generate a unique capability id scoped to the given scope."""
    prefix = _SCOPE_PREFIX.get(scope, scope)
    short = uuid.uuid4().hex[:8]
    return f"{prefix}_{short}"


def is_valid_capability_id(cap_id: str) -> bool:
    """Check whether a string looks like a valid capability id."""
    return bool(_ID_PATTERN.match(cap_id))
