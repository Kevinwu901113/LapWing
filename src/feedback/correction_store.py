"""SQLite-backed storage for behavior corrections."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from src.core.time_utils import now


@dataclass(frozen=True)
class CorrectionEntry:
    rule_key: str
    count: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_details: str
    threshold_fired_at: datetime | None


class CorrectionStore:
    """Persistent counter for user corrections."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS corrections (
        rule_key            TEXT PRIMARY KEY,
        count               INTEGER NOT NULL DEFAULT 0,
        first_seen_at       TEXT NOT NULL,
        last_seen_at        TEXT NOT NULL,
        last_details        TEXT,
        all_details         TEXT,
        threshold_fired_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_corrections_count
        ON corrections(count DESC);
    CREATE INDEX IF NOT EXISTS idx_corrections_last_seen
        ON corrections(last_seen_at DESC);
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._memory_conn: sqlite3.Connection | None = None
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:")
            return self._memory_conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(self.SCHEMA)
            conn.commit()
        finally:
            if self._db_path is not None:
                conn.close()

    def increment(self, rule_key: str, details: str) -> CorrectionEntry:
        ts = now().isoformat()
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT count, first_seen_at, threshold_fired_at, all_details
                   FROM corrections WHERE rule_key = ?""",
                (rule_key,),
            ).fetchone()

            if row is None:
                count = 1
                first_seen = ts
                fired_at = None
                all_details = details
                conn.execute(
                    """INSERT INTO corrections
                       (rule_key, count, first_seen_at, last_seen_at,
                        last_details, all_details)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (rule_key, count, ts, ts, details, all_details),
                )
            else:
                count = int(row[0]) + 1
                first_seen = str(row[1])
                fired_at = row[2]
                previous_details = str(row[3] or "")
                all_details = "; ".join(
                    part for part in (previous_details, details) if part
                )
                conn.execute(
                    """UPDATE corrections
                       SET count = ?, last_seen_at = ?,
                           last_details = ?, all_details = ?
                       WHERE rule_key = ?""",
                    (count, ts, details, all_details, rule_key),
                )
            conn.commit()
        finally:
            if self._db_path is not None:
                conn.close()

        return CorrectionEntry(
            rule_key=rule_key,
            count=count,
            first_seen_at=datetime.fromisoformat(first_seen),
            last_seen_at=datetime.fromisoformat(ts),
            last_details=details,
            threshold_fired_at=datetime.fromisoformat(fired_at) if fired_at else None,
        )

    def top(self, n: int = 5) -> list[CorrectionEntry]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT rule_key, count, first_seen_at, last_seen_at,
                          last_details, threshold_fired_at
                   FROM corrections
                   ORDER BY count DESC, last_seen_at DESC
                   LIMIT ?""",
                (n,),
            ).fetchall()
        finally:
            if self._db_path is not None:
                conn.close()
        return [self._row_to_entry(row) for row in rows]

    def all_details(self, rule_key: str) -> str:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT all_details FROM corrections WHERE rule_key = ?",
                (rule_key,),
            ).fetchone()
        finally:
            if self._db_path is not None:
                conn.close()
        return str(row[0] or "") if row else ""

    def should_fire_threshold(
        self,
        entry: CorrectionEntry,
        threshold: int,
        cooldown_hours: int = 24,
    ) -> bool:
        if entry.count < threshold:
            return False
        if entry.threshold_fired_at is None:
            return True
        return now() - entry.threshold_fired_at >= timedelta(hours=cooldown_hours)

    def mark_threshold_fired(self, rule_key: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE corrections SET threshold_fired_at = ? WHERE rule_key = ?",
                (now().isoformat(), rule_key),
            )
            conn.commit()
        finally:
            if self._db_path is not None:
                conn.close()

    def reset(self, rule_key: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM corrections WHERE rule_key = ?", (rule_key,))
            conn.commit()
        finally:
            if self._db_path is not None:
                conn.close()

    @staticmethod
    def _row_to_entry(row) -> CorrectionEntry:
        return CorrectionEntry(
            rule_key=row[0],
            count=int(row[1]),
            first_seen_at=datetime.fromisoformat(row[2]),
            last_seen_at=datetime.fromisoformat(row[3]),
            last_details=row[4] or "",
            threshold_fired_at=datetime.fromisoformat(row[5]) if row[5] else None,
        )
