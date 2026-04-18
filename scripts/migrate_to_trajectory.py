#!/usr/bin/env python3
"""Step 2e migration: conversations → trajectory.

Moves the legacy ``conversations`` table rows into the new ``trajectory``
table as described in cleanup_report_step2 §5.2. Three modes:

  --init-schema  Create the trajectory / commitments tables and indexes in
                 a clean or pre-existing lapwing.db. Idempotent. Required
                 one-time before --execute when bootstrapping a DB that
                 never ran TrajectoryStore. Also usable for test-env setup
                 and disaster recovery.
  --dry-run      Read-only audit. Prints what would happen — counts,
                 per-chat histogram, discarded rows with reason, predicted
                 write count. No writes to any DB.
  --execute      Perform the migration. Writes to ``trajectory``; does NOT
                 delete the source ``conversations`` table (Step 3 cleans up).

The mapping rules, enum values and discard policy live in this file alone so
the Step 2 cleanup_report can cite it verbatim.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiosqlite

from src.core.trajectory_store import TrajectoryEntryType
from src.logging.state_mutation_log import StateMutationLog


# ── Mapping table (matches §5.2) ────────────────────────────────────────

def _map_row(
    row_id: int,
    chat_id: str | None,
    role: str | None,
    content: str | None,
    ts: str | None,
) -> tuple[str, str, str, dict[str, Any]] | tuple[None, None, None, None]:
    """Map a legacy conversations row to (entry_type, source_chat_id, actor,
    content_dict). Returns (None, None, None, None) if the row should be
    discarded; the caller logs the reason.
    """
    if role not in {"user", "assistant"}:
        return (None, None, None, None)
    if chat_id is None or chat_id == "":
        return (None, None, None, None)
    if content is None or content == "":
        return (None, None, None, None)

    if chat_id == "__consciousness__":
        return (
            TrajectoryEntryType.INNER_THOUGHT.value,
            "__inner__",
            "lapwing" if role == "assistant" else "system",
            {"text": content, "trigger_type": "legacy_migrated"},
        )

    if role == "user":
        return (
            TrajectoryEntryType.USER_MESSAGE.value,
            chat_id,
            "user",
            {"text": content},
        )
    # role == "assistant"
    return (
        TrajectoryEntryType.ASSISTANT_TEXT.value,
        chat_id,
        "lapwing",
        {"text": content},
    )


def _parse_legacy_timestamp(raw: str | None) -> float | None:
    """Legacy rows store ISO-8601 strings; return unix float or None."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


# ── Audit buckets ──────────────────────────────────────────────────────

@dataclass
class Audit:
    total: int = 0
    migrated: int = 0
    discarded: int = 0
    by_entry_type: dict[str, int] = field(default_factory=dict)
    by_source_chat: dict[str, int] = field(default_factory=dict)
    discards: list[dict[str, Any]] = field(default_factory=list)
    chat_histogram_legacy: list[tuple[str, int]] = field(default_factory=list)
    ts_imputed: int = 0

    def add_migrated(self, entry_type: str, source_chat_id: str) -> None:
        self.migrated += 1
        self.by_entry_type[entry_type] = self.by_entry_type.get(entry_type, 0) + 1
        self.by_source_chat[source_chat_id] = (
            self.by_source_chat.get(source_chat_id, 0) + 1
        )

    def add_discard(self, row_id: int, reason: str, details: dict[str, Any]) -> None:
        self.discarded += 1
        self.discards.append({"id": row_id, "reason": reason, **details})

    def print_report(self, mode: str, out: Any = None) -> None:
        if out is None:
            out = sys.stdout
        p = lambda *a: print(*a, file=out)  # noqa: E731
        p("=" * 72)
        p(f"migrate_to_trajectory — {mode}")
        p("=" * 72)
        p(f"conversations rows read:       {self.total}")
        p(f"predicted trajectory writes:   {self.migrated}")
        p(f"discards:                      {self.discarded}")
        p(f"  → by reason:")
        reason_counts: dict[str, int] = {}
        for d in self.discards:
            reason_counts[d["reason"]] = reason_counts.get(d["reason"], 0) + 1
        for reason, count in sorted(reason_counts.items()):
            p(f"      {reason}: {count}")
        p(f"imputed timestamps (row_id-sequential): {self.ts_imputed}")
        p("")
        p("per-entry_type counts (post-migration):")
        for et, c in sorted(self.by_entry_type.items()):
            p(f"  {et}: {c}")
        p("")
        p("top 10 legacy chat_id histogram (pre-migration):")
        for chat_id, c in self.chat_histogram_legacy[:10]:
            p(f"  {chat_id}: {c}")
        p("")
        p("top 10 new source_chat_id histogram (post-migration):")
        new_hist = sorted(
            self.by_source_chat.items(), key=lambda kv: kv[1], reverse=True
        )
        for chat_id, c in new_hist[:10]:
            p(f"  {chat_id}: {c}")
        p("")
        if self.discards:
            p("discarded rows (first 30):")
            for d in self.discards[:30]:
                p(f"  id={d['id']} reason={d['reason']} {d}")
            if len(self.discards) > 30:
                p(f"  ... and {len(self.discards) - 30} more")
        p("=" * 72)
        invariant_ok = self.total == self.migrated + self.discarded
        p(f"invariant (total == migrated + discarded): {invariant_ok}")
        if not invariant_ok:
            p("!!! INVARIANT VIOLATED — DO NOT PROCEED !!!")
        p("=" * 72)


