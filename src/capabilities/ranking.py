"""Deterministic capability candidate scoring.

Pure functions — no I/O, no LLM, no embeddings, no network.
Operates on dict rows (from CapabilityIndex or CapabilityStore).
"""

from __future__ import annotations

import json
from typing import Any

SCOPE_BOOST: dict[str, float] = {
    "session": 4.0,
    "workspace": 3.0,
    "user": 2.0,
    "global": 1.0,
}

MATURITY_BOOST: dict[str, float] = {
    "stable": 5.0,
    "testing": 3.0,
    "draft": 0.0,
    "repairing": -2.0,
    "broken": -10.0,
}

RISK_PENALTY: dict[str, float] = {
    "low": 0.0,
    "medium": -2.0,
    "high": -10.0,
}


def _parse_json_field(value: Any) -> list:
    """Parse a JSON string field, returning a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def keyword_score(candidate: dict, query: str) -> float:
    """Score a candidate against a query string using keyword matching."""
    if not query or not query.strip():
        return 0.0
    q = query.strip().lower()
    score = 0.0

    name = (candidate.get("name") or "").lower()
    if q in name:
        score += 10.0
    elif any(token in name for token in q.split()):
        score += 5.0

    triggers = _parse_json_field(candidate.get("triggers") or candidate.get("triggers_json"))
    for trigger in triggers:
        if q in (trigger or "").lower():
            score += 5.0
            break

    tags = _parse_json_field(candidate.get("tags") or candidate.get("tags_json"))
    for tag in tags:
        if q in (tag or "").lower():
            score += 4.0
            break

    description = (candidate.get("description") or "").lower()
    if q in description:
        score += 3.0
    elif any(token in description for token in q.split()):
        score += 1.0

    return score


def scope_boost(scope: str) -> float:
    return SCOPE_BOOST.get(scope, 0.0)


def maturity_boost(maturity: str) -> float:
    return MATURITY_BOOST.get(maturity, 0.0)


def risk_penalty(risk_level: str) -> float:
    return RISK_PENALTY.get(risk_level, 0.0)


def usage_boost(candidate: dict) -> float:
    usage = candidate.get("usage_count", 0) or 0
    success = candidate.get("success_count", 0) or 0
    if usage <= 0:
        return 0.0
    return min(success / max(usage, 1), 1.0) * 3.0


def recency_boost(candidate: dict) -> float:
    """Slight boost for recently updated candidates (0.0–1.0)."""
    updated = candidate.get("updated_at")
    if not updated:
        return 0.0
    return 0.5


def score_candidate(candidate: dict, query: str, *, _context: dict | None = None) -> float:
    """Compute a deterministic relevance score for a candidate.

    Returns a float where higher = more relevant. The absolute value has no
    intrinsic meaning — only relative ordering within a batch matters.
    """
    q = query or ""
    return (
        keyword_score(candidate, q)
        + scope_boost(str(candidate.get("scope", "global")))
        + maturity_boost(str(candidate.get("maturity", "draft")))
        + risk_penalty(str(candidate.get("risk_level", "low")))
        + usage_boost(candidate)
        + recency_boost(candidate)
    )


def rank_candidates(candidates: list[dict], query: str) -> list[dict]:
    """Sort candidates by score (highest first), then by name for determinism."""
    scored = [(c, score_candidate(c, query)) for c in candidates]
    scored.sort(key=lambda pair: (-pair[1], (pair[0].get("name") or "").lower()))
    return [c for c, _ in scored]
