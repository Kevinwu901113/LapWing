"""EventLog — append-only operational history.

HARD CONSTRAINTS (blueprint §9):
  - APPEND-ONLY. No UPDATE / DELETE paths in this module.
  - NOT LLM memory. Not injected into prompt by default.
  - Sub-agents retrieve via explicit query() interface only.
  - No auto-distillation into Wiki / episodic memory in v1.

See docs/architecture/lapwing_v1_blueprint.md §9.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..primitives.event import Event


SCHEMA_PATH = Path(__file__).parent / "event_log.sql"


def _row_to_event(row: tuple) -> Event:
    (
        id_,
        time_str,
        actor,
        type_,
        resource,
        summary,
        outcome,
        refs_json,
        data_redacted_json,
    ) = row
    return Event(
        id=id_,
        time=datetime.fromisoformat(time_str),
        actor=actor,
        type=type_,
        resource=resource,
        summary=summary,
        outcome=outcome,
        refs=json.loads(refs_json) if refs_json else {},
        data_redacted=json.loads(data_redacted_json) if data_redacted_json else {},
    )


class EventLog:
    """Sync sqlite-backed append-only event log.

    No UPDATE / DELETE methods exposed (or even private) on this class — see
    blueprint §9.3. Retention is v1 a no-op (§9.4): infinite append, monitoring
    only. If future needs require pruning, do it via an external script not
    integrated here.
    """

    def __init__(self, db_path: Path | str):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.executescript(SCHEMA_PATH.read_text())

    def append(self, event: Event) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO events
                (id, time, actor, type, resource, summary, outcome,
                 refs_json, data_redacted_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.time.isoformat(),
                    event.actor,
                    event.type,
                    event.resource,
                    event.summary,
                    event.outcome,
                    json.dumps(event.refs, ensure_ascii=False),
                    json.dumps(event.data_redacted, ensure_ascii=False),
                ),
            )

    def query(
        self,
        *,
        type_prefix: str | None = None,
        resource: str | None = None,
        actor: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Typed query interface. LLM-facing readers go through
        read_fact(scope="eventlog", query=...) which calls this.

        Filters are AND-ed. limit caps at most 1000 to keep responses bounded.
        """
        conds: list[str] = []
        args: list = []
        if type_prefix:
            conds.append("type LIKE ?")
            args.append(f"{type_prefix}%")
        if resource is not None:
            conds.append("resource = ?")
            args.append(resource)
        if actor:
            conds.append("actor = ?")
            args.append(actor)
        if outcome:
            conds.append("outcome = ?")
            args.append(outcome)
        if since:
            conds.append("time >= ?")
            args.append(since.isoformat())
        if until:
            conds.append("time < ?")
            args.append(until.isoformat())
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        limit = max(1, min(int(limit), 1000))
        sql = (
            "SELECT id, time, actor, type, resource, summary, outcome, "
            "refs_json, data_redacted_json FROM events"
            + where
            + " ORDER BY time DESC LIMIT ?"
        )
        args.append(limit)
        with sqlite3.connect(self._path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row_to_event(r) for r in rows]

    def count(self) -> int:
        with sqlite3.connect(self._path) as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
