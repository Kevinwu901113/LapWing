"""Observation primitive — unified envelope for Action results.

See docs/architecture/lapwing_v1_blueprint.md §3.2.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


COMMON_STATUS = frozenset(
    {
        "ok",
        "blocked",
        "blocked_by_policy",
        "interrupted",
        "failed",
        "timeout",
        "network_error",
        "empty_content",
    }
)

BROWSER_EXTRA_STATUS = frozenset(
    {
        "waf_challenge",
        "captcha_required",
        "auth_required",
        "user_attention_required",
    }
)

CREDENTIAL_EXTRA_STATUS = frozenset(
    {
        "missing",
        "requires_owner",
    }
)


def validate_status(resource: str, status: str) -> bool:
    """Resource-aware status validation.

    Status is `str + check fn` rather than Enum so future resource types
    can extend without schema change.
    """
    if status in COMMON_STATUS:
        return True
    if resource == "browser" and status in BROWSER_EXTRA_STATUS:
        return True
    if resource == "credential" and status in CREDENTIAL_EXTRA_STATUS:
        return True
    return False


@dataclass(frozen=True)
class Observation:
    """Unified Action-execution envelope.

    All Resources share the same envelope. Browser / Credential / Shell do not
    invent independent Result types.

    `content` is LLM-facing. `artifacts` are NOT auto LLM-facing; each artifact
    type needs an explicit renderer before its contents can reach the model.
    """

    id: str
    action_id: str
    resource: str
    status: str
    summary: str | None = None
    content: str | None = None
    confidence: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    interrupt_id: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @staticmethod
    def ok(
        action_id: str,
        resource: str,
        *,
        summary: str | None = None,
        content: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> "Observation":
        return Observation(
            id=str(uuid.uuid4()),
            action_id=action_id,
            resource=resource,
            status="ok",
            summary=summary,
            content=content,
            confidence=confidence,
            provenance=provenance or {},
            artifacts=artifacts or [],
        )

    @staticmethod
    def interrupted(
        action_id: str,
        resource: str,
        *,
        interrupt_id: str,
        summary: str,
    ) -> "Observation":
        return Observation(
            id=str(uuid.uuid4()),
            action_id=action_id,
            resource=resource,
            status="interrupted",
            summary=summary,
            interrupt_id=interrupt_id,
        )

    @staticmethod
    def failure(
        action_id: str,
        resource: str,
        *,
        status: str,
        error: str,
        summary: str | None = None,
    ) -> "Observation":
        return Observation(
            id=str(uuid.uuid4()),
            action_id=action_id,
            resource=resource,
            status=status,
            error=error,
            summary=summary,
        )
