from __future__ import annotations

# 身份检索器 — embedding 相似度 + 置信度加权排序
# Identity retriever — embedding similarity + confidence weighted ranking.

import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.identity.auth import AuthContext
from src.identity.flags import IdentityFlags
from src.identity.models import (
    ClaimStatus,
    ContextProfile,
    GatePassReason,
    IdentityClaim,
    RetrievalTrace,
    Sensitivity,
)
from src.identity.store import IdentityStore

logger = logging.getLogger("lapwing.identity.retriever")

# 敏感度排序：PUBLIC < PRIVATE < RESTRICTED
_SENSITIVITY_ORDER: dict[str, int] = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.PRIVATE: 1,
    Sensitivity.RESTRICTED: 2,
    "public": 0,
    "private": 1,
    "restricted": 2,
}

# Final-score weights — relevance dominates, confidence stabilises ties.
_W_RELEVANCE = 0.7
_W_CONFIDENCE = 0.3


def _sensitivity_rank(s: Sensitivity | str) -> int:
    return _SENSITIVITY_ORDER.get(s, 0)


def _sensitivity_allowed(max_sensitivity: Sensitivity) -> list[str]:
    """Names of sensitivity buckets allowed up to and including max_sensitivity."""
    cap = _sensitivity_rank(max_sensitivity)
    return [name for name, rank in (("public", 0), ("private", 1), ("restricted", 2)) if rank <= cap]


@dataclass
class RetrievalResult:
    claims: list[IdentityClaim]
    trace: RetrievalTrace | None
    raw_query_stored: bool = True
    query_summary: str = ""


