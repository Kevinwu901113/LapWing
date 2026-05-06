"""Concrete AutoProposalObserver that persists proposals from curator dry-run decisions.

Converts sanitized summary dict + curator decision dict → CapabilityProposal
→ persist_proposal().  Never creates drafts, never updates indices, never
promotes.  Lives in src/capabilities/ because it depends on ExperienceCurator,
TraceSummary, and proposal persistence.
Wired in container.py behind auto_proposal_enabled flag.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("lapwing.capabilities.auto_proposal_adapter")

_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "create_skill_draft",
    "create_workflow_draft",
    "create_project_playbook_draft",
})


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


class AutoProposalAdapter:
    """Concrete observer: gates, dedup, rate-limit, then persist proposal files.

    Best-effort and failure-safe.  On any exception, logs a debug message
    and returns None.  Never mutates capability store/index/lifecycle.
    Never calls CapabilityStore.create_draft.  Never promotes.
    """

    def __init__(
        self,
        *,
        min_confidence: float = 0.75,
        allow_high_risk: bool = False,
        max_per_session: int = 3,
        dedupe_window_hours: int = 24,
        data_dir: str = "data/capabilities",
    ) -> None:
        self._min_confidence = min_confidence
        self._allow_high_risk = allow_high_risk
        self._max_per_session = max_per_session
        self._dedupe_window_hours = dedupe_window_hours
        self._data_dir = data_dir
        self._proposal_count = 0

    async def capture(
        self,
        summary: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Attempt auto-proposal persistence from summary + curator decision.

        Returns AutoProposalResult serialized to dict, or None on failure.
        """
        try:
            from src.core.execution_summary import AutoProposalResult
            from src.capabilities.trace_summary import TraceSummary
            from src.capabilities.curator import ExperienceCurator
            from src.capabilities.proposal import persist_proposal, list_proposals

            trace_id = str(decision.get("trace_id", ""))

            # ── Gate 1: should_create ──────────────────────────────────
            if not decision.get("should_create"):
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason="should_create false",
                    reason="curator decided not to create",
                ).to_dict()

            # ── Gate 2: recommended_action must be allowed ─────────────
            action = str(decision.get("recommended_action", ""))
            if action not in _ALLOWED_ACTIONS:
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason=f"unsupported recommended_action: {action}",
                    reason="action not in allowed set",
                ).to_dict()

            # ── Gate 3: confidence threshold ───────────────────────────
            confidence = float(decision.get("confidence", 0))
            if confidence < self._min_confidence:
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason="confidence below threshold",
                    reason=f"confidence {confidence} < {self._min_confidence}",
                    confidence=confidence,
                ).to_dict()

            # ── Gate 4: risk level ─────────────────────────────────────
            risk_level = str(decision.get("risk_level", "low"))
            if risk_level == "high" and not self._allow_high_risk:
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason="high risk not allowed",
                    reason="allow_high_risk_auto_proposal is false",
                    risk_level=risk_level,
                ).to_dict()

            # ── Gate 5: generalization_boundary ────────────────────────
            boundary = str(decision.get("generalization_boundary", ""))
            if not boundary.strip():
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason="missing generalization_boundary",
                    reason="generalization_boundary is empty",
                ).to_dict()

            # ── Gate 6: verification for non-low risk ──────────────────
            if risk_level in ("medium", "high"):
                verification = summary.get("verification") or []
                if not verification:
                    return AutoProposalResult(
                        trace_id=trace_id,
                        attempted=True,
                        skipped_reason="missing verification for non-low risk",
                        reason=f"risk_level={risk_level} requires verification",
                        risk_level=risk_level,
                    ).to_dict()

            # ── Gate 7: secrets check ──────────────────────────────────
            # Double-check sanitized summary contains no unredacted secrets.
            if _summary_contains_unredacted_secrets(summary):
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason="unredacted secrets in summary",
                    reason="summary contains potential secrets",
                ).to_dict()

            # ── Gate 8: rate limit ─────────────────────────────────────
            if self._proposal_count >= self._max_per_session:
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason="rate_limited",
                    reason=f"max {self._max_per_session} auto proposals per session",
                ).to_dict()

            # ── Build the proposal ─────────────────────────────────────
            trace = TraceSummary.from_dict(summary)
            curator = ExperienceCurator()
            curated = curator.summarize(trace)

            proposal = curator.propose_capability(
                curated,
                risk_level=risk_level,
            )

            # ── Gate 9: dedup ──────────────────────────────────────────
            dup_reason = self._check_duplicate(proposal, trace_id)
            if dup_reason:
                return AutoProposalResult(
                    trace_id=trace_id,
                    attempted=True,
                    skipped_reason=f"duplicate: {dup_reason}",
                    reason="duplicate proposal within dedupe window",
                    confidence=confidence,
                    risk_level=risk_level,
                    proposed_capability_id=proposal.proposed_capability_id,
                ).to_dict()

            # ── Persist ────────────────────────────────────────────────
            persist_proposal(proposal, trace, self._data_dir)
            self._proposal_count += 1

            return AutoProposalResult(
                trace_id=trace_id,
                attempted=True,
                persisted=True,
                proposal_id=proposal.proposal_id,
                proposed_capability_id=proposal.proposed_capability_id,
                reason=f"proposal {proposal.proposal_id} persisted",
                confidence=confidence,
                risk_level=risk_level,
                required_approval=bool(decision.get("required_approval", False)),
            ).to_dict()

        except Exception:
            logger.debug("Auto-proposal capture failed", exc_info=True)
            return None

    def _check_duplicate(
        self,
        proposal: Any,
        trace_id: str,
    ) -> str | None:
        """Check if a similar proposal exists within the dedupe window.

        Returns a reason string if duplicate found, None otherwise.
        """
        try:
            from src.capabilities.proposal import list_proposals
        except Exception:
            return None

        existing = list_proposals(self._data_dir)
        if not existing:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._dedupe_window_hours)
        norm_name = _normalize_name(proposal.name)
        norm_scope = proposal.scope.lower().strip()

        for ep in existing:
            # Skip proposals outside the dedupe window.
            try:
                created = datetime.fromisoformat(ep.created_at)
            except (ValueError, TypeError):
                continue
            if created < cutoff:
                continue

            # Same source trace.
            if trace_id and ep.source_trace_id == trace_id:
                return "same source_trace_id"

            # Same proposed capability id.
            if ep.proposed_capability_id == proposal.proposed_capability_id:
                return "same proposed_capability_id"

            # Same normalized name + scope.
            if _normalize_name(ep.name) == norm_name and ep.scope.lower().strip() == norm_scope:
                return "same normalized name + scope"

        return None


