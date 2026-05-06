"""Concrete ExecutionSummaryObserver that converts TaskEndContext → TraceSummary.

Uses Phase 5A TraceSummary.from_dict() + sanitize() for secrets redaction
and CoT stripping.  Lives in src/capabilities/ because it depends on
TraceSummary.  Wired in container.py behind the execution_summary_enabled flag.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.execution_summary import TaskEndContext

logger = logging.getLogger("lapwing.capabilities.trace_summary_adapter")


class TraceSummaryObserver:
    """Concrete observer: converts TaskEndContext → sanitized TraceSummary dict.

    Best-effort and failure-safe.  On any exception, logs a debug message
    and returns None.  Never mutates capability store/index/proposals.
    Never calls the curator.  Never persists.
    """

    async def capture(self, context: TaskEndContext) -> dict[str, Any] | None:
        """Convert TaskEndContext to a sanitized TraceSummary dict.

        Returns a dict suitable for debug attachment or later manual
        curation.  Does NOT persist to disk, call curator, or create
        proposals.
        """
        try:
            from src.capabilities.trace_summary import TraceSummary

            d = context.to_dict()
            # TraceSummary.from_dict expects 'context' for additional context.
            d.setdefault("context", None)
            d.setdefault("existing_capability_id", None)

            trace = TraceSummary.from_dict(d)
            sanitized = trace.sanitize()
            return sanitized.to_dict()
        except Exception:
            logger.debug("Execution summary capture failed", exc_info=True)
            return None
