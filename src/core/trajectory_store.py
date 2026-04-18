"""TrajectoryStore — main-subject behaviour timeline, cross-channel.

Blueprint v2.0 Step 2 §2. Replaces the per-chat_id ``conversations`` partition
with a single monotonic timeline of entries actor-on-world: what the user said,
what Lapwing thought, what tools she invoked, what state changed. One row per
observable behavioural moment; tool-call payload detail stays in mutation_log.

Append-only. Compaction (Step 7) is a separate write path, not exposed here.
Every ``append`` records a ``TRAJECTORY_APPENDED`` mutation — the single-truth
invariant from Blueprint v2.0 §1.3.
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
                source_chat_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                content_json TEXT NOT NULL,
                related_commitment_id TEXT,
                related_iteration_id TEXT,
                related_tool_call_id TEXT
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
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Write ───────────────────────────────────────────────────────────

    async def append(
        self,
        entry_type: TrajectoryEntryType,
        source_chat_id: str,
        actor: str,
        content: dict[str, Any],
        *,
        related_commitment_id: str | None = None,
        related_iteration_id: str | None = None,
        related_tool_call_id: str | None = None,
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
                related_commitment_id, related_iteration_id, related_tool_call_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts,
                entry_type.value,
                source_chat_id,
                actor,
                content_json,
                related_commitment_id,
                related_iteration_id,
                related_tool_call_id,
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
                },
                iteration_id=related_iteration_id,
                chat_id=source_chat_id,
            )
        except Exception:
            logger.warning(
                "trajectory %d mutation_log mirror failed", entry_id, exc_info=True
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

        ``include_inner=True`` mixes in ``source_chat_id = '__inner__'`` entries
        (consciousness-loop thinking) so the conversational path sees them.
        """
        if include_inner:
            where = "source_chat_id = ? OR source_chat_id = '__inner__'"
            params: tuple[Any, ...] = (chat_id,)
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
            "related_commitment_id, related_iteration_id, related_tool_call_id "
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
        )


# ── Legacy-dict projection ──────────────────────────────────────────
#
# Step 3 moved prompt assembly to StateSerializer, but two call paths
# still need the pre-serializer ``[{"role", "content"}]`` shape:
#
#   - brain._load_history: hands the list to _prepare_think, which
#     applies trust tagging in place and forwards as the builder's
#     trajectory_turns_override.
#   - ConversationCompactor: feeds the list into the LLM summarisation
#     prompt, which is built outside the serializer for historical
#     reasons (compaction predates Step 3).
#
# A future step can retire this helper once both callers move to the
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
