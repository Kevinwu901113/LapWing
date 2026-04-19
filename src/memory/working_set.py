"""WorkingSet — retrieval that feeds StateView.memory_snippets.

Blueprint v2.0 Step 7 §M1.d. The working-memory layer: given a query
text, pulls the top-K most relevant hits from Episodic + Semantic and
projects them into ``MemorySnippets`` so ``StateSerializer`` can emit
a 相关记忆 block in the system prompt.

Separation of concerns vs. the underlying stores: EpisodicStore /
SemanticStore own their write & single-layer query paths; WorkingSet
owns the cross-layer merge and the projection into the StateView
schema. The serializer never learns about episodic/semantic — it only
sees ``MemorySnippets``, which keeps its pure-function contract intact
(Step 3 §2).

Prefix convention: content carries a visible ``[情景]`` / ``[知识]``
tag so the model can tell apart "事件" from "知识" at read time. Score
is the composite relevance score that stores return; WorkingSet sorts
on it descending before trimming to ``top_k``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.state_view import MemorySnippet, MemorySnippets

if TYPE_CHECKING:
    from src.memory.episodic_store import EpisodicStore
    from src.memory.semantic_store import SemanticStore

logger = logging.getLogger("lapwing.memory.working_set")

_DEFAULT_TOP_K = 10
_SNIPPET_CHAR_LIMIT = 300
_TOTAL_CHAR_BUDGET = 2000


class WorkingSet:
    """两层合并检索；输出 MemorySnippets 供 StateView 消费。"""

    def __init__(
        self,
        *,
        episodic_store: "EpisodicStore | None" = None,
        semantic_store: "SemanticStore | None" = None,
    ) -> None:
        self._episodic = episodic_store
        self._semantic = semantic_store

    async def retrieve(
        self,
        query_text: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
    ) -> MemorySnippets:
        """Fetch + rank + trim across Episodic/Semantic.

        Empty query or both stores missing → empty snippets (caller
        renders no memory layer). Per-store failures are caught and
        logged; the other store still contributes.
        """
        if not query_text or not query_text.strip():
            return MemorySnippets(snippets=())
        if self._episodic is None and self._semantic is None:
            return MemorySnippets(snippets=())

        half = max(1, top_k // 2)
        episodic_hits: list = []
        semantic_hits: list = []

        if self._episodic is not None:
            try:
                episodic_hits = await self._episodic.query(
                    query_text, top_k=half,
                )
            except Exception as exc:
                logger.warning(
                    "[working_set] episodic query failed: %s", exc,
                )

        if self._semantic is not None:
            try:
                semantic_hits = await self._semantic.query(
                    query_text, top_k=half,
                )
            except Exception as exc:
                logger.warning(
                    "[working_set] semantic query failed: %s", exc,
                )

        snippets: list[MemorySnippet] = []
        for hit in episodic_hits:
            snippets.append(
                MemorySnippet(
                    note_id=hit.episode_id,
                    content=_episodic_body(hit),
                    score=hit.score,
                )
            )
        for hit in semantic_hits:
            snippets.append(
                MemorySnippet(
                    note_id=hit.fact_id,
                    content=_semantic_body(hit),
                    score=hit.score,
                )
            )

        snippets.sort(key=lambda s: s.score, reverse=True)
        trimmed = _apply_budget(snippets, top_k)
        return MemorySnippets(snippets=tuple(trimmed))


# ── Helpers ─────────────────────────────────────────────────────────

def _episodic_body(hit) -> str:
    """Format an Episodic hit for the prompt.

    Example: ``[情景 4/17] Kevin 问了道奇比赛结果，我查到但超时``
    """
    date_tag = hit.date or ""
    short_date = _short_date(date_tag)
    title = hit.title or ""
    summary = hit.summary or ""
    body = title if title else summary
    if title and summary and summary != title:
        body = f"{title} — {summary}"
    return _truncate(f"[情景 {short_date}] {body}")


def _semantic_body(hit) -> str:
    """Format a Semantic hit: ``[知识 / kevin] Kevin 喜欢看道奇比赛``"""
    category = hit.category or "misc"
    return _truncate(f"[知识 / {category}] {hit.content}")


def _short_date(iso: str) -> str:
    if not iso or len(iso) < 10:
        return iso or "?"
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


def _truncate(text: str) -> str:
    if len(text) <= _SNIPPET_CHAR_LIMIT:
        return text
    return text[: _SNIPPET_CHAR_LIMIT - 1] + "…"


def _apply_budget(
    snippets: list[MemorySnippet], top_k: int,
) -> list[MemorySnippet]:
    out: list[MemorySnippet] = []
    total = 0
    for s in snippets[:top_k]:
        if total + len(s.content) > _TOTAL_CHAR_BUDGET and out:
            break
        out.append(s)
        total += len(s.content)
    return out
