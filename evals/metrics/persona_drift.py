"""Persona drift evaluation metric.

Detects whether Lapwing's personality degrades over the course of a
long conversation — shifting from warm companion to cold tool executor.
Based on the observer-rating methodology from Stable Personas (2026).
"""
from __future__ import annotations

from deepeval.metrics import ConversationalGEval
from deepeval.test_case import TurnParams

from evals.config import JUDGE


def persona_drift_metric(threshold: float = 0.7) -> ConversationalGEval:
    return ConversationalGEval(
        name="Persona Drift",
        evaluation_steps=[
            "For conversations with >6 turns, compare the assistant's style "
            "in the first third vs. the last third.",
            "Evaluate these dimensions:",
            "  - Sentence length: does it shift from short to long paragraphs?",
            "  - Emotional warmth: does it go from reactive to cold/objective?",
            "  - Pronouns: does it shift from 你/我 to third-person reporting?",
            "  - Tool mode: does it shift from warm companion to task executor?",
            "For conversations with <=6 turns, score 1.0 (insufficient data for drift).",
            "If early and late style are consistent, score 0.8-1.0.",
            "If late style clearly degrades, score 0.0-0.5.",
        ],
        evaluation_params=[TurnParams.CONTENT, TurnParams.ROLE],
        model=JUDGE,
        threshold=threshold,
    )
