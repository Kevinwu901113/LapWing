"""Action primitive — intent to invoke a Resource.

See docs/architecture/lapwing_v1_blueprint.md §3.1.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Action:
    """Intent to call a Resource. Data, not function.

    All Actions must go through ActionExecutor.execute. Adapters that bypass
    the pipeline = protocol violation.

    resource_profile is part of routing identity (separate from args). The
    ResourceRegistry keys on (resource, resource_profile).
    """

    id: str
    resource: str
    resource_profile: str | None = None
    verb: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    actor: str = "lapwing"
    task_ref: str | None = None
    parent_action_id: str | None = None

    @staticmethod
    def new(
        resource: str,
        verb: str,
        *,
        resource_profile: str | None = None,
        args: dict[str, Any] | None = None,
        actor: str = "lapwing",
        task_ref: str | None = None,
        parent_action_id: str | None = None,
    ) -> "Action":
        return Action(
            id=str(uuid.uuid4()),
            resource=resource,
            resource_profile=resource_profile,
            verb=verb,
            args=args or {},
            actor=actor,
            task_ref=task_ref,
            parent_action_id=parent_action_id,
        )
