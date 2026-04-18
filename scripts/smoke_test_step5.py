"""Step 5 smoke test framework — manual verification.

NOT a pytest fixture. Manual scenario script for verifying the Step 5
behaviour against a real LLM. Spin up a full container, drive 5
canonical scenarios through (preferably via QQ adapter or simulated
QQ-like adapter), then compare TrajectoryStore + CommitmentStore +
StateMutationLog against expected shapes.

Run:
    python scripts/smoke_test_step5.py

What this does NOT do automatically:
- Verify text quality (does the model say things in voice?)
- Verify exact content (does the search return the right answer?)
- Drive timing-sensitive overdue scenarios end-to-end (set deadline=2
  and wait — the script frames it but expects manual observation).

What you MUST observe manually:
- Each tell_user delivery shows up as ONE QQ message
- A commit_promise call leaves a row in commitments table with the
  expected description + deadline
- A search-then-fulfill leaves both a tell_user TELL_USER trajectory
  entry and a COMMITMENT_STATUS_CHANGED mutation
- Overdue commitment shows up in the next inner-tick prompt with ⚠️
  prefix (inspect data/logs/mutations_<date>.log + the iteration's
  LLM_REQUEST payload to see the rendered system_prompt)
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

# Add project root for imports
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("smoke_step5")


SCENARIOS = [
    {
        "id": "S1",
        "name": "纯聊天 — 单 tell_user",
        "user_input": "在吗",
        "expected": [
            "single tell_user call",
            "no commit_promise",
            "trajectory contains TELL_USER entry with text matching reply",
        ],
    },
    {
        "id": "S2",
        "name": "搜索任务 — tell_user + commit + search + tell_user + fulfill",
        "user_input": "查一下道奇下一场比赛",
        "expected": [
            "tell_user 'etc.' before search",
            "commit_promise with description ~ '查道奇'",
            "research/web tool call",
            "tell_user with the result",
            "fulfill_promise on the commitment",
        ],
    },
    {
        "id": "S3",
        "name": "提醒登记 — tell_user + set_reminder",
        "user_input": "帮我记一下明天下午三点开会",
        "expected": [
            "tell_user acknowledging",
            "set_reminder tool call (existing reminder system)",
            "no commit_promise required (reminder ≠ promise)",
        ],
    },
    {
        "id": "S4",
        "name": "Inner tick — 等 3 分钟无消息",
        "user_input": None,
        "wait_seconds": 180,
        "expected": [
            "InnerTickEvent fires",
            "think_inner runs without forcing a tell_user",
            "if no overdue commitments: silent",
            "if overdue exists: model surfaces it (manual check trajectory)",
        ],
    },
    {
        "id": "S5",
        "name": "跨轮回忆 — '刚才说的那个比赛几点'",
        "user_input": "刚才说的那个比赛几点",
        "expected": [
            "tell_user with cached/recall content",
            "model uses TrajectoryWindow context to recall",
            "no false commitment created",
        ],
    },
]


def _print_scenario(scn: dict) -> None:
    print()
    print(f"━━ {scn['id']}: {scn['name']} ━━")
    if scn.get("user_input") is not None:
        print(f"  Input: {scn['user_input']!r}")
    if scn.get("wait_seconds"):
        print(f"  Wait:  {scn['wait_seconds']}s")
    print("  Expected behavior:")
    for e in scn["expected"]:
        print(f"    • {e}")


async def _print_state_summary() -> None:
    """Read commitments.db and dump open + overdue counts."""
    import aiosqlite

    db_path = ROOT / "data" / "lapwing.db"
    if not db_path.exists():
        print(f"\n(no DB at {db_path}; start the app first)")
        return

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM commitments GROUP BY status"
        ) as cur:
            rows = await cur.fetchall()
        print("\n━━ CommitmentStore counts ━━")
        if not rows:
            print("  (empty)")
        for status, count in rows:
            print(f"  {status}: {count}")

        async with db.execute(
            "SELECT COUNT(*) FROM commitments "
            "WHERE deadline IS NOT NULL AND deadline < ? "
            "AND status IN ('pending', 'in_progress')",
            (time.time(),),
        ) as cur:
            (overdue,) = await cur.fetchone()
        print(f"  overdue (open & past deadline): {overdue}")


async def main() -> None:
    print("Step 5 smoke test — scenarios listed below")
    print("This script frames the test plan; you drive Lapwing through")
    print("each scenario manually (QQ / Telegram / Desktop adapter) and")
    print("verify the expected behaviours.")
    print()
    print("After running each scenario, inspect:")
    print("  - data/lapwing.db (commitments table)")
    print("  - data/logs/mutations_<today>.log (TELL_USER, COMMITMENT_*)")
    print("  - The actual messages your client received")

    for scn in SCENARIOS:
        _print_scenario(scn)

    await _print_state_summary()

    print()
    print("Reminder: this is a smoke-test framework, not an automated")
    print("verifier. The true integration is the unit + integration suite.")


if __name__ == "__main__":
    asyncio.run(main())
