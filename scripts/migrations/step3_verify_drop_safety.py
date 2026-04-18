"""Step 3 M2.e: verify it is safe to drop the legacy conversations table.

Opens data/lapwing.db and classifies every row in ``conversations``:

    X — row has a semantic match in the ``trajectory`` table. Safe to
        drop; the payload lives on in the post-Step-2 truth source.
    Y — row id is in the Step 2 discard list (ids explicitly known to
        have been discarded during Step 2's migration for reasons
        documented in docs/refactor_v2/step2_data_audit_notes.md).
    Z — neither: payload exists in conversations but not in trajectory
        and not on the discard list. Z > 0 blocks the DROP and forces
        a documented resolution.

Outputs a JSON report to stdout summarising the counts and, when
``--verbose`` is passed, listing each Z row's id + chat_id +
timestamp + content-prefix so a resolution memo can cite them.

Matching criteria (conversations row ↔ trajectory entry):

    same chat_id AND (conversations.role → expected actor/entry_type)
    AND the trajectory content JSON's ``text`` field equals the
    conversations row's content.

Timestamps are NOT part of the match — Step 2 migrated chat rows into
trajectory with fresh wall-clock times, so timestamp equality is not a
useful signal.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Step 2 discard list — conversation.id values explicitly discarded by
# Step 2's migration with Kevin's sign-off, as recorded in the Step 2
# cleanup report §5. Kept inline so this script is self-contained.
_STEP2_DISCARD_IDS: frozenset[int] = frozenset({1728, 1752, 1878})


_ROLE_TO_EXPECTED: dict[str, tuple[str, ...]] = {
    # conversations.role → candidate (trajectory.entry_type) tuples
    "user": ("user_message",),
    "assistant": ("assistant_text", "tell_user"),
    "system": ("inner_thought",),
}


# Chat-id remap introduced in Step 2e/2i: the old consciousness loop
# wrote with chat_id="__consciousness__"; Step 2 trajectory rows use
# source_chat_id="__inner__" (and entry_type=inner_thought regardless of
# role). Apply the same remap here so the verifier matches rows across
# the rename boundary.
_CHAT_ID_REMAP: dict[str, str] = {
    "__consciousness__": "__inner__",
}
_INNER_CHAT_IDS: frozenset[str] = frozenset({"__inner__"})


@dataclass
class UnmatchedRow:
    id: int
    chat_id: str
    role: str
    timestamp: str
    content_prefix: str


def _load_trajectory_index(db: sqlite3.Connection) -> dict[tuple[str, str, str], int]:
    """Build ``(chat_id, trajectory_entry_type, text) → count`` for matching.

    Returns an index that ``_row_matches`` will consult; we decrement
    counts on match so two identical conversations rows don't both
    satisfy against a single trajectory row.
    """
    index: dict[tuple[str, str, str], int] = {}
    cur = db.cursor()
    cur.execute(
        "SELECT source_chat_id, entry_type, content_json FROM trajectory"
    )
    for chat_id, entry_type, content_json in cur:
        try:
            content = json.loads(content_json) if content_json else {}
        except (json.JSONDecodeError, TypeError):
            continue
        text: str | None = None
        if entry_type == "tell_user":
            msgs = content.get("messages")
            if isinstance(msgs, list) and msgs:
                text = "\n".join(str(m) for m in msgs)
            else:
                t = content.get("text")
                if isinstance(t, str):
                    text = t
        else:
            t = content.get("text")
            if isinstance(t, str):
                text = t
        if text is None:
            continue
        key = (chat_id, entry_type, text)
        index[key] = index.get(key, 0) + 1
    return index


def _row_matches(
    index: dict[tuple[str, str, str], int], chat_id: str, role: str, content: str
) -> bool:
    # Apply Step 2e/2i remap so __consciousness__ rows line up with the
    # __inner__ trajectory entries they became during the migration.
    remapped = _CHAT_ID_REMAP.get(chat_id, chat_id)
    if remapped in _INNER_CHAT_IDS:
        # Inner-loop rows land on inner_thought regardless of role
        # (user → system actor; assistant → lapwing actor). The actor
        # isn't indexed here — we only match on (chat, entry_type, text).
        candidates = ("inner_thought",)
    else:
        candidates = _ROLE_TO_EXPECTED.get(role, ())
    for entry_type in candidates:
        key = (remapped, entry_type, content)
        if index.get(key, 0) > 0:
            index[key] -= 1
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="data/lapwing.db",
        help="Path to the lapwing SQLite database (default: data/lapwing.db)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="List every Z (unmatched) row in the report",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"error: {db_path} does not exist", file=sys.stderr)
        return 2

    db = sqlite3.connect(db_path)
    try:
        cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM conversations")
        total = cur.fetchone()[0]

        trajectory_index = _load_trajectory_index(db)

        x = 0
        y = 0
        unmatched: list[UnmatchedRow] = []

        cur.execute(
            "SELECT id, chat_id, role, content, timestamp FROM conversations "
            "ORDER BY id"
        )
        for row_id, chat_id, role, content, timestamp in cur:
            if _row_matches(trajectory_index, chat_id or "", role or "", content or ""):
                x += 1
            elif row_id in _STEP2_DISCARD_IDS:
                y += 1
            else:
                unmatched.append(
                    UnmatchedRow(
                        id=row_id,
                        chat_id=chat_id or "",
                        role=role or "",
                        timestamp=timestamp or "",
                        content_prefix=(content or "")[:120],
                    )
                )
        z = len(unmatched)

        report: dict[str, object] = {
            "db_path": str(db_path.resolve()),
            "conversations_total": total,
            "matched_in_trajectory_x": x,
            "in_step2_discard_list_y": y,
            "unmatched_z": z,
            "step2_discard_ids": sorted(_STEP2_DISCARD_IDS),
        }
        if args.verbose:
            report["unmatched_rows"] = [r.__dict__ for r in unmatched]

        print(json.dumps(report, ensure_ascii=False, indent=2))
        # Non-zero exit when Z > 0 so automation notices.
        return 1 if z > 0 else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
