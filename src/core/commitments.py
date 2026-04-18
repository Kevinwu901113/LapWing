"""CommitmentStore — durable record of Lapwing's outstanding promises.

Blueprint v2.0 Step 2 §3 / Step 5 §(commitments). A commitment is any
discrete promise the subject has made that has a definable fulfilment
trigger: "I'll check that and get back to you", "remind me in 20 min",
"let me think and tell you tomorrow". The reviewer loop (Step 5) extracts
these from each iteration's TrajectoryStore output; until Step 5 lands,
this store is allocated but unwritten — ``list_open`` returns ``[]`` and
StateSerializer's "outstanding commitments" region renders empty, which
is the expected Step 2 behaviour.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
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

logger = logging.getLogger("lapwing.core.commitments")


class CommitmentStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FULFILLED = "fulfilled"
    ABANDONED = "abandoned"


_OPEN_STATUSES: frozenset[str] = frozenset(
    {CommitmentStatus.PENDING.value, CommitmentStatus.IN_PROGRESS.value}
)
_VALID_STATUSES: frozenset[str] = frozenset(s.value for s in CommitmentStatus)


@dataclass(frozen=True)
class Commitment:
    id: str
    created_at: float
    target_chat_id: str
    content: str
    source_trajectory_entry_id: int
    status: str
    status_changed_at: float
    fulfilled_by_entry_ids: list[int] | None
    reasoning: str | None


class CommitmentStore:
    """Durable store of open and historical commitments.

    Lives in the shared ``data/lapwing.db``. Step 2 allocates the table and
    exposes the interface; Reviewer-driven writes arrive in Step 5.
    """

    def __init__(
        self,
        db_path: str | Path,
        mutation_log: StateMutationLog,
    ) -> None:
        self._db_path = Path(db_path)
        self._mutation_log = mutation_log
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS commitments (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                target_chat_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source_trajectory_entry_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                status_changed_at REAL NOT NULL,
                fulfilled_by_entry_ids TEXT,
                reasoning TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_commit_status
                ON commitments(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_commit_chat
                ON commitments(target_chat_id, status);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Write ───────────────────────────────────────────────────────────

    async def create(
        self,
        target_chat_id: str,
        content: str,
        source_trajectory_entry_id: int,
        *,
        reasoning: str | None = None,
    ) -> str:
        """Create a new pending commitment; returns its id.

        Emits ``COMMITMENT_CREATED`` on mutation_log.
        """
        if self._db is None:
            raise RuntimeError("CommitmentStore not initialized; call init() first")

        commitment_id = uuid.uuid4().hex
        now = time.time()
        status = CommitmentStatus.PENDING.value

        await self._db.execute(
            """INSERT INTO commitments
               (id, created_at, target_chat_id, content,
                source_trajectory_entry_id, status, status_changed_at,
                fulfilled_by_entry_ids, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                commitment_id, now, target_chat_id, content,
                source_trajectory_entry_id, status, now, reasoning,
            ),
        )
        await self._db.commit()

        try:
            await self._mutation_log.record(
                MutationType.COMMITMENT_CREATED,
                {
                    "commitment_id": commitment_id,
                    "target_chat_id": target_chat_id,
                    "content": content,
                    "source_trajectory_entry_id": source_trajectory_entry_id,
                    "reasoning": reasoning,
                },
                iteration_id=current_iteration_id(),
                chat_id=target_chat_id,
            )
        except Exception:
            logger.warning(
                "commitment %s mutation_log mirror failed",
                commitment_id, exc_info=True,
            )

        return commitment_id

    async def set_status(
        self,
        commitment_id: str,
        status: str,
        *,
        fulfilled_by_entry_ids: list[int] | None = None,
    ) -> None:
        """Transition status. Emits ``COMMITMENT_STATUS_CHANGED``."""
        if self._db is None:
            raise RuntimeError("CommitmentStore not initialized; call init() first")
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"
            )

        existing = await self.get(commitment_id)
        if existing is None:
            raise KeyError(f"commitment {commitment_id!r} not found")

        now = time.time()
        entries_json = (
            json.dumps(fulfilled_by_entry_ids) if fulfilled_by_entry_ids else None
        )
        await self._db.execute(
            """UPDATE commitments
               SET status = ?, status_changed_at = ?, fulfilled_by_entry_ids = ?
               WHERE id = ?""",
            (status, now, entries_json, commitment_id),
        )
        await self._db.commit()

        try:
            await self._mutation_log.record(
                MutationType.COMMITMENT_STATUS_CHANGED,
                {
                    "commitment_id": commitment_id,
                    "old_status": existing.status,
                    "new_status": status,
                    "fulfilled_by_entry_ids": fulfilled_by_entry_ids,
                },
                iteration_id=current_iteration_id(),
                chat_id=existing.target_chat_id,
            )
        except Exception:
            logger.warning(
                "commitment %s status-change mirror failed",
                commitment_id, exc_info=True,
            )

    # ── Read ────────────────────────────────────────────────────────────

    async def get(self, commitment_id: str) -> Commitment | None:
        if self._db is None:
            return None
        async with self._db.execute(
            "SELECT id, created_at, target_chat_id, content, "
            "source_trajectory_entry_id, status, status_changed_at, "
            "fulfilled_by_entry_ids, reasoning "
            "FROM commitments WHERE id = ?",
            (commitment_id,),
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_commitment(row) if row else None

    async def list_open(
        self, chat_id: str | None = None
    ) -> list[Commitment]:
        """Return commitments with status pending or in_progress.

        ``chat_id=None`` returns across all chats. Oldest→newest by ``created_at``.
        """
        if self._db is None:
            return []
        placeholders = ",".join("?" * len(_OPEN_STATUSES))
        params: tuple[Any, ...] = tuple(_OPEN_STATUSES)
        sql = (
            "SELECT id, created_at, target_chat_id, content, "
            "source_trajectory_entry_id, status, status_changed_at, "
            "fulfilled_by_entry_ids, reasoning "
            f"FROM commitments WHERE status IN ({placeholders})"
        )
        if chat_id is not None:
            sql += " AND target_chat_id = ?"
            params = params + (chat_id,)
        sql += " ORDER BY created_at ASC, id ASC"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_commitment(row) for row in rows]

    @staticmethod
    def _row_to_commitment(row: tuple) -> Commitment:
        fulfilled = json.loads(row[7]) if row[7] else None
        return Commitment(
            id=row[0],
            created_at=row[1],
            target_chat_id=row[2],
            content=row[3],
            source_trajectory_entry_id=row[4],
            status=row[5],
            status_changed_at=row[6],
            fulfilled_by_entry_ids=fulfilled,
            reasoning=row[8],
        )