class IdentityRetriever:
    """身份检索器（Module 4）。

    Two ranking modes:
    - With ``vector_index``: Chroma similarity + sensitivity where-filter,
      then ``final = relevance * 0.7 + confidence * 0.3``.
    - Without: legacy confidence-DESC sort (preserves no-deps tests).
    """

    def __init__(
        self,
        *,
        store: IdentityStore,
        flags: IdentityFlags,
        vector_index=None,
    ) -> None:
        self._store = store
        self._flags = flags
        self._vector_index = vector_index

    async def retrieve(
        self,
        query: str,
        auth: AuthContext,
        *,
        profile: ContextProfile | None = None,
        max_sensitivity: Sensitivity = Sensitivity.PUBLIC,
        top_k: int = 10,
        min_confidence: float = 0.3,
    ) -> RetrievalResult:
        # --- killswitch: no trace, no result ---
        if self._flags.identity_system_killswitch:
            logger.debug("identity killswitch is ON — skipping retrieval")
            return RetrievalResult(
                claims=[], trace=None, raw_query_stored=False, query_summary="",
            )

        # --- component disabled: write a disabled trace, return empty ---
        if not self._flags.retriever_enabled:
            logger.debug("retriever_enabled=False — writing disabled trace")
            trace = await self._write_trace(
                query=query,
                profile=profile,
                candidate_ids=[],
                selected_claims=[],
                redacted_ids=[],
                latency_ms=0.0,
                max_sensitivity=max_sensitivity,
                pass_reason=GatePassReason.COMPONENT_DISABLED,
                scores={},
            )
            return RetrievalResult(
                claims=[],
                trace=trace,
                raw_query_stored=_sensitivity_rank(max_sensitivity) == 0,
                query_summary=trace.query,
            )

        t0 = time.monotonic()

        # Decide path: embedding when vector_index has entries; else fallback.
        use_embedding = False
        if self._vector_index is not None:
            try:
                use_embedding = (await self._vector_index.count()) > 0
            except Exception:
                logger.debug("vector_index.count() failed — falling back to confidence sort", exc_info=True)
                use_embedding = False

        if use_embedding and query.strip():
            selected, candidate_ids, scores = await self._retrieve_via_embedding(
                query=query,
                auth=auth,
                max_sensitivity=max_sensitivity,
                top_k=top_k,
                min_confidence=min_confidence,
            )
        else:
            selected, candidate_ids, scores = await self._retrieve_via_confidence(
                auth=auth,
                max_sensitivity=max_sensitivity,
                top_k=top_k,
                min_confidence=min_confidence,
            )

        latency_ms = (time.monotonic() - t0) * 1000

        trace = await self._write_trace(
            query=query,
            profile=profile,
            candidate_ids=candidate_ids,
            selected_claims=selected,
            redacted_ids=[],
            latency_ms=latency_ms,
            max_sensitivity=max_sensitivity,
            scores=scores,
        )

        return RetrievalResult(
            claims=selected,
            trace=trace,
            raw_query_stored=_sensitivity_rank(max_sensitivity) == 0,
            query_summary=trace.query,
        )

    # ------------------------------------------------------------------
    # Ranking strategies
    # ------------------------------------------------------------------

    async def _retrieve_via_confidence(
        self,
        *,
        auth: AuthContext,
        max_sensitivity: Sensitivity,
        top_k: int,
        min_confidence: float,
    ) -> tuple[list[IdentityClaim], list[str], dict]:
        """Legacy path: confidence DESC (used in tests + as a fallback)."""
        all_claims = await self._store.list_claims(auth, status=ClaimStatus.ACTIVE.value)
        max_rank = _sensitivity_rank(max_sensitivity)
        candidates = [
            c for c in all_claims
            if _sensitivity_rank(c.sensitivity) <= max_rank
            and c.confidence >= min_confidence
        ]
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        selected = candidates[:top_k]
        return selected, [c.claim_id for c in candidates], {}

    async def _retrieve_via_embedding(
        self,
        *,
        query: str,
        auth: AuthContext,
        max_sensitivity: Sensitivity,
        top_k: int,
        min_confidence: float,
    ) -> tuple[list[IdentityClaim], list[str], dict]:
        """Vector path: Chroma similarity + sensitivity where-filter, weighted score."""
        allowed = _sensitivity_allowed(max_sensitivity)
        where: dict | None
        if len(allowed) == 1:
            where = {"sensitivity": allowed[0]}
        else:
            where = {"sensitivity": {"$in": allowed}}

        # Pull 2x top_k so post-filtering by min_confidence still has headroom.
        pairs = await self._vector_index.query(
            query_text=query,
            n_results=max(top_k * 2, top_k),
            where=where,
        )
        if not pairs:
            return [], [], {}

        # Hydrate claims; keep only ACTIVE + min_confidence pass.
        scored: list[tuple[IdentityClaim, float, float, float]] = []
        candidate_ids: list[str] = []
        scores: dict[str, dict[str, float]] = {}
        for claim_id, distance in pairs:
            claim = await self._store.get_claim(claim_id, auth)
            if claim is None:
                continue
            status_val = claim.status.value if hasattr(claim.status, "value") else claim.status
            if status_val != "active":
                continue
            if claim.confidence < min_confidence:
                continue
            relevance = max(0.0, 1.0 - distance)
            final = _W_RELEVANCE * relevance + _W_CONFIDENCE * float(claim.confidence)
            scored.append((claim, relevance, float(claim.confidence), final))
            candidate_ids.append(claim_id)
            scores[claim_id] = {
                "relevance": round(relevance, 4),
                "confidence": round(float(claim.confidence), 4),
                "final": round(final, 4),
            }

        scored.sort(key=lambda t: t[3], reverse=True)
        selected = [t[0] for t in scored[:top_k]]
        return selected, candidate_ids, scores

    # ------------------------------------------------------------------
    # Trace writer
    # ------------------------------------------------------------------

    async def _write_trace(
        self,
        *,
        query: str,
        profile: ContextProfile | None,
        candidate_ids: list[str],
        selected_claims: list[IdentityClaim],
        redacted_ids: list[str],
        latency_ms: float,
        max_sensitivity: Sensitivity,
        scores: dict,
        pass_reason: GatePassReason | None = None,
    ) -> RetrievalTrace:
        """构建 RetrievalTrace 并持久化。

        PRIVATE/RESTRICTED 查询使用占位符代替原始文本。
        """
        if _sensitivity_rank(max_sensitivity) == 0:
            stored_query = query
        else:
            stored_query = "[redacted query]"

        selected_ids = [c.claim_id for c in selected_claims]

        trace = RetrievalTrace(
            trace_id=str(uuid4()),
            query=stored_query,
            context_profile=profile,
            candidate_ids=candidate_ids,
            selected_ids=selected_ids,
            redacted_ids=redacted_ids,
            latency_ms=latency_ms,
            created_at=datetime.now(timezone.utc).isoformat(),
            scores=scores,
        )

        await self._store.write_retrieval_trace(trace)
        logger.debug(
            "retrieval trace written trace_id=%s candidates=%d selected=%d scored=%d",
            trace.trace_id,
            len(candidate_ids),
            len(selected_ids),
            len(scores),
        )
        return trace
