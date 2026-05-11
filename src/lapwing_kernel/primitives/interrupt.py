"""Interrupt primitive — suspended execution requiring outside intervention.

See docs/architecture/lapwing_v1_blueprint.md §3.3.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


INTERRUPT_STATUS = frozenset(
    {
        "pending",
        "resolved",
        "denied",
        "expired",
        "cancelled",
    }
)


# Default expires_at per kind (Open Question O-2 answer).
DEFAULT_INTERRUPT_EXPIRY: dict[str, timedelta] = {
    "browser.captcha": timedelta(hours=24),
    "browser.login_required": timedelta(hours=24),
    "browser.auth_2fa": timedelta(hours=24),
    "browser.waf": timedelta(hours=24),
}


@dataclass(frozen=True)
class Interrupt:
    """Suspended execution awaiting outside intervention.

    Continuation-first: an Interrupt produced by an in-progress Action MUST
    carry a `continuation_ref` OR be explicitly `non_resumable=True`. Without
    one of those the Interrupt is purely notification and cannot drive resume.
    """

    id: str
    kind: str
    status: str
    actor_required: str
    resource: str
    resource_ref: str | None
    continuation_ref: str | None
    non_resumable: bool = False
    non_resumable_reason: str | None = None
    summary: str = ""
    payload_redacted: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    updated_at: datetime = field(default_factory=datetime.utcnow)
    resolved_payload: dict[str, Any] | None = None

    @staticmethod
    def new(
        kind: str,
        actor_required: str,
        resource: str,
        *,
        resource_ref: str | None = None,
        continuation_ref: str | None = None,
        non_resumable: bool = False,
        non_resumable_reason: str | None = None,
        summary: str = "",
        payload_redacted: dict[str, Any] | None = None,
        expires_in: timedelta | None = None,
    ) -> "Interrupt":
        # Hard rule: must have continuation_ref OR non_resumable=True
        if continuation_ref is None and not non_resumable:
            raise ValueError(
                "Interrupt must have continuation_ref OR non_resumable=True; "
                "see blueprint §3.3"
            )
        now = datetime.utcnow()
        return Interrupt(
            id=str(uuid.uuid4()),
            kind=kind,
            status="pending",
            actor_required=actor_required,
            resource=resource,
            resource_ref=resource_ref,
            continuation_ref=continuation_ref,
            non_resumable=non_resumable,
            non_resumable_reason=non_resumable_reason,
            summary=summary,
            payload_redacted=payload_redacted or {},
            created_at=now,
            expires_at=(now + expires_in) if expires_in else None,
            updated_at=now,
        )
