"""StateMutationLog — append-only durable log of state-mutating events.

Blueprint v2.0 Step 1 §2. Records LLM calls, tool invocations, iteration
boundaries, and system lifecycle in a separate SQLite file `mutation_log.db`,
plus a daily JSONL mirror for human inspection. Complementary to the
in-memory `Dispatcher` pub/sub: mutation_log is durable source of truth,
dispatcher is UI live stream.

The event-type vocabulary is strict: only members of ``MutationType`` are
accepted. Callers violating this raise ``TypeError`` instead of recording
an unknown type — this is intentional (see blueprint §2.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

import aiosqlite

logger = logging.getLogger("lapwing.logging.state_mutation_log")


# ContextVars propagate iteration_id / chat_id implicitly through async calls,
# so callers don't have to thread them through every method signature.
_current_iteration_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "lapwing_iteration_id", default=None
)
_current_chat_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "lapwing_chat_id", default=None
)
# Most recent LLM request_id in the current context — used by tool-call
# records to set ``parent_llm_response_id`` (plan §2.3). Updated inside
# ``LLMRouter._tracked_call``.
_last_llm_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "lapwing_last_llm_request_id", default=None
)


def current_iteration_id() -> str | None:
    return _current_iteration_id.get()


def current_chat_id() -> str | None:
    return _current_chat_id.get()


def current_llm_request_id() -> str | None:
    return _last_llm_request_id.get()


def set_last_llm_request_id(request_id: str | None) -> None:
    _last_llm_request_id.set(request_id)


@contextlib.contextmanager
def iteration_context(
    iteration_id: str | None,
    chat_id: str | None = None,
) -> Iterator[None]:
    """Bind iteration_id / chat_id to the current async context for its duration.

    The body may contain ``await`` — contextvars propagate automatically through
    awaits within the same Task.
    """
    t_iter = _current_iteration_id.set(iteration_id)
    t_chat = _current_chat_id.set(chat_id)
    try:
        yield
    finally:
        _current_iteration_id.reset(t_iter)
        _current_chat_id.reset(t_chat)


class MutationType(str, Enum):
    """Closed vocabulary of event types accepted by ``StateMutationLog``.

    Members marked "Step 1" are instrumented in this Step. Members marked
    "future" are defined up-front so payload schemas stay consistent when
    their call sites arrive, but have no emitters yet.
    """

    # --- Iteration lifecycle (Step 1) ---
    ITERATION_STARTED = "iteration.started"
    ITERATION_ENDED = "iteration.ended"

    # --- LLM calls (Step 1) ---
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"

    # --- Tool calls (Step 1) ---
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"

    # --- System lifecycle (Step 1) ---
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"

    # --- Future Steps: defined now, no emitters in Step 1 ---
    TRAJECTORY_APPENDED = "trajectory.appended"        # Step 2
    ATTENTION_CHANGED = "attention.changed"            # Step 2
    COMMITMENT_CREATED = "commitment.created"          # Step 5
    COMMITMENT_STATUS_CHANGED = "commitment.status_changed"  # Step 5
    IDENTITY_EDITED = "identity.edited"                # Step 3+
    MEMORY_RAPTOR_UPDATED = "memory.raptor_updated"    # Step 3+
    MEMORY_FILE_EDITED = "memory.file_edited"          # Step 3+

    # TEMPORARY (Step 1 → Step 5): records suspected MiniMax hallucinations
    # where the reply claims prior work with zero supporting tool calls.
    # Observation-only; does NOT intercept the reply. Scheduled for removal
    # in Step 5 once the trajectory/commitment path lands. See
    # cleanup_report_step1.md debt registry.
    LLM_HALLUCINATION_SUSPECTED = "llm.hallucination_suspected"


@dataclass
class Mutation:
    """A row from the ``mutations`` table."""

    id: int
    timestamp: float
    event_type: str
    iteration_id: str | None
    chat_id: str | None
    payload: dict[str, Any]
    payload_size: int


def new_iteration_id() -> str:
    """Fresh id for an iteration — used to correlate events within one loop."""
    return uuid.uuid4().hex


def new_request_id() -> str:
    """Fresh id correlating an LLM_REQUEST with its matching LLM_RESPONSE."""
    return uuid.uuid4().hex


class StateMutationLog:
    """Append-only log. Dual output: SQLite + daily JSONL mirror."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        logs_dir: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.logs_dir = (
            Path(logs_dir) if logs_dir is not None else self.db_path.parent / "logs"
        )
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS mutations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                iteration_id TEXT,
                chat_id TEXT,
                payload_json TEXT NOT NULL,
                payload_size INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mut_timestamp ON mutations(timestamp);
            CREATE INDEX IF NOT EXISTS idx_mut_event_type ON mutations(event_type, timestamp);
            CREATE INDEX IF NOT EXISTS idx_mut_iteration ON mutations(iteration_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_mut_chat ON mutations(chat_id, timestamp);
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def record(
        self,
        event_type: MutationType,
        payload: dict[str, Any],
        *,
        iteration_id: str | None = None,
        chat_id: str | None = None,
    ) -> int:
        """Write one mutation. Returns the autoincrement id (or -1 if not init)."""
        if not isinstance(event_type, MutationType):
            raise TypeError(
                "event_type must be a MutationType enum member, "
                f"got {type(event_type).__name__}: {event_type!r}"
            )
        if self._db is None:
            logger.warning(
                "StateMutationLog not initialized; dropping event %s", event_type.value
            )
            return -1

        timestamp = time.time()
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        payload_size = len(payload_json.encode("utf-8"))

        async with self._write_lock:
            cursor = await self._db.execute(
                """INSERT INTO mutations
                   (timestamp, event_type, iteration_id, chat_id, payload_json, payload_size)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    timestamp,
                    event_type.value,
                    iteration_id,
                    chat_id,
                    payload_json,
                    payload_size,
                ),
            )
            await self._db.commit()
            mutation_id = cursor.lastrowid or -1

        try:
            await self._append_jsonl(
                timestamp=timestamp,
                mutation_id=mutation_id,
                event_type=event_type,
                iteration_id=iteration_id,
                chat_id=chat_id,
                payload_json=payload_json,
            )
        except Exception:
            logger.warning(
                "mutation %d JSONL mirror failed", mutation_id, exc_info=True
            )

        return mutation_id

    async def _append_jsonl(
        self,
        *,
        timestamp: float,
        mutation_id: int,
        event_type: MutationType,
        iteration_id: str | None,
        chat_id: str | None,
        payload_json: str,
    ) -> None:
        day = date.fromtimestamp(timestamp).isoformat()
        path = self.logs_dir / f"mutations_{day}.log"
        line = json.dumps(
            {
                "id": mutation_id,
                "timestamp": timestamp,
                "iso": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                "event_type": event_type.value,
                "iteration_id": iteration_id,
                "chat_id": chat_id,
                "payload": json.loads(payload_json),
            },
            ensure_ascii=False,
        )
        await asyncio.to_thread(self._write_line_sync, path, line)

    @staticmethod
    def _write_line_sync(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ── Query API ────────────────────────────────────────────────────────

    async def query_by_iteration(self, iteration_id: str) -> list[Mutation]:
        return await self._query(
            "iteration_id = ?",
            (iteration_id,),
            order="timestamp ASC, id ASC",
        )

    async def query_by_window(
        self, start_ts: float, end_ts: float, *, limit: int = 1000
    ) -> list[Mutation]:
        return await self._query(
            "timestamp >= ? AND timestamp < ?",
            (start_ts, end_ts),
            order="timestamp ASC, id ASC",
            limit=limit,
        )

    async def query_by_type(
        self, event_type: MutationType, *, limit: int = 100
    ) -> list[Mutation]:
        return await self._query(
            "event_type = ?",
            (event_type.value,),
            order="timestamp DESC, id DESC",
            limit=limit,
        )

    async def query_llm_request(self, request_id: str) -> Mutation | None:
        """Find the LLM_REQUEST mutation whose payload has the given ``request_id``."""
        if self._db is None:
            return None
        # payload_json is a JSON string; a LIKE probe is cheap and sufficient
        # because request_ids are uuid4.hex (collision-proof).
        like = f'%"request_id": "{request_id}"%'
        async with self._db.execute(
            """SELECT id, timestamp, event_type, iteration_id, chat_id, payload_json, payload_size
               FROM mutations
               WHERE event_type = ? AND payload_json LIKE ?
               ORDER BY timestamp DESC, id DESC
               LIMIT 1""",
            (MutationType.LLM_REQUEST.value, like),
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_mutation(row) if row else None

    async def _query(
        self,
        where: str,
        params: tuple[Any, ...],
        *,
        order: str = "timestamp ASC, id ASC",
        limit: int | None = None,
    ) -> list[Mutation]:
        if self._db is None:
            return []
        sql = (
            "SELECT id, timestamp, event_type, iteration_id, chat_id, payload_json, payload_size "
            f"FROM mutations WHERE {where} ORDER BY {order}"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_mutation(row) for row in rows]

    @staticmethod
    def _row_to_mutation(row: tuple) -> Mutation:
        return Mutation(
            id=row[0],
            timestamp=row[1],
            event_type=row[2],
            iteration_id=row[3],
            chat_id=row[4],
            payload=json.loads(row[5]),
            payload_size=row[6],
        )
