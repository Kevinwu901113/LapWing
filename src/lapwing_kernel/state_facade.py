"""State + fact facades for the <10 main-surface tool set.

Replaces the granular tool inventory (set_reminder / view_reminders /
commit_promise / close_focus / add_correction / list_notes / ...) with
three dispatcher tools:

  read_state(scope, query=None)   — read current self-state
  update_state(scope, op, value)  — mutate self-state
  read_fact(scope, query)         — read append-only fact sources

The legacy tools STAY DEFINED — they remain in INNER_TICK_PROFILE /
LOCAL_EXECUTION_PROFILE so background and operator paths keep working.
Only STANDARD_PROFILE (the cognitive surface) switches to these façades.

v1 scope (blueprint §11.4): scopes wired below cover the high-traffic
paths. Unwired scopes return a clear "not_yet_routed" payload so
unexpected calls fail predictably rather than silently doing the wrong
thing.

See docs/architecture/lapwing_v1_blueprint.md §11.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Known scope namespaces (string, not enum, to allow extension without
# schema change — same convention as Action.resource / Event.type).
STATE_SCOPES = frozenset(
    {
        "reminder",
        "promise",
        "focus",
        "correction",
        "note",
        "datetime",
        "identity",
        "agents",
        "capability",  # read-only via read_state(scope='capability')
    }
)

FACT_SCOPES = frozenset(
    {
        "wiki",
        "eventlog",
        "trajectory",
    }
)


def _not_routed(scope: str, verb: str) -> dict[str, Any]:
    return {
        "status": "not_yet_routed",
        "scope": scope,
        "verb": verb,
        "hint": (
            f"scope {scope!r}/{verb!r} not yet wired in state_facade. "
            f"Use the legacy tool directly via INNER_TICK_PROFILE or wait "
            f"for the dispatcher implementation."
        ),
    }


async def read_state(
    *,
    scope: str,
    query: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read current self-state.

    Scopes:
      datetime   — current wall time + timezone (always wired)
      reminder   — active reminders list (DurableScheduler)
      focus      — current focus topic (FocusManager)
      note       — recent notes (NoteStore)
      identity   — non-sensitive identity facts (ResidentIdentity)
      agents     — registered agent kinds (AgentCatalog)
      promise    — active promises (CommitmentStore)
      correction — recent corrections received
      capability — list/search/view capabilities (read-only)
    """
    services = services or {}
    query = query or {}

    if scope == "datetime":
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return {
            "status": "ok",
            "scope": "datetime",
            "value": {
                "iso": now.isoformat(),
                "unix": now.timestamp(),
                "tz": "UTC",
            },
        }

    if scope == "reminder":
        sched = services.get("durable_scheduler")
        if sched is None:
            return _not_routed(scope, "read")
        try:
            reminders = await sched.list_reminders()  # type: ignore[attr-defined]
        except Exception as exc:
            return {"status": "error", "scope": scope, "error": str(exc)[:200]}
        return {
            "status": "ok",
            "scope": "reminder",
            "value": {"reminders": reminders},
        }

    if scope == "focus":
        focus_mgr = services.get("focus_manager")
        if focus_mgr is None:
            return _not_routed(scope, "read")
        try:
            current = focus_mgr.current()  # type: ignore[attr-defined]
        except Exception as exc:
            return {"status": "error", "scope": scope, "error": str(exc)[:200]}
        return {"status": "ok", "scope": "focus", "value": {"current": current}}

    if scope == "agents":
        catalog = services.get("agent_catalog")
        if catalog is None:
            return _not_routed(scope, "read")
        try:
            agents = await catalog.list()  # type: ignore[attr-defined]
        except Exception as exc:
            return {"status": "error", "scope": scope, "error": str(exc)[:200]}
        return {"status": "ok", "scope": "agents", "value": {"agents": agents}}

    if scope == "identity":
        identity = services.get("identity")
        if identity is None:
            return _not_routed(scope, "read")
        # Return only the non-sensitive subset
        return {
            "status": "ok",
            "scope": "identity",
            "value": {
                "agent_name": getattr(identity, "agent_name", None),
                "owner_name": getattr(identity, "owner_name", None),
                "home_server_name": getattr(identity, "home_server_name", None),
            },
        }

    if scope in STATE_SCOPES:
        # Recognized scope, just not yet wired
        return _not_routed(scope, "read")

    return {
        "status": "error",
        "scope": scope,
        "error": f"unknown_scope:{scope}",
    }


