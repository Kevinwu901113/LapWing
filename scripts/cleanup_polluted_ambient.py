"""一次性清理 2026-05-03 ambient 污染事件遗留条目。

执行后写报告到 data/reports/ambient_cleanup_<timestamp>.md，列删除的 key
和 summary 摘要。执行前自动备份 data/ambient.db。
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "ambient.db"
REPORT_DIR = ROOT / "data" / "reports"
CUTOFF = datetime.fromisoformat("2026-05-04T00:00:00+08:00")


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"ambient db not found: {DB_PATH}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = DB_PATH.with_name(f"{DB_PATH.name}.bak.{stamp}")
    report_path = REPORT_DIR / f"ambient_cleanup_{stamp}.md"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    shutil.copy2(DB_PATH, backup_path)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT key, category, topic, summary, fetched_at, expires_at, source, confidence
            FROM ambient_entries
            WHERE source = 'research_writeback'
              AND confidence < 0.7
            ORDER BY fetched_at
            """
        ).fetchall()

        to_delete = [
            row for row in rows
            if _parse_dt(row["fetched_at"]) < CUTOFF
        ]

        with report_path.open("w", encoding="utf-8") as f:
            f.write("# Ambient Cleanup Report\n\n")
            f.write(f"- generated_at: {stamp}\n")
            f.write(f"- db: {DB_PATH}\n")
            f.write(f"- backup: {backup_path}\n")
            f.write("- rule: source=research_writeback, confidence<0.7, fetched_at<2026-05-04T00:00:00+08:00\n")
            f.write(f"- deleted_count: {len(to_delete)}\n\n")
            f.write("## Deleted Entries\n\n")
            if not to_delete:
                f.write("No matching entries.\n")
            for row in to_delete:
                summary = (row["summary"] or "").replace("\n", " ")[:240]
                f.write(
                    f"- `{row['key']}` | category={row['category']} | "
                    f"confidence={row['confidence']} | fetched_at={row['fetched_at']}\n"
                )
                f.write(f"  summary: {summary}\n")

        if to_delete:
            conn.executemany(
                "DELETE FROM ambient_entries WHERE key = ?",
                [(row["key"],) for row in to_delete],
            )
            conn.commit()
            conn.execute("VACUUM")
    finally:
        conn.close()

    print(f"backup: {backup_path}")
    print(f"report: {report_path}")
    print(f"deleted: {len(to_delete)}")


if __name__ == "__main__":
    main()
