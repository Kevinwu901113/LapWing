"""Event primitive — append-only operational history.

See docs/architecture/lapwing_v1_blueprint.md §3.4.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    """Append-only operational history record.

    EventLog is NOT LLM memory:
    - not injected into prompt by default
    - sub-agents retrieve via explicit query interface
    - v1 does NOT auto-distill into Wiki / episodic memory
    """

    id: str
    time: datetime
    actor: str
    type: str
    resource: str | None
    summary: str
    outcome: str | None
    refs: dict[str, str] = field(default_factory=dict)
    data_redacted: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(
        actor: str,
        type: str,
        summary: str,
        *,
        resource: str | None = None,
        outcome: str | None = None,
        refs: dict[str, str] | None = None,
        data_redacted: dict[str, Any] | None = None,
    ) -> "Event":
        return Event(
            id=str(uuid.uuid4()),
            time=datetime.utcnow(),
            actor=actor,
            type=type,
            resource=resource,
            summary=summary,
            outcome=outcome,
            refs=refs or {},
            data_redacted=data_redacted or {},
        )
