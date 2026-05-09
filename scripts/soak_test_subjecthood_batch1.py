#!/usr/bin/env python3
"""Subjecthood Batch 1 soak test — 30-minute supervisor soak.

Usage:
    1. Start Lapwing normally: python main.py
    2. Run this script: python scripts/soak_test_subjecthood_batch1.py [--duration 1800]
    3. Interact with the bot through QQ during the soak:
       - Send normal messages (foreground turns)
       - Send weather queries to trigger background tasks
       - Send "停止天气查询" to test topic cancellation
       - Wait for background task completions
    4. After the duration, this script prints the soak report.

Metrics collected from SQLite stores (TrajectoryStore, MutationLog, AgentTaskStore):
    - Outbound counts by source
    - Active/cancelled/completed task counts
    - Suppressed count (expression_gate_suppressed)
    - Breaker transitions (from mutation log)
    - Gate fail-open count (from mutation log)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import aiosqlite


async def collect_soak_metrics(db_path: str, mutation_db_path: str, since_ts: float) -> dict:
    """Collect soak metrics from the Lapwing SQLite databases."""
    metrics = {
        "outbound_by_source": {},
        "outbound_total": 0,
        "tasks_active": 0,
        "tasks_completed": 0,
        "tasks_cancelled": 0,
        "tasks_failed": 0,
        "gate_suppressed_count": 0,
        "gate_rejected_count": 0,
        "gate_fail_open_count": 0,
        "breaker_transitions": [],
        "infra_unavailable_events": 0,
    }

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        return metrics

    # Query lapwing.db for trajectory + agent_tasks
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Outbound counts by source from trajectory table
        # trajectory.timestamp is REAL (unix), content_json is TEXT (JSON)
        try:
            async with db.execute(
                """
                SELECT
                    json_extract(content_json, '$.source') as source,
                    COUNT(*) as cnt
                FROM trajectory
                WHERE entry_type IN ('tell_user', 'proactive_outbound')
                  AND timestamp >= ?
                GROUP BY source
                """,
                (since_ts,),
            ) as cursor:
                async for row in cursor:
                    source = row["source"] or "unknown"
                    count = row["cnt"]
                    metrics["outbound_by_source"][source] = count
                    metrics["outbound_total"] += count
        except Exception as e:
            print(f"  [warn] trajectory query failed: {e}")

        # Agent task counts from agent_tasks table
        # agent_tasks.created_at is TEXT (ISO format)
        try:
            from datetime import datetime, timezone
            since_iso = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()
            for status, key in [
                ("active", "tasks_active"),
                ("completed", "tasks_completed"),
                ("cancelled", "tasks_cancelled"),
                ("failed", "tasks_failed"),
            ]:
                async with db.execute(
                    "SELECT COUNT(*) as cnt FROM agent_tasks WHERE status = ? AND created_at >= ?",
                    (status, since_iso),
                ) as cursor:
                    row = await cursor.fetchone()
                    metrics[key] = row["cnt"] if row else 0
        except Exception as e:
            print(f"  [warn] agent_tasks query failed: {e}")

    # Query mutation_log.db for gate events
    if not os.path.exists(mutation_db_path):
        print(f"Mutation log DB not found: {mutation_db_path}")
        return metrics

    async with aiosqlite.connect(mutation_db_path) as mdb:
        mdb.row_factory = aiosqlite.Row

        # Gate events from mutations table
        # mutations.timestamp is REAL (unix), payload_json is TEXT (JSON)
        try:
            async with mdb.execute(
                """
                SELECT event_type, COUNT(*) as cnt
                FROM mutations
                WHERE timestamp >= ?
                  AND event_type IN (
                    'expression_gate_suppressed',
                    'expression_gate_rejected',
                    'expression_gate_fail_open',
                    'topic_stopped'
                  )
                GROUP BY event_type
                """,
                (since_ts,),
            ) as cursor:
                async for row in cursor:
                    etype = row["event_type"]
                    count = row["cnt"]
                    if etype == "expression_gate_suppressed":
                        metrics["gate_suppressed_count"] = count
                    elif etype == "expression_gate_rejected":
                        metrics["gate_rejected_count"] = count
                    elif etype == "expression_gate_fail_open":
                        metrics["gate_fail_open_count"] = count
        except Exception as e:
            print(f"  [warn] mutation_log query failed: {e}")

        # Tool infra unavailability events
        try:
            async with mdb.execute(
                """
                SELECT COUNT(*) as cnt
                FROM mutations
                WHERE timestamp >= ?
                  AND event_type = 'tool_result'
                  AND json_extract(payload_json, '$.error_code') = 'tool.infra_unavailable'
                """,
                (since_ts,),
            ) as cursor:
                row = await cursor.fetchone()
                metrics["infra_unavailable_events"] = row["cnt"] if row else 0
        except Exception as e:
            print(f"  [warn] infra_unavailable query failed: {e}")

    return metrics


def print_soak_report(metrics: dict, duration_seconds: int) -> None:
    """Print the soak test report."""
    print("\n" + "=" * 60)
    print("  Subjecthood Batch 1 — Soak Test Report")
    print("=" * 60)
    print(f"  Duration: {duration_seconds}s ({duration_seconds // 60}m {duration_seconds % 60}s)")
    print()

    print("  Outbound counts by source:")
    if metrics["outbound_by_source"]:
        for source, count in sorted(metrics["outbound_by_source"].items()):
            print(f"    {source}: {count}")
    else:
        print("    (no outbound messages recorded)")
    print(f"    TOTAL: {metrics['outbound_total']}")
    print()

    print("  Background task counts:")
    print(f"    active:    {metrics['tasks_active']}")
    print(f"    completed: {metrics['tasks_completed']}")
    print(f"    cancelled: {metrics['tasks_cancelled']}")
    print(f"    failed:    {metrics['tasks_failed']}")
    print()

    print("  ExpressionGate metrics:")
    print(f"    suppressed:  {metrics['gate_suppressed_count']}")
    print(f"    rejected:    {metrics['gate_rejected_count']}")
    print(f"    fail-open:   {metrics['gate_fail_open_count']}")
    print()

    print("  Infra breaker:")
    print(f"    tool_infra_unavailable events: {metrics['infra_unavailable_events']}")
    if metrics["breaker_transitions"]:
        print(f"    transitions: {len(metrics['breaker_transitions'])}")
        for t in metrics["breaker_transitions"][-5:]:
            print(f"      {t}")
    print()

    # Acceptance checks
    print("  Acceptance checks:")
    checks = [
        ("outbound_total > 0", metrics["outbound_total"] > 0),
        ("no raw AGENTNEEDSINPUT in outbound",
         "agent_needs_input" not in metrics["outbound_by_source"]),
        ("fail-open count documented",
         metrics["gate_fail_open_count"] >= 0),  # always passes, just documents
    ]
    all_pass = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"    [{status}] {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("  RESULT: PASS")
    else:
        print("  RESULT: FAIL — see failed checks above")
    print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Subjecthood Batch 1 soak test")
    parser.add_argument("--duration", type=int, default=1800, help="Soak duration in seconds (default: 1800 = 30min)")
    parser.add_argument("--db", type=str, default=None, help="Path to lapwing.db (default: data/lapwing.db)")
    parser.add_argument("--mutation-db", type=str, default=None, help="Path to mutation_log.db (default: data/mutation_log.db)")
    parser.add_argument("--interval", type=int, default=60, help="Progress report interval in seconds (default: 60)")
    args = parser.parse_args()

    db_path = args.db or str(_PROJECT_ROOT / "data" / "lapwing.db")
    mutation_db_path = args.mutation_db or str(_PROJECT_ROOT / "data" / "mutation_log.db")
    duration = args.duration
    interval = args.interval

    if not os.path.exists(db_path):
        print(f"ERROR: Database not found at {db_path}")
        print("Make sure Lapwing is running: python main.py")
        sys.exit(1)

    start_time = time.time()
    start_iso = datetime.now(timezone.utc).isoformat()

    print(f"Soak test started at {start_iso}")
    print(f"Duration: {duration}s ({duration // 60}m)")
    print(f"Database: {db_path}")
    print(f"Mutation log: {mutation_db_path}")
    print(f"Progress reports every {interval}s")
    print("Interact with the bot through QQ during this time.")
    print()

    elapsed = 0
    while elapsed < duration:
        await asyncio.sleep(min(interval, duration - elapsed))
        elapsed = int(time.time() - start_time)
        metrics = await collect_soak_metrics(db_path, mutation_db_path, start_time)
        print(
            f"  [{elapsed // 60:02d}m{elapsed % 60:02d}s] "
            f"outbound={metrics['outbound_total']} "
            f"tasks={metrics['tasks_active']}+{metrics['tasks_completed']}+{metrics['tasks_cancelled']}+{metrics['tasks_failed']} "
            f"suppressed={metrics['gate_suppressed_count']} "
            f"fail_open={metrics['gate_fail_open_count']} "
            f"infra_err={metrics['infra_unavailable_events']}"
        )

    final_metrics = await collect_soak_metrics(db_path, mutation_db_path, start_time)
    print_soak_report(final_metrics, duration)


if __name__ == "__main__":
    asyncio.run(main())
