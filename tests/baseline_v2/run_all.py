"""Run all baseline v2 cases; print summary."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import ALL_CASES
from common import RESULTS_DIR, run_case


async def main(only: list[str] | None = None, n: int = 10) -> None:
    summaries: list[dict] = []
    for cfg in ALL_CASES:
        if only and cfg.name not in only:
            continue
        print(f"\n=== {cfg.name} ({n} iters) ===", flush=True)
        t0 = time.time()
        summary = await run_case(cfg, n=n)
        elapsed = time.time() - t0
        summary["wall_sec"] = round(elapsed, 1)
        summaries.append(summary)
        print(f"→ {summary['pass_rate']} passed in {elapsed:.1f}s")
    (RESULTS_DIR / "_index.json").write_text(
        json.dumps(
            [{"case": s["case"], "pass_rate": s["pass_rate"], "wall_sec": s.get("wall_sec")} for s in summaries],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n=== SUMMARY ===")
    for s in summaries:
        print(f"  {s['case']}: {s['pass_rate']}  ({s.get('wall_sec', '?')}s)")


if __name__ == "__main__":
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    asyncio.run(main(only=only))
