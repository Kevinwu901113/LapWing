"""TEMPORARY observation-only hallucination patch — Step 1 → Step 5.

Blueprint v2.0 §5 isolation clause: until the structural fix (trajectory +
commitment pipeline) lands in Step 5, records ``LLM_HALLUCINATION_SUSPECTED``
when a reply text contains one of a small set of hallucination phrases AND
the current iteration had zero tool calls — the MiniMax failure mode where
the model claims prior work it never did.

Pure observability: does NOT intercept the reply. Purely leaves a trail in
mutation_log.db for later post-mortem.

**Removal schedule**: this whole file, the
``MutationType.LLM_HALLUCINATION_SUSPECTED`` enum entry, and every call site
must be deleted by Step 5. Tracked in ``cleanup_report_step1.md`` debt
registry.
"""

from __future__ import annotations

import logging

from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    current_chat_id,
    current_iteration_id,
)

logger = logging.getLogger("lapwing.logging.hallucination_patch")


# The strict list: every occurrence is treated as a hallucination signal.
HALLUCINATION_PHRASES_STRICT: tuple[str, ...] = (
    "我刚才走神了",
    "刚一直在忙",
    "一直在看",
    "我刚才一直在",
)

# The soft list: easily false-positives on quoted text or "只是 X 了一下" style
# phrasing, so each match gets a ±5-char context check before counting.
HALLUCINATION_PHRASES_SOFT: tuple[str, ...] = (
    "在看了",
    "查到了",
)

# If any of these appear within 5 chars of a soft-phrase occurrence, we
# treat it as quoted / negated and ignore.
_SOFT_DISAMBIGUATORS: tuple[str, ...] = (
    '"', "'", "“", "”", "‘", "’", "「", "」", "『", "』",
    "只是",
)


def _soft_phrase_is_genuine(text: str, phrase: str) -> bool:
    """True if at least one occurrence of ``phrase`` in ``text`` looks like a
    genuine hallucination claim (not quoted, not prefixed by a negation)."""
    idx = text.find(phrase)
    while idx != -1:
        window_start = max(0, idx - 5)
        window_end = min(len(text), idx + len(phrase) + 5)
        window = text[window_start:window_end]
        if not any(d in window for d in _SOFT_DISAMBIGUATORS):
            return True
        idx = text.find(phrase, idx + len(phrase))
    return False


def _match_phrase(text: str) -> str | None:
    for phrase in HALLUCINATION_PHRASES_STRICT:
        if phrase in text:
            return phrase
    for phrase in HALLUCINATION_PHRASES_SOFT:
        if phrase in text and _soft_phrase_is_genuine(text, phrase):
            return phrase
    return None


async def check_and_record(
    reply_text: str,
    mutation_log: StateMutationLog | None,
) -> str | None:
    """Observation-only check. Returns the matched phrase if recorded, else None.

    Must be called while the iteration_context is still bound (so
    ``current_iteration_id()`` resolves). Safe to call from anywhere —
    failures are swallowed.
    """
    if not reply_text or mutation_log is None:
        return None
    iteration_id = current_iteration_id()
    if iteration_id is None:
        return None

    matched = _match_phrase(reply_text)
    if matched is None:
        return None

    try:
        rows = await mutation_log.query_by_iteration(iteration_id)
    except Exception:
        logger.warning("hallucination check: query_by_iteration failed", exc_info=True)
        return None

    tool_called_count = sum(
        1 for r in rows if r.event_type == MutationType.TOOL_CALLED.value
    )
    if tool_called_count > 0:
        return None

    try:
        await mutation_log.record(
            MutationType.LLM_HALLUCINATION_SUSPECTED,
            {
                "matched_phrase": matched,
                "reply_text_preview": reply_text[:500],
                "tool_calls_in_iteration": 0,
            },
            iteration_id=iteration_id,
            chat_id=current_chat_id(),
        )
    except Exception:
        logger.warning("LLM_HALLUCINATION_SUSPECTED record failed", exc_info=True)
    return matched
