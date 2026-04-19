"""MVP cleanup (2026-04-19): drop legacy SQLite tables.

The 2026-04-19 MVP cleanup removes five tables that survived into v2.0
but have no live readers (see docs/superpowers/plans/2026-04-19-mvp-cleanup.md):

  * user_facts       — replaced by SemanticStore (data/memory/semantic/kevin.md)
  * interest_topics  — merged into SemanticStore.world
  * discoveries      — write-only orphan (no SELECT anywhere in src/)
  * todos            — empty; superseded by DurableScheduler (reminders_v2)
  * reminders        — legacy; superseded by DurableScheduler (reminders_v2)

Usage:

    python scripts/migrations/mvp_drop_legacy_tables.py --dry-run
    python scripts/migrations/mvp_drop_legacy_tables.py --execute

Back up first — rows are unrecoverable once dropped. The MVP cleanup
took backups at ``~/lapwing-backups/pre_mvp_cleanup_<timestamp>/`` via
JSONL exports before invoking this script.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

TABLES_TO_DROP = (
    "user_facts",
    "interest_topics",
    "discoveries",
    "todos",
    "reminders",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/lapwing.db"),
        help="Path to lapwing.db (default: data/lapwing.db)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print counts only")
    mode.add_argument("--execute", action="store_true", help="Actually DROP tables")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"error: {args.db} not found", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        targets = [t for t in TABLES_TO_DROP if t in existing]
        if not targets:
            print("nothing to do — all target tables already absent")
            return 0

        for table in targets:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count} rows")

        if args.dry_run:
            print("\ndry-run — no changes made")
            return 0

        for table in targets:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()

        after = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        survivors = [t for t in TABLES_TO_DROP if t in after]
        if survivors:
            print(f"error: tables still present after DROP: {survivors}", file=sys.stderr)
            return 2

        print(f"\ndropped {len(targets)} tables: {', '.join(targets)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