async def update_state(
    *,
    scope: str,
    op: str,
    value: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mutate self-state.

    Common ops: add / remove / set / clear / commit / fulfill / abandon /
                close / cancel
    """
    services = services or {}
    value = value or {}

    if scope == "focus":
        focus_mgr = services.get("focus_manager")
        if focus_mgr is None:
            return _not_routed(scope, op)
        if op == "close":
            try:
                await focus_mgr.close(reason=value.get("reason", "lapwing_close"))  # type: ignore[attr-defined]
            except Exception as exc:
                return {"status": "error", "scope": scope, "error": str(exc)[:200]}
            return {"status": "ok", "scope": "focus", "op": "close"}

    if scope == "reminder":
        sched = services.get("durable_scheduler")
        if sched is None:
            return _not_routed(scope, op)
        if op == "add":
            try:
                reminder_id = await sched.add_reminder(**value)  # type: ignore[attr-defined]
            except Exception as exc:
                return {"status": "error", "scope": scope, "error": str(exc)[:200]}
            return {
                "status": "ok",
                "scope": "reminder",
                "op": "add",
                "id": reminder_id,
            }
        if op == "cancel":
            try:
                await sched.cancel_reminder(value.get("id"))  # type: ignore[attr-defined]
            except Exception as exc:
                return {"status": "error", "scope": scope, "error": str(exc)[:200]}
            return {"status": "ok", "scope": "reminder", "op": "cancel"}

    if scope == "correction":
        corr_mgr = services.get("correction_manager")
        if corr_mgr is None:
            return _not_routed(scope, op)
        if op == "add":
            try:
                await corr_mgr.add(value.get("text", ""))  # type: ignore[attr-defined]
            except Exception as exc:
                return {"status": "error", "scope": scope, "error": str(exc)[:200]}
            return {"status": "ok", "scope": "correction", "op": "add"}

    if scope in STATE_SCOPES:
        return _not_routed(scope, op)

    return {
        "status": "error",
        "scope": scope,
        "error": f"unknown_scope:{scope}",
    }


async def read_fact(
    *,
    scope: str,
    query: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read append-only fact sources.

    Scopes:
      eventlog   — kernel EventLog (blueprint §9)
      trajectory — cross-channel behavior timeline
      wiki       — read-only entity lookups
    """
    services = services or {}
    query = query or {}

    if scope == "eventlog":
        event_log = services.get("event_log")
        if event_log is None:
            return _not_routed(scope, "read")
        try:
            rows = event_log.query(
                type_prefix=query.get("type_prefix"),
                resource=query.get("resource"),
                actor=query.get("actor"),
                outcome=query.get("outcome"),
                limit=min(int(query.get("limit", 50)), 500),
            )
        except Exception as exc:
            return {"status": "error", "scope": scope, "error": str(exc)[:200]}
        return {
            "status": "ok",
            "scope": "eventlog",
            "value": {
                "events": [
                    {
                        "id": e.id,
                        "time": e.time.isoformat(),
                        "actor": e.actor,
                        "type": e.type,
                        "resource": e.resource,
                        "summary": e.summary,
                        "outcome": e.outcome,
                    }
                    for e in rows
                ],
            },
        }

    if scope == "trajectory":
        traj = services.get("trajectory_store")
        if traj is None:
            return _not_routed(scope, "read")
        try:
            limit = min(int(query.get("limit", 30)), 200)
            rows = await traj.recent(limit=limit)  # type: ignore[attr-defined]
        except Exception as exc:
            return {"status": "error", "scope": scope, "error": str(exc)[:200]}
        return {"status": "ok", "scope": "trajectory", "value": {"entries": rows}}

    if scope in FACT_SCOPES:
        return _not_routed(scope, "read")

    return {
        "status": "error",
        "scope": scope,
        "error": f"unknown_scope:{scope}",
    }
