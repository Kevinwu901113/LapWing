"""Fixed goldens runner.

Loads pre-authored conversation examples and evaluates them against
the metric suite. These conversations represent "known-good" Lapwing
behavior — the gold standard for voice and persona quality.
"""
from __future__ import annotations

from deepeval.test_case import ConversationalTestCase

from evals.goldens import load_fixed_goldens


def get_fixed_cases() -> list[ConversationalTestCase]:
    return load_fixed_goldens()
