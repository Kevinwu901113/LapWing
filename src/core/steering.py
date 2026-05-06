"""Structured mid-task steering state.

Steering is not prompt mutation. It is recorded as runtime state, disclosed in
the dynamic StateView block at safe boundaries, then acknowledged or expired.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import aiosqlite

from src.logging.state_mutation_log import MutationType

logger = logging.getLogger("lapwing.core.steering")


@dataclass(frozen=True, slots=True)
class SteeringEvent:
    id: str
    task_id: str | None
    source_message_id: str
    content: str
    created_at: datetime
    expires_at: datetime | None
    acknowledged_at: datetime | None
    priority: Literal["low", "normal", "high"]
    reason: str | None = None
    source_channel: str | None = None
    source_trust_level: str | None = None
    chat_id: str | None = None

    @property
    def is_acknowledged(self) -> bool:
        return self.acknowledged_at is not None

    def is_expired(self, at: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (at or datetime.now(timezone.utc))


class SteeringStore:
    """SQLite-backed store for pending steering events."""

    def __init__(self, db_path: str | Path, mutation_log=None) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._mutation_log = mutation_log

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS steering_events (
                id TEXT PRIMARY KEY,
                chat_id TEXT,
                task_id TEXT,
                source_message_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                acknowledged_at TEXT,
                priority TEXT NOT NULL,
                reason TEXT,
                source_channel TEXT,
                source_trust_level TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS idx_steering_pending
                ON steering_events(chat_id, status, created_at);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def add(self, event: SteeringEvent) -> SteeringEvent:
        db = self._require_db()
        await db.execute(
            """
            INSERT OR REPLACE INTO steering_events
            (id, chat_id, task_id, source_message_id, content, created_at,
             expires_at, acknowledged_at, priority, reason, source_channel,
             source_trust_level, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.chat_id,
                event.task_id,
                event.source_message_id,
                event.content,
                _dt_to_iso(event.created_at),
                _dt_to_iso(event.expires_at),
                _dt_to_iso(event.acknowledged_at),
                event.priority,
                event.reason,
                event.source_channel,
                event.source_trust_level,
                "acknowledged" if event.acknowledged_at else "pending",
            ),
        )
        await db.commit()
        await self._record_mutation(
            MutationType.STEERING_RECEIVED,
            event,
            extra={"content_length": len(event.content)},
        )
        return event

    async def pending(
        self,
        *,
        chat_id: str | None = None,
        task_id: str | None = None,
        max_count: int = 5,
        now: datetime | None = None,
    ) -> tuple[SteeringEvent, ...]:
        await self.expire_stale(now=now)
        db = self._require_db()
        clauses = ["status = 'pending'", "acknowledged_at IS NULL"]
        params: list[object] = []
        if chat_id is not None:
            clauses.append("(chat_id = ? OR chat_id IS NULL)")
            params.append(chat_id)
        if task_id is not None:
            clauses.append("(task_id = ? OR task_id IS NULL)")
            params.append(task_id)
        params.append(max_count)
        cursor = await db.execute(
            f"""
            SELECT id, chat_id, task_id, source_message_id, content, created_at,
                   expires_at, acknowledged_at, priority, reason, source_channel,
                   source_trust_level
            FROM steering_events
            WHERE {' AND '.join(clauses)}
            ORDER BY
                CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                created_at ASC,
                id ASC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return tuple(_event_from_row(row) for row in rows)

    async def acknowledge(self, event_ids: tuple[str, ...] | list[str]) -> int:
        ids = tuple(dict.fromkeys(event_ids))
        if not ids:
            return 0
        db = self._require_db()
        at = datetime.now(timezone.utc)
        await db.executemany(
            """
            UPDATE steering_events
            SET acknowledged_at = ?, status = 'acknowledged'
            WHERE id = ? AND acknowledged_at IS NULL
            """,
            [(_dt_to_iso(at), event_id) for event_id in ids],
        )
        await db.commit()
        for event_id in ids:
            await self._record_mutation_by_id(MutationType.STEERING_ACKNOWLEDGED, event_id)
        return len(ids)

    async def expire_stale(self, *, now: datetime | None = None) -> int:
        db = self._require_db()
        at = now or datetime.now(timezone.utc)
        cursor = await db.execute(
            """
            SELECT id FROM steering_events
            WHERE status = 'pending'
              AND expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (_dt_to_iso(at),),
        )
        ids = [str(row[0]) for row in await cursor.fetchall()]
        if not ids:
            return 0
        await db.executemany(
            "UPDATE steering_events SET status = 'expired' WHERE id = ?",
            [(event_id,) for event_id in ids],
        )
        await db.commit()
        for event_id in ids:
            await self._record_mutation_by_id(MutationType.STEERING_EXPIRED, event_id)
        return len(ids)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SteeringStore is not initialized")
        return self._db

    async def _record_mutation(
        self,
        event_type: MutationType,
        event: SteeringEvent,
        *,
        extra: dict | None = None,
    ) -> None:
        if self._mutation_log is None:
            return
        payload = {
            "id": event.id,
            "chat_id": event.chat_id,
            "task_id": event.task_id,
            "source_message_id": event.source_message_id,
            "priority": event.priority,
            "reason": event.reason,
            "source_channel": event.source_channel,
            "source_trust_level": event.source_trust_level,
        }
        if extra:
            payload.update(extra)
        try:
            await self._mutation_log.record(event_type, payload, chat_id=event.chat_id)
        except Exception:
            logger.debug("steering mutation write failed", exc_info=True)

    async def _record_mutation_by_id(self, event_type: MutationType, event_id: str) -> None:
        if self._mutation_log is None:
            return
        try:
            await self._mutation_log.record(event_type, {"id": event_id})
        except Exception:
            logger.debug("steering mutation write failed", exc_info=True)


def _dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_from_row(row) -> SteeringEvent:
    return SteeringEvent(
        id=str(row[0]),
        chat_id=row[1],
        task_id=row[2],
        source_message_id=str(row[3]),
        content=str(row[4]),
        created_at=_parse_dt(row[5]) or datetime.now(timezone.utc),
        expires_at=_parse_dt(row[6]),
        acknowledged_at=_parse_dt(row[7]),
        priority=row[8] if row[8] in ("low", "normal", "high") else "normal",
        reason=row[9],
        source_channel=row[10],
        source_trust_level=row[11],
    )
