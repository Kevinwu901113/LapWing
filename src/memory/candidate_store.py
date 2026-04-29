"""CandidateStore — persistence for pending memory candidates.

Phase 2 §2.4. The fast gate runs synchronously; compilation is
asynchronous and may happen minutes later (trajectory close, scheduled
maintenance). Candidates need to survive process restarts, so we
persist them in ``lapwing.db``.

State machine:

    pending → compiling → compiled
                       ↘ failed
            ↘ skipped         (manual)

``mark_compiling`` is the concurrency interlock: a worker claims a
batch by transitioning ``pending → compiling`` atomically; later
calls only return rows still in ``pending``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel

from src.memory.candidate import MemoryCandidate
from src.memory.quality_gate import MemoryGateDecision

logger = logging.getLogger("lapwing.memory.candidate_store")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PendingRecord(BaseModel):
    id: str
    source_ids: list[str]
    source_hash: str
    status: str
    gate_score: float | None = None
    rough_category: str | None = None
    candidate_json: str | None = None
    created_at: str
    updated_at: str
    last_error: str | None = None


class CandidateStore:
    """Pending-candidate queue. Lives in ``lapwing.db``."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_candidates (
                id              TEXT PRIMARY KEY,
                source_ids      TEXT NOT NULL,
                source_hash     TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                gate_score      REAL,
                rough_category  TEXT,
                candidate_json  TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                last_error      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_candidates_status
                ON memory_candidates(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_candidates_source_hash
                ON memory_candidates(source_hash);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Write API ───────────────────────────────────────────────────

    async def enqueue(
        self,
        gate_decision: MemoryGateDecision,
        source_ids: list[str],
        source_hash: str,
    ) -> str:
        """Record a fast-gate ``accept`` as a pending row. Returns id.

        Idempotent on ``(source_hash, gate_decision.source_id)`` — if a
        prior run already enqueued the same source, returns the existing
        id without inserting a duplicate. Rejects with ValueError if the
        gate decision wasn't ``accept``.
        """
        if gate_decision.decision != "accept":
            raise ValueError(
                f"only accepted gate decisions can be enqueued, got "
                f"{gate_decision.decision}"
            )
        assert self._db is not None, "CandidateStore.init() not called"
        async with self._lock:
            existing = await self._find_by_source(
                gate_decision.source_id, source_hash
            )
            if existing is not None:
                return existing

            cid = gate_decision.source_id
            if not cid.startswith("candidate:"):
                cid = f"candidate:{cid}"

            now = _utc_now()
            await self._db.execute(
                """
                INSERT INTO memory_candidates (
                    id, source_ids, source_hash, status, gate_score,
                    rough_category, candidate_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, ?, ?)
                """,
                (
                    cid,
                    json.dumps(source_ids),
                    source_hash,
                    gate_decision.salience,
                    gate_decision.rough_category,
                    now,
                    now,
                ),
            )
            await self._db.commit()
            return cid

    async def fill_candidate(
        self, candidate_id: str, candidate: MemoryCandidate,
    ) -> None:
        """Write the structured form back to the pending row."""
        assert self._db is not None
        await self._db.execute(
            """
            UPDATE memory_candidates
               SET candidate_json = ?, updated_at = ?
             WHERE id = ?
            """,
            (candidate.model_dump_json(), _utc_now(), candidate_id),
        )
        await self._db.commit()

    async def mark_compiling(self, candidate_ids: list[str]) -> None:
        """Atomically transition pending → compiling. Idempotent."""
        if not candidate_ids:
            return
        assert self._db is not None
        async with self._lock:
            placeholders = ",".join("?" * len(candidate_ids))
            await self._db.execute(
                f"""
                UPDATE memory_candidates
                   SET status = 'compiling', updated_at = ?
                 WHERE id IN ({placeholders}) AND status = 'pending'
                """,
                (_utc_now(), *candidate_ids),
            )
            await self._db.commit()

    async def mark_compiled(
        self, candidate_id: str, output_page_ids: list[str] | None = None,
    ) -> None:
        """Mark compile finished. ``output_page_ids`` is recorded in
        ``last_error`` slot reused as a free-form note (we don't add a
        dedicated column for one summary line)."""
        assert self._db is not None
        note = (
            f"compiled → {','.join(output_page_ids)}"
            if output_page_ids
            else None
        )
        await self._db.execute(
            """
            UPDATE memory_candidates
               SET status = 'compiled', updated_at = ?, last_error = ?
             WHERE id = ?
            """,
            (_utc_now(), note, candidate_id),
        )
        await self._db.commit()

    async def mark_failed(self, candidate_id: str, error: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            UPDATE memory_candidates
               SET status = 'failed', updated_at = ?, last_error = ?
             WHERE id = ?
            """,
            (_utc_now(), error, candidate_id),
        )
        await self._db.commit()

    # ── Read API ────────────────────────────────────────────────────

    async def get_pending(self, limit: int = 50) -> list[PendingRecord]:
        assert self._db is not None
        async with self._db.execute(
            """
            SELECT id, source_ids, source_hash, status, gate_score,
                   rough_category, candidate_json, created_at,
                   updated_at, last_error
              FROM memory_candidates
             WHERE status = 'pending'
             ORDER BY created_at
             LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    async def get_by_id(self, candidate_id: str) -> PendingRecord | None:
        assert self._db is not None
        async with self._db.execute(
            """
            SELECT id, source_ids, source_hash, status, gate_score,
                   rough_category, candidate_json, created_at,
                   updated_at, last_error
              FROM memory_candidates
             WHERE id = ?
            """,
            (candidate_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_record(row) if row else None

    async def _find_by_source(
        self, source_id: str, source_hash: str,
    ) -> str | None:
        assert self._db is not None
        cid = source_id if source_id.startswith("candidate:") else f"candidate:{source_id}"
        async with self._db.execute(
            "SELECT id FROM memory_candidates WHERE id = ? AND source_hash = ?",
            (cid, source_hash),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None


def _row_to_record(row: tuple[Any, ...]) -> PendingRecord:
    return PendingRecord(
        id=row[0],
        source_ids=json.loads(row[1]) if row[1] else [],
        source_hash=row[2],
        status=row[3],
        gate_score=row[4],
        rough_category=row[5],
        candidate_json=row[6],
        created_at=row[7],
        updated_at=row[8],
        last_error=row[9],
    )
