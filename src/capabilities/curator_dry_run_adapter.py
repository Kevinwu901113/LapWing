"""Concrete CuratorDryRunObserver that calls ExperienceCurator in dry-run mode.

Converts sanitized summary dict → TraceSummary → CuratorDecision (+ CuratedExperience)
→ CuratorDryRunResult.  Never persists, never proposes, never creates drafts.
Lives in src/capabilities/ because it depends on ExperienceCurator and TraceSummary.
Wired in container.py behind curator_dry_run_enabled flag.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("lapwing.capabilities.curator_dry_run_adapter")


class CuratorDryRunAdapter:
    """Concrete observer: runs ExperienceCurator.should_reflect + summarize on
    a sanitized summary dict, returning a CuratorDryRunResult as dict.

    Best-effort and failure-safe.  On any exception, logs a debug message
    and returns None.  Never mutates capability store/index/proposals.
    Never calls propose_capability.  Never persists.
    """

    async def capture(self, summary: dict[str, Any]) -> dict[str, Any] | None:
        """Run curator dry-run on a sanitized execution summary dict.

        Returns a CuratorDryRunResult serialized to dict, or None on failure.
        """
        try:
            from src.capabilities.trace_summary import TraceSummary
            from src.capabilities.curator import ExperienceCurator
            from src.core.execution_summary import CuratorDryRunResult

            # summary is already sanitized by TraceSummaryObserver; from_dict
            # applies a second pass (idempotent — _DROP_KEYS and _SECRET_PATTERNS
            # won't double-redact).
            trace = TraceSummary.from_dict(summary)

            curator = ExperienceCurator()
            decision = curator.should_reflect(trace)

            generalization_boundary = ""
            suggested_capability_type = "skill"
            suggested_triggers: list[str] = []
            suggested_tags: list[str] = []

            if decision.should_create:
                curated = curator.summarize(trace)
                generalization_boundary = curated.generalization_boundary
                suggested_capability_type = curated.recommended_capability_type
                suggested_triggers = list(curated.suggested_triggers)
                suggested_tags = list(curated.suggested_tags)

            result = CuratorDryRunResult(
                trace_id=trace.trace_id or "",
                should_create=decision.should_create,
                recommended_action=decision.recommended_action,
                confidence=decision.confidence,
                reasons=list(decision.reasons),
                risk_level=decision.risk_level,
                required_approval=decision.required_approval,
                generalization_boundary=generalization_boundary,
                suggested_capability_type=suggested_capability_type,
                suggested_triggers=suggested_triggers,
                suggested_tags=suggested_tags,
            )
            return result.to_dict()
        except Exception:
            logger.debug("Curator dry-run capture failed", exc_info=True)
            return None
