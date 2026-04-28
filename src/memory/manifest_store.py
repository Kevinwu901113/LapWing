"""ManifestStore — incremental processing tracker for the wiki layer.

Phase 1 §1.2 of the wiki blueprint. Two responsibilities:

1. Record every (source → wiki) compilation pass keyed by ``source_id``
   plus a hash of the source content, so the compiler can skip work
   when nothing relevant has changed.
2. Maintain a ``dirty entity`` queue separate from the audit log so
   recompilation jobs always have a single authoritative todo list,
   even when the manifest accumulates many historical rows.

Storage: lives in the shared ``data/lapwing.db`` (no new database
files), with a daily JSONL mirror under ``data/logs/`` for human
inspection. The dual-write is best-effort: SQLite is the source of
truth, JSONL is for ``grep``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

import aiosqlite
from pydantic import BaseModel, Field

logger = logging.getLogger("lapwing.memory.manifest_store")


SourceType = Literal[
    "trajectory",
    "episodic_day",
    "semantic_page",
    "note",
    "message",
]
GateDecision = Literal["accept", "reject", "defer"]
DirtyStatus = Literal["dirty", "compiling", "clean"]


class ManifestEntry(BaseModel):
    """One processing record. Persisted to ``memory_manifest`` + JSONL."""

    source_id: str
    source_type: SourceType
    source_path: str | None = None
    source_hash: str
    source_event_ids: list[str] = Field(default_factory=list)
    processed_at: str  # ISO 8601
    extractor_version: str = "v1"
    compiler_version: str = "wiki-compiler-v1"
    prompt_hash: str | None = None
    model_id: str | None = None
    output_page_ids: list[str] = Field(default_factory=list)
    dirty_entities: list[str] = Field(default_factory=list)
    gate_decision: GateDecision = "accept"
    gate_score: float | None = None
    last_error: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ManifestStore:
    """Incremental processing tracker. Dual-writes SQLite + JSONL."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        logs_dir: str | Path | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._logs_dir = (
            Path(logs_dir) if logs_dir is not None else Path("data/logs")
        )
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_manifest (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id           TEXT NOT NULL,
                source_type         TEXT NOT NULL,
                source_path         TEXT,
                source_hash         TEXT NOT NULL,
                source_event_ids    TEXT,
                processed_at        TEXT NOT NULL,
                extractor_version   TEXT NOT NULL DEFAULT 'v1',
                compiler_version    TEXT NOT NULL DEFAULT 'wiki-compiler-v1',
                prompt_hash         TEXT,
                model_id            TEXT,
                output_page_ids     TEXT,
                dirty_entities      TEXT,
                gate_decision       TEXT,
                gate_score          REAL,
                last_error          TEXT,
                created_at          TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_manifest_source_id ON memory_manifest(source_id);
            CREATE INDEX IF NOT EXISTS idx_manifest_source_hash ON memory_manifest(source_hash);
            CREATE INDEX IF NOT EXISTS idx_manifest_processed_at ON memory_manifest(processed_at);
            CREATE INDEX IF NOT EXISTS idx_manifest_gate_decision ON memory_manifest(gate_decision);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_manifest_idempotent
                ON memory_manifest(source_id, source_hash, extractor_version, compiler_version);

            CREATE TABLE IF NOT EXISTS memory_dirty_entities (
                entity_id       TEXT PRIMARY KEY,
                dirty_reason    TEXT,
                source_ids      TEXT NOT NULL DEFAULT '[]',
                first_dirty_at  TEXT NOT NULL,
                last_dirty_at   TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'dirty'
            );
            CREATE INDEX IF NOT EXISTS idx_dirty_status ON memory_dirty_entities(status);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Idempotency check ────────────────────────────────────────────

    async def is_processed(
        self,
        source_id: str,
        source_hash: str,
        *,
        extractor_version: str = "v1",
        compiler_version: str = "wiki-compiler-v1",
    ) -> bool:
        """True iff (source_id, source_hash, versions) was already recorded."""
        assert self._db is not None, "ManifestStore.init() not called"
        async with self._db.execute(
            """
            SELECT 1 FROM memory_manifest
            WHERE source_id = ? AND source_hash = ?
              AND extractor_version = ? AND compiler_version = ?
            LIMIT 1
            """,
            (source_id, source_hash, extractor_version, compiler_version),
        ) as cur:
            row = await cur.fetchone()
        return row is not None

    # ── Write path ───────────────────────────────────────────────────

    async def record_processing(self, entry: ManifestEntry) -> int:
        """Insert an entry. Idempotent on the unique index — returns the
        existing row id if the same (source, hash, versions) was already
        recorded. Side effect: also enqueues ``entry.dirty_entities``
        into ``memory_dirty_entities`` and writes a JSONL mirror line.
        """
        assert self._db is not None, "ManifestStore.init() not called"
        async with self._write_lock:
            row_id = await self._insert_or_get(entry)
            if entry.dirty_entities:
                await self._enqueue_dirty(entry.dirty_entities, entry.source_id)
            await self._db.commit()
            self._append_jsonl(entry, row_id)
        return row_id

    async def _insert_or_get(self, entry: ManifestEntry) -> int:
        assert self._db is not None
        try:
            async with self._db.execute(
                """
                INSERT INTO memory_manifest (
                    source_id, source_type, source_path, source_hash,
                    source_event_ids, processed_at, extractor_version,
                    compiler_version, prompt_hash, model_id,
                    output_page_ids, dirty_entities, gate_decision,
                    gate_score, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.source_id,
                    entry.source_type,
                    entry.source_path,
                    entry.source_hash,
                    json.dumps(entry.source_event_ids),
                    entry.processed_at,
                    entry.extractor_version,
                    entry.compiler_version,
                    entry.prompt_hash,
                    entry.model_id,
                    json.dumps(entry.output_page_ids),
                    json.dumps(entry.dirty_entities),
                    entry.gate_decision,
                    entry.gate_score,
                    entry.last_error,
                ),
            ) as cur:
                return cur.lastrowid or 0
        except aiosqlite.IntegrityError:
            async with self._db.execute(
                """
                SELECT id FROM memory_manifest
                WHERE source_id = ? AND source_hash = ?
                  AND extractor_version = ? AND compiler_version = ?
                """,
                (
                    entry.source_id,
                    entry.source_hash,
                    entry.extractor_version,
                    entry.compiler_version,
                ),
            ) as cur:
                row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def _enqueue_dirty(
        self, entity_ids: Iterable[str], source_id: str
    ) -> None:
        """Upsert dirty entries for the given entity ids."""
        assert self._db is not None
        now = _utc_now()
        for eid in entity_ids:
            async with self._db.execute(
                "SELECT source_ids, status FROM memory_dirty_entities WHERE entity_id = ?",
                (eid,),
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                await self._db.execute(
                    """
                    INSERT INTO memory_dirty_entities
                        (entity_id, dirty_reason, source_ids,
                         first_dirty_at, last_dirty_at, status)
                    VALUES (?, ?, ?, ?, ?, 'dirty')
                    """,
                    (eid, "manifest", json.dumps([source_id]), now, now),
                )
            else:
                existing = json.loads(row[0]) if row[0] else []
                if source_id not in existing:
                    existing.append(source_id)
                await self._db.execute(
                    """
                    UPDATE memory_dirty_entities
                       SET source_ids    = ?,
                           last_dirty_at = ?,
                           status        = 'dirty'
                     WHERE entity_id = ?
                    """,
                    (json.dumps(existing), now, eid),
                )

    def _append_jsonl(self, entry: ManifestEntry, row_id: int) -> None:
        try:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            today = date.today().isoformat()
            path = self._logs_dir / f"memory_manifest_{today}.jsonl"
            payload = entry.model_dump()
            payload["_row_id"] = row_id
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[manifest_store] jsonl write failed: %s", exc)

    # ── Dirty-queue API ──────────────────────────────────────────────

    async def get_dirty_entities(self) -> list[str]:
        """Entity ids currently flagged ``dirty`` (excludes ``compiling``)."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT entity_id FROM memory_dirty_entities WHERE status = 'dirty' ORDER BY first_dirty_at"
        ) as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def mark_entities_compiling(self, entity_ids: list[str]) -> None:
        if not entity_ids:
            return
        assert self._db is not None
        placeholders = ",".join("?" * len(entity_ids))
        await self._db.execute(
            f"UPDATE memory_dirty_entities SET status='compiling' WHERE entity_id IN ({placeholders})",
            entity_ids,
        )
        await self._db.commit()

    async def mark_entities_clean(self, entity_ids: list[str]) -> None:
        if not entity_ids:
            return
        assert self._db is not None
        placeholders = ",".join("?" * len(entity_ids))
        await self._db.execute(
            f"UPDATE memory_dirty_entities SET status='clean' WHERE entity_id IN ({placeholders})",
            entity_ids,
        )
        await self._db.commit()

    # ── Provenance ───────────────────────────────────────────────────

    async def get_provenance(self, page_id: str) -> list[ManifestEntry]:
        """Manifest rows that produced or updated the given wiki page."""
        assert self._db is not None
        like = f'%"{page_id}"%'
        async with self._db.execute(
            """
            SELECT source_id, source_type, source_path, source_hash,
                   source_event_ids, processed_at, extractor_version,
                   compiler_version, prompt_hash, model_id,
                   output_page_ids, dirty_entities, gate_decision,
                   gate_score, last_error
              FROM memory_manifest
             WHERE output_page_ids LIKE ?
             ORDER BY processed_at
            """,
            (like,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_entry(r) for r in rows]


def _row_to_entry(row: tuple[Any, ...]) -> ManifestEntry:
    return ManifestEntry(
        source_id=row[0],
        source_type=row[1],
        source_path=row[2],
        source_hash=row[3],
        source_event_ids=json.loads(row[4]) if row[4] else [],
        processed_at=row[5],
        extractor_version=row[6],
        compiler_version=row[7],
        prompt_hash=row[8],
        model_id=row[9],
        output_page_ids=json.loads(row[10]) if row[10] else [],
        dirty_entities=json.loads(row[11]) if row[11] else [],
        gate_decision=row[12] or "accept",
        gate_score=row[13],
        last_error=row[14],
    )
