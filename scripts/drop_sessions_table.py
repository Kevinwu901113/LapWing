#!/usr/bin/env python3
"""Step 2j: archive the legacy ``sessions`` table then DROP it.

Blueprint v2.0 Step 2j §7.2. The session concept is being removed from
the codebase; existing rows are preserved as JSON so Step 4 can inspect
them when re-specifying session semantics.

Usage:
    python scripts/drop_sessions_table.py --audit      # report, no writes
    python scripts/drop_sessions_table.py --execute \\
        --archive ~/lapwing-backups/pre_step2_*/sessions_archive.json

``--execute`` requires an ``--archive`` path; the script writes every
row there before issuing DROP TABLE. Fails cleanly if the table is
already gone (exit 0).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


async def _table_exists(db, name: str) -> bool:
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ) as cur:
        return (await cur.fetchone()) is not None


async def _rows(db) -> list[dict]:
    async with db.execute("PRAGMA table_info(sessions)") as cur:
        cols = [row[1] async for row in cur]
    async with db.execute(
        f"SELECT {', '.join(cols)} FROM sessions ORDER BY id"
    ) as cur:
        return [dict(zip(cols, r)) async for r in cur]


async def _audit(db_path: Path) -> int:
    db = await aiosqlite.connect(db_path)
    try:
        if not await _table_exists(db, "sessions"):
            print(f"sessions table does not exist in {db_path} — nothing to archive")
            return 0
        rows = await _rows(db)
        print(f"sessions table rows: {len(rows)}")
        if rows:
            statuses: dict[str, int] = {}
            chat_ids: dict[str, int] = {}
            for r in rows:
                statuses[r.get("status", "?")] = statuses.get(r.get("status", "?"), 0) + 1
                chat_ids[r.get("chat_id", "?")] = chat_ids.get(r.get("chat_id", "?"), 0) + 1
            print(f"  status histogram: {statuses}")
            print(f"  chat_id histogram: {chat_ids}")
            print(f"  first row: {rows[0]}")
            print(f"  last row: {rows[-1]}")
        return len(rows)
    finally:
        await db.close()


async def _execute(db_path: Path, archive_path: Path) -> int:
    db = await aiosqlite.connect(db_path)
    try:
        if not await _table_exists(db, "sessions"):
            print(f"sessions table does not exist in {db_path} — nothing to drop")
            return 0
        rows = await _rows(db)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(
            json.dumps(
                {
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "source_db": str(db_path),
                    "row_count": len(rows),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"archived {len(rows)} rows to {archive_path}")
        await db.execute("DROP TABLE sessions")
        await db.execute("DROP INDEX IF EXISTS idx_sessions_chat_id_status")
        await db.execute("DROP INDEX IF EXISTS idx_sessions_last_active")
        await db.execute("DROP INDEX IF EXISTS idx_conversations_session_id")
        await db.commit()
        print("DROP TABLE sessions + related indexes executed")
        return len(rows)
    finally:
        await db.close()


async def _run(args) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2
    if args.audit:
        await _audit(db_path)
        return 0
    if args.execute:
        if not args.archive:
            print("--execute requires --archive <path>", file=sys.stderr)
            return 2
        await _execute(db_path, Path(args.archive))
        return 0
    return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--audit", action="store_true", help="report only")
    grp.add_argument("--execute", action="store_true", help="archive + drop")
    p.add_argument("--db", default="data/lapwing.db")
    p.add_argument("--archive", default=None, help="JSON archive path (required for --execute)")
    return asyncio.run(_run(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
