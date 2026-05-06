"""CapabilityRetriever — deterministic progressive-disclosure retrieval.

Retrieves a small set of relevant capability summaries for injection into
StateView. Never executes capabilities, calls LLMs, or accesses the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.capabilities.ranking import score_candidate

if TYPE_CHECKING:
    from src.capabilities.document import CapabilityDocument
    from src.capabilities.index import CapabilityIndex
    from src.capabilities.policy import CapabilityPolicy
    from src.capabilities.store import CapabilityStore

logger = logging.getLogger(__name__)

SCOPE_PRECEDENCE_ORDER = ["session", "workspace", "user", "global"]

DEFAULT_INCLUDE_MATURITIES = {"stable", "testing"}
DEFAULT_INCLUDE_RISK_LEVELS = {"low", "medium"}
DEFAULT_MAX_RESULTS = 5


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RetrievalContext:
    """Filters and constraints for a retrieval call."""

    user_task: str = ""
    current_scope: str | None = None
    allowed_scopes: list[str] = field(default_factory=list)
    runtime_profile_name: str | None = None
    runtime_capabilities: set[str] = field(default_factory=set)
    available_tools: set[str] = field(default_factory=set)
    max_results: int = DEFAULT_MAX_RESULTS
    include_maturity: list[str] = field(
        default_factory=lambda: sorted(DEFAULT_INCLUDE_MATURITIES)
    )
    include_risk_levels: list[str] = field(
        default_factory=lambda: sorted(DEFAULT_INCLUDE_RISK_LEVELS)
    )
    include_draft: bool = False
    include_high_risk: bool = False
    include_disabled: bool = False
    include_archived: bool = False
    include_quarantined: bool = False
    sensitive_contexts: set[str] = field(default_factory=set)
    approved_sensitive_contexts: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class CapabilitySummary:
    """Compact capability summary for StateView progressive disclosure.

    Never contains full CAPABILITY.md body, procedure, script contents,
    traces, evals, or version contents.
    """

    id: str
    name: str
    description: str
    type: str
    scope: str
    maturity: str
    status: str
    risk_level: str
    trust_required: str = "developer"
    triggers: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    do_not_apply_when: tuple[str, ...] = ()
    sensitive_contexts: tuple[str, ...] = ()
    match_reason: str = ""
    score: float = 0.0


# ── Retriever ─────────────────────────────────────────────────────────────


class CapabilityRetriever:
    """Retrieve relevant capability summaries for progressive disclosure.

    Deterministic filtering + ranking. No LLM calls, no embeddings,
    no network access, no script execution, no capability execution.
    """

    def __init__(
        self,
        *,
        store: CapabilityStore,
        index: CapabilityIndex,
        policy: CapabilityPolicy | None = None,
        available_tools: set[str] | None = None,
        current_scope: str | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> None:
        self._store = store
        self._index = index
        self._policy = policy
        self._available_tools = available_tools or set()
        self._current_scope = current_scope
        self._max_results = max_results

    # ── Public API ────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        context: RetrievalContext | None = None,
    ) -> list[CapabilitySummary]:
        """Retrieve top-k relevant capability summaries.

        Returns an empty list (never raises) when the index is unavailable.
        """
        ctx = context or RetrievalContext()
        try:
            candidates = self._fetch_candidates(query, ctx)
        except Exception:
            logger.debug("Candidate fetch failed", exc_info=True)
            return []

        if not candidates:
            return []

        try:
            filtered = self.filter_candidates(candidates, ctx)
        except Exception:
            logger.debug("Filter failed", exc_info=True)
            return []

        if not filtered:
            return []

        try:
            ranked = self.rank_candidates(filtered, ctx)
        except Exception:
            logger.debug("Rank failed", exc_info=True)
            return []

        return ranked[: ctx.max_results]

    def summarize(self, doc: CapabilityDocument) -> CapabilitySummary:
        """Create a compact summary from a CapabilityDocument.

        Deliberately excludes body, procedure, scripts, traces, evals,
        and version contents.
        """
        m = doc.manifest
        return CapabilitySummary(
            id=m.id,
            name=m.name,
            description=m.description,
            type=m.type.value if hasattr(m.type, "value") else str(m.type),
            scope=m.scope.value if hasattr(m.scope, "value") else str(m.scope),
            maturity=m.maturity.value if hasattr(m.maturity, "value") else str(m.maturity),
            status=m.status.value if hasattr(m.status, "value") else str(m.status),
            risk_level=m.risk_level.value if hasattr(m.risk_level, "value") else str(m.risk_level),
            trust_required=m.trust_required,
            triggers=tuple(m.triggers),
            tags=tuple(m.tags),
            required_tools=tuple(m.required_tools),
            do_not_apply_when=tuple(m.do_not_apply_when),
            sensitive_contexts=tuple(v.value if hasattr(v, "value") else str(v) for v in m.sensitive_contexts),
        )

    def filter_candidates(
        self,
        candidates: list[dict],
        context: RetrievalContext,
    ) -> list[CapabilitySummary]:
        """Apply deterministic filtering rules to raw candidates."""
        include_maturity = set(context.include_maturity)
        include_risk = set(context.include_risk_levels)
        available_tools = context.available_tools or self._available_tools

        summaries: list[CapabilitySummary] = []
        for c in candidates:
            status = str(c.get("status", "active"))
            maturity = str(c.get("maturity", "draft"))
            risk = str(c.get("risk_level", "low"))

            # Status filters
            if status in {"broken", "repairing", "needs_permission", "environment_mismatch"}:
                continue
            if status == "archived" and not context.include_archived:
                continue
            if status == "disabled" and not context.include_disabled:
                continue
            if status == "quarantined" and not context.include_quarantined:
                continue

            # Maturity filter
            if maturity == "broken":
                continue
            if maturity == "draft" and not context.include_draft:
                continue
            if maturity not in include_maturity and maturity != "draft":
                continue

            # Risk filter
            if risk == "high" and not context.include_high_risk:
                continue
            if risk not in include_risk and risk != "high":
                continue

            # Required tools check
            required_tools = _parse_list(c.get("required_tools") or c.get("required_tools_json"))
            if available_tools and required_tools:
                if not set(required_tools).issubset(available_tools):
                    continue

            sensitive_contexts = set(_parse_list(c.get("sensitive_contexts") or c.get("sensitive_contexts_json")))
            active_sensitive = set(context.sensitive_contexts or set())
            approved_sensitive = set(context.approved_sensitive_contexts or set())
            intersection = active_sensitive & sensitive_contexts
            if intersection and not intersection.issubset(approved_sensitive):
                continue

            summary = self._dict_to_summary(c)
            summaries.append(summary)

        # Deduplicate by id with scope precedence
        return _deduplicate_by_precedence(summaries)

    def rank_candidates(
        self,
        candidates: list[CapabilitySummary],
        context: RetrievalContext,
    ) -> list[CapabilitySummary]:
        """Rank summaries by deterministic relevance score."""
        scored: list[tuple[CapabilitySummary, float]] = []
        for summary in candidates:
            row = {
                "id": summary.id,
                "name": summary.name,
                "description": summary.description,
                "scope": summary.scope,
                "maturity": summary.maturity,
                "risk_level": summary.risk_level,
                "triggers": list(summary.triggers),
                "tags": list(summary.tags),
            }
            s = score_candidate(row, context.user_task)
            scored.append((summary, s))

        scored.sort(key=lambda pair: (-pair[1], pair[0].name.lower()))

        # Attach score and match_reason to summaries
        result: list[CapabilitySummary] = []
        for summary, score in scored:
            match_reason = _derive_match_reason(summary, context.user_task)
            result.append(
                CapabilitySummary(
                    id=summary.id,
                    name=summary.name,
                    description=summary.description,
                    type=summary.type,
                    scope=summary.scope,
                    maturity=summary.maturity,
                    status=summary.status,
                    risk_level=summary.risk_level,
                    trust_required=summary.trust_required,
                    triggers=summary.triggers,
                    tags=summary.tags,
                    required_tools=summary.required_tools,
                    do_not_apply_when=summary.do_not_apply_when,
                    sensitive_contexts=summary.sensitive_contexts,
                    match_reason=match_reason,
                    score=score,
                )
            )
        return result

    # ── Internal ──────────────────────────────────────────────────────

    def _fetch_candidates(
        self, query: str, context: RetrievalContext
    ) -> list[dict]:
        """Query the index for candidate rows."""
        filters: dict = {}

        allowed_scopes = context.allowed_scopes
        if not allowed_scopes:
            allowed_scopes = SCOPE_PRECEDENCE_ORDER

        candidates: list[dict] = []
        for scope in allowed_scopes:
            filters["scope"] = scope
            rows = self._index.search(query=query, filters=filters, limit=50)
            candidates.extend(rows)

        return candidates

    def _dict_to_summary(self, row: dict) -> CapabilitySummary:
        return CapabilitySummary(
            id=str(row.get("id", "")),
            name=str(row.get("name", "")),
            description=str(row.get("description", "")),
            type=str(row.get("type", "")),
            scope=str(row.get("scope", "global")),
            maturity=str(row.get("maturity", "draft")),
            status=str(row.get("status", "active")),
            risk_level=str(row.get("risk_level", "low")),
            trust_required=str(row.get("trust_required", "developer")),
            triggers=tuple(_parse_list(row.get("triggers") or row.get("triggers_json"))),
            tags=tuple(_parse_list(row.get("tags") or row.get("tags_json"))),
            required_tools=tuple(_parse_list(row.get("required_tools") or row.get("required_tools_json"))),
            do_not_apply_when=tuple(_parse_list(row.get("do_not_apply_when") or row.get("do_not_apply_when_json"))),
            sensitive_contexts=tuple(_parse_list(row.get("sensitive_contexts") or row.get("sensitive_contexts_json"))),
        )


# ── Module-private helpers ────────────────────────────────────────────────


def _parse_list(value) -> list:
    """Parse a value that might be a JSON string or already a list."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _deduplicate_by_precedence(
    summaries: list[CapabilitySummary],
) -> list[CapabilitySummary]:
    """Keep the highest-precedence scope for each capability id."""
    scope_rank = {s: i for i, s in enumerate(SCOPE_PRECEDENCE_ORDER)}
    by_id: dict[str, CapabilitySummary] = {}
    for s in summaries:
        existing = by_id.get(s.id)
        if existing is None:
            by_id[s.id] = s
        else:
            current_rank = scope_rank.get(s.scope, len(SCOPE_PRECEDENCE_ORDER))
            existing_rank = scope_rank.get(existing.scope, len(SCOPE_PRECEDENCE_ORDER))
            if current_rank < existing_rank:
                by_id[s.id] = s
    return list(by_id.values())


def _derive_match_reason(summary: CapabilitySummary, query: str) -> str:
    """Derive a human-readable match reason."""
    if not query or not query.strip():
        return "candidate"
    q = query.strip().lower()
    reasons: list[str] = []
    if q in summary.name.lower():
        reasons.append("name")
    if q in summary.description.lower():
        reasons.append("description")
    if any(q in t.lower() for t in summary.triggers):
        reasons.append("trigger")
    if any(q in t.lower() for t in summary.tags):
        reasons.append("tag")
    return ", ".join(reasons) if reasons else "keyword"