# ── Internal helpers ────────────────────────────────────────────────────────

import re as _re

# Patterns that indicate unredacted secrets.  Each pattern must NOT match
# the already-redacted form (e.g. "sk-<REDACTED>").
_UNREDACTED_SECRET_PATTERNS: list[_re.Pattern] = [
    _re.compile(r'sk-[a-zA-Z0-9-]{20,}'),
    _re.compile(r'API[_-]?KEY\s*=\s*["\' ]?[^\s"\'\n<]{4,}', _re.IGNORECASE),
    _re.compile(r'Authorization:\s*Bearer\s+[^\s<]{10,}', _re.IGNORECASE),
    _re.compile(r'password\s*=\s*["\' ]?[^\s"\'\n<]{3,}', _re.IGNORECASE),
    _re.compile(
        r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?(?:[A-Za-z0-9+/]{40,})[\s\S]*?-----END',
        _re.DOTALL,
    ),
]


def _summary_contains_unredacted_secrets(summary: dict[str, Any]) -> bool:
    """Double-check that the sanitized summary contains no unredacted secrets.

    The TraceSummary sanitizer already redacts secrets — this is a
    defense-in-depth check before persistence.
    """
    import json as _json

    text = _json.dumps(summary, ensure_ascii=False, default=str)
    for pattern in _UNREDACTED_SECRET_PATTERNS:
        if pattern.search(text):
            return True
    return False
