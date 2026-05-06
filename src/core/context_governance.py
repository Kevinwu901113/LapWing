"""Shared schemas for reversible long-term context proposals."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


RecordType = Literal[
    "memory",
    "summary",
    "research_result",
    "identity_fact",
    "relationship",
    "commitment",
    "preference",
    "project_fact",
    "curator_output",
]

Sensitivity = Literal["public", "personal", "sensitive", "secret"]
RecordStatus = Literal["draft", "proposal", "pending", "published", "rejected", "superseded", "expired"]
ApprovalState = Literal["not_required", "required", "pending", "approved", "rejected"]


def new_reversible_record_id(prefix: str = "ctx") -> str:
    return f"{prefix}:{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class ReversibleContextRecord:
    id: str
    record_type: RecordType
    content: str
    source_handles: tuple[str, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "lapwing"
    confidence: float = 0.0
    sensitivity: Sensitivity = "personal"
    status: RecordStatus = "proposal"
    why_this_matters: str = ""
    user_intent_evidence: str = ""
    emotional_or_relational_context: str | None = None
    decision_boundary: str = ""
    reversibility_handle: str = ""
    approval_state: ApprovalState = "pending"
    expires_at: datetime | None = None
    review_after: datetime | None = None
    published_at: datetime | None = None
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_safe_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_type": self.record_type,
            "source_handles": list(self.source_handles),
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "status": self.status,
            "why_this_matters": self.why_this_matters,
            "user_intent_evidence": self.user_intent_evidence,
            "decision_boundary": self.decision_boundary,
            "reversibility_handle": self.reversibility_handle,
            "approval_state": self.approval_state,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "review_after": self.review_after.isoformat() if self.review_after else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
        }
