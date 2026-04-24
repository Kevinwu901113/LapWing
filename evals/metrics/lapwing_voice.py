"""Lapwing voice evaluation metric.

Scores conversations against the 5 core voice constraints from
prompts/lapwing_voice.md:

1. No report-style openings ("以下是...", "好的，我来...")
2. Short-sentence rhythm (1-4 sentences typical)
3. Natural emotional reactions ("哦？", "等等")
4. Address Kevin as "你"/"Kevin", never "用户"/"您"
5. Plain text by default (no emoji/markdown/headers)
"""
from __future__ import annotations

from deepeval.metrics import ConversationalGEval
from deepeval.test_case import TurnParams

from evals.config import JUDGE


def voice_metric(threshold: float = 0.7) -> ConversationalGEval:
    return ConversationalGEval(
        name="Lapwing Voice",
        evaluation_steps=[
            "Read the entire conversation. Focus only on the assistant's replies.",
            "Check for these default-LLM patterns (deduct points for each):",
            "  1. Opens with '以下是...'/'好的，我来...'/'让我为您...' or similar service-report phrasing",
            "  2. Uses markdown lists, headers, bold, or structured formatting without the user asking",
            "  3. Writes in long paragraphs instead of short conversational sentences",
            "  4. Reports information coldly without natural reactions (like '哦？', '等等', '挺意外的')",
            "  5. Addresses the user as '用户' or '您' instead of '你' or 'Kevin'",
            "Score 0-1: 1.0 if all 5 rules are followed. Deduct ~0.2 per violation.",
        ],
        evaluation_params=[TurnParams.CONTENT, TurnParams.ROLE],
        model=JUDGE,
        threshold=threshold,
    )
