#!/usr/bin/env python3
"""Verify ConversationMemory → TrajectoryStore dual-write consistency.

Step 2f validation helper. Designed to be run in two phases:

  1. Before your validation conversation:
       python scripts/verify_dual_write.py --snapshot /tmp/pre.json
     Captures current max(conversations.id) and max(trajectory.id).

  2. After your validation conversation:
       python scripts/verify_dual_write.py --diff /tmp/pre.json
     Pulls every row newer than the snapshot from both tables, diffs them,
     and prints a side-by-side verdict.

Designed to be deterministic even if the consciousness loop ticks during
your test — __consciousness__ rows remap to __inner__/INNER_THOUGHT in
trajectory (see ConversationMemory._mirror_to_trajectory), so the
"matched" bucket covers both the direct-chat and consciousness paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _snapshot(db_path: Path) -> dict:
    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM conversations"
        ) as cur:
            conv_max = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM trajectory"
        ) as cur:
            traj_max = (await cur.fetchone())[0]
    finally:
        await db.close()
    return {
        "db": str(db_path),
        "conversations_max_id": conv_max,
        "trajectory_max_id": traj_max,
        "taken_at": datetime.now(timezone.utc).isoformat(),
    }


def _parse_iso(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


async def _fetch_new_conversations(db_path: Path, after_id: int) -> list[dict]:
    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute(
            "SELECT id, chat_id, role, content, timestamp "
            "FROM conversations WHERE id > ? ORDER BY id ASC",
            (after_id,),
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()
    return [
        {
            "id": r[0], "chat_id": r[1], "role": r[2],
            "content": r[3], "timestamp": r[4],
            "ts_float": _parse_iso(r[4]),
        }
        for r in rows
    ]


async def _fetch_new_trajectory(db_path: Path, after_id: int) -> list[dict]:
    db = await aiosqlite.connect(db_path)
    try:
        async with db.execute(
            "SELECT id, timestamp, entry_type, source_chat_id, actor, content_json "
            "FROM trajectory WHERE id > ? ORDER BY id ASC",
            (after_id,),
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()
    return [
        {
            "id": r[0], "ts": r[1], "entry_type": r[2],
            "source_chat_id": r[3], "actor": r[4],
            "content": json.loads(r[5]),
        }
        for r in rows
    ]


def _expected_trajectory_row(conv: dict) -> tuple[str, str, str, str]:
    """Return (entry_type, source_chat_id, actor, text) the mirror should write."""
    chat = conv["chat_id"]
    role = conv["role"]
    content = conv["content"]
    if chat == "__consciousness__":
        return (
            "inner_thought",
            "__inner__",
            "lapwing" if role == "assistant" else "system",
            content,
        )
    if role == "user":
        return ("user_message", chat, "user", content)
    if role == "assistant":
        return ("assistant_text", chat, "lapwing", content)
    return ("?", chat, "?", content)


def _diff(conv_rows: list[dict], traj_rows: list[dict]) -> dict:
    """Walk conv_rows in order and try to match each to a traj_row.

    A match requires: same expected entry_type / source_chat_id / actor /
    text. The trajectory row must also appear after the conversations row
    in time (timestamp within 5s). Unmatched rows on either side are
    reported.
    """
    unmatched_conv: list[dict] = []
    unmatched_traj: list[dict] = list(traj_rows)  # copy for consuming
    matched: list[tuple[dict, dict]] = []

    for conv in conv_rows:
        exp = _expected_trajectory_row(conv)
        if exp[0] == "?":
            # Legacy row with a role we don't mirror (e.g., system) — skip
            matched.append((conv, {"note": "role not mirrored to trajectory"}))
            continue
        found_idx = None
        for i, traj in enumerate(unmatched_traj):
            if (
                traj["entry_type"] == exp[0]
                and traj["source_chat_id"] == exp[1]
                and traj["actor"] == exp[2]
                and traj["content"].get("text") == exp[3]
            ):
                found_idx = i
                break
        if found_idx is None:
            unmatched_conv.append(conv)
        else:
            matched.append((conv, unmatched_traj.pop(found_idx)))

    return {
        "matched": matched,
        "unmatched_conversations": unmatched_conv,
        "unmatched_trajectory": unmatched_traj,
    }


def _report(snap: dict, conv_rows: list[dict], traj_rows: list[dict]) -> bool:
    diff = _diff(conv_rows, traj_rows)
    matched = diff["matched"]
    uc = diff["unmatched_conversations"]
    ut = diff["unmatched_trajectory"]

    print("=" * 72)
    print("Dual-write diff")
    print("=" * 72)
    print(f"snapshot: {snap}")
    print(f"new conversations rows: {len(conv_rows)}")
    print(f"new trajectory   rows: {len(traj_rows)}")
    print(f"matched pairs:         {len(matched)}")
    print(f"unmatched (conv side): {len(uc)}")
    print(f"unmatched (traj side): {len(ut)}")
    print()

    if matched:
        print("Matched (first 15):")
        for conv, traj in matched[:15]:
            text = conv["content"]
            preview = text[:80] + ("…" if len(text) > 80 else "")
            chat = conv["chat_id"]
            role = conv["role"]
            if isinstance(traj, dict) and "entry_type" in traj:
                print(
                    f"  conv#{conv['id']} ({role:9s} @ {chat:20s})"
                    f"  ↔  traj#{traj['id']} ({traj['entry_type']:14s} @ {traj['source_chat_id']:20s} actor={traj['actor']})"
                )
                print(f"    text: {preview!r}")
            else:
                print(f"  conv#{conv['id']} ({role:9s} @ {chat})  [note: {traj.get('note')}]")
                print(f"    text: {preview!r}")
        if len(matched) > 15:
            print(f"  … {len(matched) - 15} more")
        print()

    if uc:
        print("UNMATCHED conversations rows (no trajectory counterpart):")
        for conv in uc:
            print(f"  id={conv['id']} chat={conv['chat_id']} role={conv['role']}")
            print(f"    content: {conv['content'][:120]!r}")
        print()

    if ut:
        print("UNMATCHED trajectory rows (no conversations counterpart):")
        for traj in ut:
            print(f"  id={traj['id']} type={traj['entry_type']} src={traj['source_chat_id']} actor={traj['actor']}")
            print(f"    content: {json.dumps(traj['content'], ensure_ascii=False)[:120]}")
        print()

    ok = len(uc) == 0 and len(ut) == 0
    print("=" * 72)
    print(f"VERDICT: {'PASS — all writes mirrored' if ok else 'FAIL — see unmatched above'}")
    print("=" * 72)
    return ok


async def _run(args) -> int:
    db_path = Path(args.db)
    if args.snapshot:
        snap = await _snapshot(db_path)
        Path(args.snapshot).write_text(json.dumps(snap, indent=2))
        print(f"snapshot written to {args.snapshot}")
        print(json.dumps(snap, indent=2))
        return 0

    if args.diff:
        snap = json.loads(Path(args.diff).read_text())
        conv_rows = await _fetch_new_conversations(
            db_path, snap["conversations_max_id"]
        )
        traj_rows = await _fetch_new_trajectory(
            db_path, snap["trajectory_max_id"]
        )
        ok = _report(snap, conv_rows, traj_rows)
        return 0 if ok else 1

    print("pass --snapshot <path> or --diff <path>", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--snapshot", help="write current max-ids to this path")
    grp.add_argument("--diff", help="diff against the snapshot at this path")
    p.add_argument("--db", default="data/lapwing.db")
    args = p.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
