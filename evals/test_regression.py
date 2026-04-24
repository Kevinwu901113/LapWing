"""Regression suite entry point.

Run:
    pytest evals/ --run-evals                 # all evals
    pytest evals/ --run-evals -k fixed        # fixed goldens only
    pytest evals/ --run-evals -k simulated    # simulated scenarios only
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from deepeval import evaluate
from deepeval.metrics import (
    KnowledgeRetentionMetric,
    RoleAdherenceMetric,
)

from evals.config import JUDGE
from evals.metrics.lapwing_voice import voice_metric
from evals.metrics.persona_drift import persona_drift_metric
from evals.runners.fixed import get_fixed_cases

REPORTS_DIR = Path(__file__).parent / "reports"


def _metric_suite():
    return [
        voice_metric(threshold=0.7),
        persona_drift_metric(threshold=0.7),
        RoleAdherenceMetric(threshold=0.7, model=JUDGE),
        KnowledgeRetentionMetric(threshold=0.7, model=JUDGE),
    ]


def _save_report(suite_name: str, result) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"{ts}_{suite_name}.json"

    serialized = {
        "suite": suite_name,
        "timestamp": ts,
        "test_cases": [],
    }
    if hasattr(result, "test_results"):
        for tr in result.test_results:
            case_data = {
                "name": getattr(tr, "name", ""),
                "success": getattr(tr, "success", None),
                "metrics": [],
            }
            for md in getattr(tr, "metrics_data", []):
                case_data["metrics"].append({
                    "name": getattr(md, "name", ""),
                    "score": getattr(md, "score", None),
                    "threshold": getattr(md, "threshold", None),
                    "success": getattr(md, "success", None),
                    "reason": getattr(md, "reason", None),
                })
            serialized["test_cases"].append(case_data)

    path.write_text(
        json.dumps(serialized, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n[eval] report saved to {path}")


@pytest.mark.eval
def test_fixed_goldens():
    """Evaluate pre-authored golden conversations against the metric suite."""
    cases = get_fixed_cases()
    assert len(cases) > 0, "No fixed goldens found in evals/goldens/fixed/"
    result = evaluate(test_cases=cases, metrics=_metric_suite())
    _save_report("fixed", result)


@pytest.mark.eval
async def test_simulated():
    """Generate and evaluate simulated conversations from scenarios."""
    from evals.runners.simulated import simulate_all

    cases = await simulate_all()
    assert len(cases) > 0, "No scenarios found in evals/goldens/scenarios/"
    result = evaluate(test_cases=cases, metrics=_metric_suite())
    _save_report("simulated", result)