# ── Core scan ──────────────────────────────────────────────────────────

async def scan(db_path: Path) -> tuple[Audit, list[tuple[float, str, str, str, dict]]]:
    """Read every conversations row; produce an Audit and the staged inserts.

    Staged tuple shape: (timestamp, entry_type, source_chat_id, actor, content_dict).
    """
    audit = Audit()
    staged: list[tuple[float, str, str, str, dict]] = []

    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute(
            "SELECT chat_id, COUNT(*) FROM conversations "
            "GROUP BY chat_id ORDER BY 2 DESC"
        ) as cur:
            audit.chat_histogram_legacy = [
                (row[0], row[1]) for row in await cur.fetchall()
            ]

        async with db.execute(
            "SELECT id, chat_id, role, content, timestamp "
            "FROM conversations ORDER BY id ASC"
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()

    audit.total = len(rows)

    # For sequential timestamp imputation
    last_good_ts: float = 0.0

    for rid, chat_id, role, content, ts_raw in rows:
        # Decode / impute timestamp first
        ts = _parse_legacy_timestamp(ts_raw)
        if ts is None:
            ts = last_good_ts + 0.001 if last_good_ts else time.time()
            audit.ts_imputed += 1
            imputed = True
        else:
            last_good_ts = ts
            imputed = False

        # UTF-8 roundtrip sanity — most rows are already decoded by sqlite,
        # but catch surrogates / lone bytes defensively.
        content_clean: str | None = content
        if content is not None:
            try:
                content.encode("utf-8")
            except UnicodeEncodeError:
                audit.add_discard(
                    rid, "utf8_decode_failed",
                    {"chat_id": chat_id, "role": role},
                )
                continue

        # Discard rules
        if content_clean is None or content_clean == "":
            audit.add_discard(
                rid, "empty_content",
                {"chat_id": chat_id, "role": role},
            )
            continue
        if role not in {"user", "assistant"}:
            audit.add_discard(
                rid, "bad_role",
                {"chat_id": chat_id, "role": role},
            )
            continue
        if chat_id is None or chat_id == "":
            audit.add_discard(
                rid, "missing_chat_id",
                {"role": role},
            )
            continue

        entry_type, src, actor, payload = _map_row(
            rid, chat_id, role, content_clean, ts_raw
        )
        assert entry_type and src and actor and payload is not None
        if imputed:
            payload = {**payload, "ts_imputed": True}

        staged.append((ts, entry_type, src, actor, payload))
        audit.add_migrated(entry_type, src)

    return audit, staged


# ── Execute writes ─────────────────────────────────────────────────────

async def write_staged(
    db_path: Path,
    staged: list[tuple[float, str, str, str, dict]],
    *,
    force: bool = False,
) -> None:
    """Bulk insert the staged tuples into trajectory.

    Refuses to write if trajectory already has rows of the migrated
    entry_types (USER_MESSAGE/ASSISTANT_TEXT/INNER_THOUGHT), to avoid a
    double-migration creating duplicate entries. Use ``force=True`` only
    after manually clearing prior rows.
    """
    db = await aiosqlite.connect(db_path)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        # trajectory table is already created by TrajectoryStore.init() at
        # app start; the script does not create it here. Abort if absent so
        # we don't silently write into a malformed db.
        async with db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='trajectory'"
        ) as cur:
            if await cur.fetchone() is None:
                raise RuntimeError(
                    "trajectory table does not exist in %s — run the app "
                    "once so TrajectoryStore.init() creates it, or bootstrap "
                    "it manually before running --execute." % db_path
                )

        if not force:
            async with db.execute(
                "SELECT COUNT(*) FROM trajectory "
                "WHERE entry_type IN (?, ?, ?)",
                (
                    TrajectoryEntryType.USER_MESSAGE.value,
                    TrajectoryEntryType.ASSISTANT_TEXT.value,
                    TrajectoryEntryType.INNER_THOUGHT.value,
                ),
            ) as cur:
                existing = (await cur.fetchone())[0]
            if existing > 0:
                raise RuntimeError(
                    f"trajectory already has {existing} migrated-type rows; "
                    "refusing to double-migrate. Backup + delete manually, "
                    "or pass --force."
                )

        rows = [
            (
                ts,
                entry_type,
                source_chat_id,
                actor,
                json.dumps(content, ensure_ascii=False, default=str),
                None,   # related_commitment_id
                None,   # related_iteration_id — legacy data has no iteration
                None,   # related_tool_call_id
            )
            for ts, entry_type, source_chat_id, actor, content in staged
        ]

        await db.executemany(
            """INSERT INTO trajectory
               (timestamp, entry_type, source_chat_id, actor, content_json,
                related_commitment_id, related_iteration_id,
                related_tool_call_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await db.commit()
    finally:
        await db.close()


async def post_execute_verify(
    db_path: Path, expected_migrated: int
) -> tuple[int, bool]:
    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute(
            "SELECT COUNT(*) FROM trajectory "
            "WHERE entry_type IN (?, ?, ?)",
            (
                TrajectoryEntryType.USER_MESSAGE.value,
                TrajectoryEntryType.ASSISTANT_TEXT.value,
                TrajectoryEntryType.INNER_THOUGHT.value,
            ),
        ) as cur:
            migrated_rows = (await cur.fetchone())[0]
    finally:
        await db.close()
    return migrated_rows, migrated_rows == expected_migrated


# ── Schema init ────────────────────────────────────────────────────────

async def init_schema(db_path: Path) -> dict[str, int]:
    """Create trajectory + commitments tables and indexes in ``db_path``.

    Idempotent — runs CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
    EXISTS. Uses the TrajectoryStore and CommitmentStore init methods as
    the single source of truth for the schema, so this subcommand stays
    aligned with the runtime.

    Returns a summary dict with existing row counts post-init.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Lazy import so tests can run without the runtime wiring.
    from src.core.commitments import CommitmentStore
    from src.core.trajectory_store import TrajectoryStore

    # Use a scratch mutation_log file alongside the target db: we do NOT
    # log the schema-init itself (no actual state change yet) and we close
    # the log immediately. The store classes require a StateMutationLog
    # instance but do not record anything during init().
    scratch_log_path = db_path.parent / "mutation_log.db"
    scratch_logs_dir = db_path.parent / "logs"
    log = StateMutationLog(scratch_log_path, logs_dir=scratch_logs_dir)
    await log.init()
    try:
        traj = TrajectoryStore(db_path, log)
        await traj.init()
        await traj.close()
        commit = CommitmentStore(db_path, log)
        await commit.init()
        await commit.close()
    finally:
        await log.close()

    # Post-init counts
    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute("SELECT COUNT(*) FROM trajectory") as cur:
            traj_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM commitments") as cur:
            commit_count = (await cur.fetchone())[0]
    finally:
        await db.close()
    return {"trajectory_rows": traj_count, "commitments_rows": commit_count}


# ── CLI ────────────────────────────────────────────────────────────────

async def _run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)

    if args.init_schema:
        counts = await init_schema(db_path)
        print(
            f"init-schema OK: db={db_path}  "
            f"trajectory rows={counts['trajectory_rows']}  "
            f"commitments rows={counts['commitments_rows']}"
        )
        return 0

    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2

    audit, staged = await scan(db_path)
    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    audit.print_report(mode)

    if args.dry_run:
        return 0

    # Sanity gate before writing
    if audit.total != audit.migrated + audit.discarded:
        print("invariant violation — refusing to write", file=sys.stderr)
        return 3

    print(f"writing {audit.migrated} rows to trajectory ...")
    await write_staged(db_path, staged, force=args.force)
    actual, ok = await post_execute_verify(db_path, audit.migrated)
    print(
        f"post-execute verify: trajectory rows "
        f"(user/assistant/inner) = {actual}, "
        f"expected = {audit.migrated}, ok = {ok}"
    )
    return 0 if ok else 4


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--init-schema", dest="init_schema", action="store_true",
        help="create trajectory + commitments tables (idempotent)",
    )
    group.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="audit only — no writes",
    )
    group.add_argument(
        "--execute", dest="execute", action="store_true",
        help="perform the migration (dry-run invariants must hold)",
    )
    p.add_argument(
        "--db", default="data/lapwing.db", help="path to lapwing.db",
    )
    p.add_argument(
        "--force", action="store_true",
        help="overwrite prior migration rows (use only after manual backup)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
