"""TrajectoryStore — main-subject behaviour timeline, cross-channel.

Blueprint v2.0 Step 2 §2. Replaces the per-chat_id ``conversations`` partition
with a single monotonic timeline of entries actor-on-world: what the user said,
what Lapwing thought, what tools she invoked, what state changed. One row per
observable behavioural moment; tool-call payload detail stays in mutation_log.

Append-only. Focus is a label layer over this single timeline: rows may carry
``focus_id`` but are never moved into physical partitions. Every ``append``
records a ``TRAJECTORY_APPENDED`` mutation — the single-truth invariant from
Blueprint v2.0 §1.3.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite

from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    current_iteration_id,
)

logger = logging.getLogger("lapwing.core.trajectory_store")


class TrajectoryEntryType(str, Enum):
    """Closed vocabulary of trajectory entries.

    ``TELL_USER`` / ``STAY_SILENT`` are defined but unused in Step 2 — their
    callers arrive in Step 5 / Step 4 respectively. ``ASSISTANT_TEXT`` is the
    transitional direct-output type and will be progressively replaced by
    ``TELL_USER`` once the tool-based output path lands in Step 5.
    """

    USER_MESSAGE = "user_message"
    TELL_USER = "tell_user"                # Step 5+
    ASSISTANT_TEXT = "assistant_text"      # Step 2–4 transitional
    INNER_THOUGHT = "inner_thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATE_CHANGE = "state_change"
    STAY_SILENT = "stay_silent"            # Step 4+
    INTERRUPTED = "interrupted"            # Step 4 M4 — partial output saved on OWNER preempt


_VALID_ACTORS = frozenset({"user", "lapwing", "system"})


@dataclass(frozen=True)
class TrajectoryEntry:
    """One row from the ``trajectory`` table."""

    id: int
    timestamp: float
    entry_type: str
    source_chat_id: str
    actor: str
    content: dict[str, Any]
    related_commitment_id: str | None
    related_iteration_id: str | None
    related_tool_call_id: str | None
    focus_id: str | None = None


class TrajectoryStore:
    """Append-only cross-channel behaviour timeline.

    Lives in ``data/lapwing.db`` alongside Commitments (Step 2) and the legacy
    ``conversations`` facade tables, enabling single-transaction joins.
    """

    def __init__(
        self,
        db_path: str | Path,
        mutation_log: StateMutationLog,
    ) -> None:
        self._db_path = Path(db_path)
        self._mutation_log = mutation_log
        self._db: aiosqlite.Connection | None = None
        self._on_append_listeners: list = []

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS trajectory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                entry_type TEXT NOT NULL,
                source_chat_id TEXT,
                actor TEXT NOT NULL,
                content_json TEXT NOT NULL,
                related_commitment_id TEXT,
                related_iteration_id TEXT,
                related_tool_call_id TEXT,
                focus_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_traj_timestamp
                ON trajectory(timestamp);
            CREATE INDEX IF NOT EXISTS idx_traj_chat
                ON trajectory(source_chat_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_traj_type
                ON trajectory(entry_type, timestamp);
            CREATE INDEX IF NOT EXISTS idx_traj_iteration
                ON trajectory(related_iteration_id, timestamp);
            """
        )
        # Step 4 M3: in-place migration for databases created with the
        # original NOT NULL schema. Idempotent — only runs when the
        # constraint is still present.
        await self._migrate_source_chat_id_nullable()
        await self._add_column_if_missing("focus_id", "TEXT")
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_trajectory_focus "
            "ON trajectory(focus_id, timestamp)"
        )
        await self._db.commit()

    async def _add_column_if_missing(self, column: str, sql_type: str) -> None:
        if self._db is None:
            return
        async with self._db.execute("PRAGMA table_info(trajectory)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if column not in cols:
            await self._db.execute(
                f"ALTER TABLE trajectory ADD COLUMN {column} {sql_type}"
            )

    async def _migrate_source_chat_id_nullable(self) -> None:
        """Drop ``NOT NULL`` on ``source_chat_id`` for legacy DBs.

        Pre-Step-4 schema required ``source_chat_id NOT NULL``; inner
        thoughts then had to use the ``"__inner__"`` sentinel string.
        Step 4 M3 retires that sentinel by allowing NULL — inner thoughts
        identify themselves via ``entry_type = 'inner_thought'`` instead.
        Existing rows are preserved; new inner writes go in as NULL.
        """
        if self._db is None:
            return
        cur = await self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='trajectory'"
        )
        row = await cur.fetchone()
        if not row or "source_chat_id TEXT NOT NULL" not in (row[0] or ""):
            return
        await self._db.executescript(
            """
            BEGIN;
            CREATE TABLE trajectory_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                entry_type TEXT NOT NULL,
                source_chat_id TEXT,
                actor TEXT NOT NULL,
                content_json TEXT NOT NULL,
                related_commitment_id TEXT,
                related_iteration_id TEXT,
                related_tool_call_id TEXT
            );
            INSERT INTO trajectory_new
                (id, timestamp, entry_type, source_chat_id, actor,
                 content_json, related_commitment_id, related_iteration_id,
                 related_tool_call_id)
                SELECT id, timestamp, entry_type, source_chat_id, actor,
                       content_json, related_commitment_id, related_iteration_id,
                       related_tool_call_id
                FROM trajectory;
            DROP TABLE trajectory;
            ALTER TABLE trajectory_new RENAME TO trajectory;
            CREATE INDEX IF NOT EXISTS idx_traj_timestamp
                ON trajectory(timestamp);
            CREATE INDEX IF NOT EXISTS idx_traj_chat
                ON trajectory(source_chat_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_traj_type
                ON trajectory(entry_type, timestamp);
            CREATE INDEX IF NOT EXISTS idx_traj_iteration
                ON trajectory(related_iteration_id, timestamp);
            COMMIT;
            """
        )
        logger.info(
            "trajectory schema migrated — source_chat_id is now nullable "
            "(Step 4 M3 inner-thought writes use NULL instead of '__inner__')"
        )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def add_on_append_listener(self, listener) -> None:
        """Register a callback invoked after a successful append.

        `listener` may be sync or async and receives the new TrajectoryEntry.
        Exceptions are logged, never raised — this is a fanout channel, not
        a transaction participant.
        """
        self._on_append_listeners.append(listener)

    # ── Write ───────────────────────────────────────────────────────────

    async def append(
        self,
        entry_type: TrajectoryEntryType,
        source_chat_id: str | None,
        actor: str,
        content: dict[str, Any],
        *,
        related_commitment_id: str | None = None,
        related_iteration_id: str | None = None,
        related_tool_call_id: str | None = None,
        focus_id: str | None = None,
        timestamp: float | None = None,
    ) -> int:
        """Append one entry; return its autoincrement id.

        Emits ``TRAJECTORY_APPENDED`` on ``mutation_log`` in the same call. If
        ``related_iteration_id`` is omitted, it's picked up from the current
        ``iteration_context`` contextvar (Step 1 §2.2 convention).
        """
        if not isinstance(entry_type, TrajectoryEntryType):
            raise TypeError(
                "entry_type must be TrajectoryEntryType, got "
                f"{type(entry_type).__name__}: {entry_type!r}"
            )
        if actor not in _VALID_ACTORS:
            raise ValueError(
                f"actor must be one of {sorted(_VALID_ACTORS)}, got {actor!r}"
            )
        if not isinstance(content, dict):
            raise TypeError(
                f"content must be dict, got {type(content).__name__}"
            )
        if self._db is None:
            raise RuntimeError("TrajectoryStore not initialized; call init() first")

        if related_iteration_id is None:
            related_iteration_id = current_iteration_id()

        ts = timestamp if timestamp is not None else time.time()
        content_json = json.dumps(content, ensure_ascii=False, default=str)

        cursor = await self._db.execute(
            """INSERT INTO trajectory
               (timestamp, entry_type, source_chat_id, actor, content_json,
                related_commitment_id, related_iteration_id, related_tool_call_id,
                focus_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts,
                entry_type.value,
                source_chat_id,
                actor,
                content_json,
                related_commitment_id,
                related_iteration_id,
                related_tool_call_id,
                focus_id,
            ),
        )
        await self._db.commit()
        entry_id = cursor.lastrowid or -1

        try:
            await self._mutation_log.record(
                MutationType.TRAJECTORY_APPENDED,
                {
                    "trajectory_id": entry_id,
                    "entry_type": entry_type.value,
                    "source_chat_id": source_chat_id,
                    "actor": actor,
                    "related_commitment_id": related_commitment_id,
                    "related_tool_call_id": related_tool_call_id,
                    "focus_id": focus_id,
                },
                iteration_id=related_iteration_id,
                chat_id=source_chat_id,
            )
        except Exception:
            logger.warning(
                "trajectory %d mutation_log mirror failed", entry_id, exc_info=True
            )

        if self._on_append_listeners:
            entry = TrajectoryEntry(
                id=entry_id,
                timestamp=ts,
                entry_type=entry_type.value,
                source_chat_id=source_chat_id,
                actor=actor,
                content=content,
                related_commitment_id=related_commitment_id,
                related_iteration_id=related_iteration_id,
                related_tool_call_id=related_tool_call_id,
                focus_id=focus_id,
            )
            import asyncio as _asyncio
            for listener in list(self._on_append_listeners):
                try:
                    result = listener(entry)
                    if _asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.warning(
                        "trajectory on-append listener failed", exc_info=True
                    )

        return entry_id

    # ── Read ────────────────────────────────────────────────────────────

    async def recent(self, n: int) -> list[TrajectoryEntry]:
        """Most recent N entries across all source_chat_ids, oldest→newest."""
        rows = await self._fetch(
            "1 = 1",
            (),
            order="timestamp DESC, id DESC",
            limit=n,
        )
        return list(reversed(rows))

    async def relevant_to_chat(
        self,
        chat_id: str,
        n: int,
        *,
        include_inner: bool = True,
    ) -> list[TrajectoryEntry]:
        """Most recent N entries related to ``chat_id``, oldest→newest.

        ``include_inner=True`` mixes in inner-thought entries identified
        by ``entry_type = 'inner_thought'``. This matches both legacy
        rows (where ``source_chat_id`` was the literal ``'__inner__'``)
        and Step-4 rows (where ``source_chat_id IS NULL``).
        """
        if include_inner:
            where = "(source_chat_id = ? OR entry_type = ?)"
            params: tuple[Any, ...] = (chat_id, TrajectoryEntryType.INNER_THOUGHT.value)
        else:
            where = "source_chat_id = ?"
            params = (chat_id,)
        rows = await self._fetch(
            where,
            params,
            order="timestamp DESC, id DESC",
            limit=n,
        )
        return list(reversed(rows))

    async def in_iteration(self, iteration_id: str) -> list[TrajectoryEntry]:
        """All entries produced within a single iteration, oldest→newest.

        Used by Commitment Reviewer (Step 5) to scan an iteration's outputs
        for unfulfilled promises.
        """
        return await self._fetch(
            "related_iteration_id = ?",
            (iteration_id,),
            order="timestamp ASC, id ASC",
        )

    async def entries_by_focus(
        self,
        focus_id: str,
        n: int,
    ) -> list[TrajectoryEntry]:
        """Most recent N entries carrying ``focus_id``, oldest→newest."""
        if not focus_id:
            return []
        rows = await self._fetch(
            "focus_id = ?",
            (focus_id,),
            order="timestamp DESC, id DESC",
            limit=n,
        )
        return list(reversed(rows))

    async def in_window(
        self,
        start_ts: float,
        end_ts: float,
        *,
        limit: int = 1000,
    ) -> list[TrajectoryEntry]:
        """Entries within [start_ts, end_ts). Oldest→newest. For audits."""
        return await self._fetch(
            "timestamp >= ? AND timestamp < ?",
            (start_ts, end_ts),
            order="timestamp ASC, id ASC",
            limit=limit,
        )

    async def list_for_timeline(
        self,
        *,
        before_ts: float | None = None,
        limit: int = 50,
        entry_types: list[TrajectoryEntryType] | None = None,
        source_chat_id: str | None = None,
    ) -> list[TrajectoryEntry]:
        """Page entries for timeline views. Returns newest→oldest (DESC).

        `before_ts` is a strict upper bound (`<`), so passing the last row's
        timestamp as the next page's cursor will not duplicate it.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if before_ts is not None:
            clauses.append("timestamp < ?")
            params.append(before_ts)

        if entry_types:
            placeholders = ",".join("?" for _ in entry_types)
            clauses.append(f"entry_type IN ({placeholders})")
            params.extend(t.value for t in entry_types)

        if source_chat_id is not None:
            clauses.append("source_chat_id = ?")
            params.append(source_chat_id)

        where = " AND ".join(clauses) if clauses else "1 = 1"
        return await self._fetch(
            where,
            tuple(params),
            order="timestamp DESC, id DESC",
            limit=limit,
        )

    async def _fetch(
        self,
        where: str,
        params: tuple[Any, ...],
        *,
        order: str,
        limit: int | None = None,
    ) -> list[TrajectoryEntry]:
        if self._db is None:
            return []
        sql = (
            "SELECT id, timestamp, entry_type, source_chat_id, actor, content_json, "
            "related_commitment_id, related_iteration_id, related_tool_call_id, "
            "focus_id "
            f"FROM trajectory WHERE {where} ORDER BY {order}"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: tuple) -> TrajectoryEntry:
        return TrajectoryEntry(
            id=row[0],
            timestamp=row[1],
            entry_type=row[2],
            source_chat_id=row[3],
            actor=row[4],
            content=json.loads(row[5]),
            related_commitment_id=row[6],
            related_iteration_id=row[7],
            related_tool_call_id=row[8],
            focus_id=row[9] if len(row) > 9 else None,
        )


# ── Legacy-dict projection ──────────────────────────────────────────
#
# Step 3 moved prompt assembly to StateSerializer, but two call paths
# still need the pre-serializer ``[{"role", "content"}]`` shape:
#
#   - brain._load_history: hands the list to _prepare_think, which
#     applies trust tagging in place and forwards as the builder's
#     trajectory_turns_override.
# A future step can retire this helper once callers move to the
# TrajectoryTurn shape. The projection itself is the same mapping
# Step 2g introduced in a short-lived transitional module; this is
# the permanent home.

_LEGACY_ROLE_MAP: dict[str, str] = {
    TrajectoryEntryType.USER_MESSAGE.value: "user",
    TrajectoryEntryType.TELL_USER.value: "assistant",
    TrajectoryEntryType.ASSISTANT_TEXT.value: "assistant",
}


def trajectory_entries_to_messages(
    entries: list[TrajectoryEntry] | tuple[TrajectoryEntry, ...],
    *,
    include_inner: bool = False,
) -> list[dict]:
    """Project trajectory rows onto the legacy conversation-message shape.

    ``USER_MESSAGE`` / ``TELL_USER`` / ``ASSISTANT_TEXT`` map to
    ``user`` / ``assistant``; ``INNER_THOUGHT`` surfaces as a
    ``[内部思考]``-prefixed system note when ``include_inner=True``; all
    other types drop. Preserves input iteration order.
    """
    out: list[dict] = []
    for entry in entries:
        role = _LEGACY_ROLE_MAP.get(entry.entry_type)
        if role is not None:
            text = _extract_legacy_text(entry)
            if text is None:
                continue
            out.append({"role": role, "content": text})
            continue

        if entry.entry_type == TrajectoryEntryType.INNER_THOUGHT.value:
            if not include_inner:
                continue
            text = _extract_legacy_text(entry)
            if text is None:
                continue
            out.append({"role": "system", "content": f"[内部思考] {text}"})
            continue
    return out


def _extract_legacy_text(entry: TrajectoryEntry) -> str | None:
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        msgs = content.get("messages")
        if isinstance(msgs, list) and msgs:
            return "\n".join(str(m) for m in msgs)
        text = content.get("text")
        if isinstance(text, str):
            return text
        return None
    text = content.get("text")
    if isinstance(text, str):
        return text
    return None
