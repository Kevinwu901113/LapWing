from __future__ import annotations

# 身份检索器 — 按查询、敏感度、置信度过滤并追踪检索过程
# Identity retriever — filter claims by query, sensitivity, confidence, and trace retrieval

import time
import logging
from dataclasses import dataclass, field
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
    # 原始字符串值也映射，容错 DB 返回字符串
    "public": 0,
    "private": 1,
    "restricted": 2,
}


def _sensitivity_rank(s: Sensitivity | str) -> int:
    """返回敏感度的数值排名，数值越小越不敏感。"""
    return _SENSITIVITY_ORDER.get(s, 0)


@dataclass
class RetrievalResult:
    """检索结果，包含匹配的主张、追踪记录和查询隐私元数据。"""
    claims: list[IdentityClaim]
    trace: RetrievalTrace | None
    raw_query_stored: bool = True    # False 表示查询文本已脱敏后存储
    query_summary: str = ""          # 实际写入追踪的查询内容


class IdentityRetriever:
    """身份检索器（Module 4）。

    根据查询过滤活跃主张，应用敏感度上限和置信度下限，
    写入检索追踪（killswitch 时除外）。
    """

    def __init__(self, *, store: IdentityStore, flags: IdentityFlags) -> None:
        self._store = store
        self._flags = flags

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
        """检索与查询匹配的身份主张。

        1. killswitch 开启 → 返回空结果，不写任何追踪（Addendum P0.5）
        2. retriever_enabled=False → 返回空结果，写追踪（pass_reason=COMPONENT_DISABLED）
        3. 过滤活跃主张（敏感度 ≤ max_sensitivity 且 confidence ≥ min_confidence）
        4. 按置信度降序排序，取 top_k
        5. 写检索追踪（PRIVATE/RESTRICTED 查询脱敏后存储）
        6. 返回 RetrievalResult
        """
        # --- P0.5: 全局主开关，不写任何追踪 ---
        if self._flags.identity_system_killswitch:
            logger.debug("identity killswitch is ON — skipping retrieval")
            return RetrievalResult(claims=[], trace=None, raw_query_stored=False, query_summary="")

        # --- 组件开关关闭：写 disabled 追踪后返回空 ---
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
            )
            return RetrievalResult(
                claims=[],
                trace=trace,
                raw_query_stored=_sensitivity_rank(max_sensitivity) == 0,
                query_summary=trace.query,
            )

        # --- 正常检索路径 ---
        t0 = time.monotonic()

        # 列出所有 ACTIVE 主张
        all_claims = await self._store.list_claims(auth, status=ClaimStatus.ACTIVE.value)

        max_rank = _sensitivity_rank(max_sensitivity)

        # 过滤：敏感度 ≤ max_sensitivity 且 confidence ≥ min_confidence
        candidates = [
            c for c in all_claims
            if _sensitivity_rank(c.sensitivity) <= max_rank
            and c.confidence >= min_confidence
        ]

        # 按置信度降序排序，取 top_k
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        selected = candidates[:top_k]

        latency_ms = (time.monotonic() - t0) * 1000

        candidate_ids = [c.claim_id for c in candidates]
        selected_ids = [c.claim_id for c in selected]

        trace = await self._write_trace(
            query=query,
            profile=profile,
            candidate_ids=candidate_ids,
            selected_claims=selected,
            redacted_ids=[],
            latency_ms=latency_ms,
            max_sensitivity=max_sensitivity,
        )

        raw_stored = _sensitivity_rank(max_sensitivity) == 0
        return RetrievalResult(
            claims=selected,
            trace=trace,
            raw_query_stored=raw_stored,
            query_summary=trace.query,
        )

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
        pass_reason: GatePassReason | None = None,
    ) -> RetrievalTrace:
        """构建 RetrievalTrace 并持久化。

        PRIVATE/RESTRICTED 查询使用占位符代替原始文本。
        """
        # 查询脱敏：仅 PUBLIC 级别保留原始查询
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
        )

        await self._store.write_retrieval_trace(trace)
        logger.debug(
            "retrieval trace written trace_id=%s candidates=%d selected=%d",
            trace.trace_id,
            len(candidate_ids),
            len(selected_ids),
        )
        return trace
