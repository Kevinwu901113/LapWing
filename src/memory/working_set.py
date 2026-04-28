"""WorkingSet — retrieval that feeds StateView.memory_snippets.

Blueprint v2.0 Step 7 §M1.d. The working-memory layer: given a query
text, pulls the top-K most relevant hits from Episodic + Semantic and
projects them into ``MemorySnippets`` so ``StateSerializer`` can emit
a 相关记忆 block in the system prompt.

Phase 1 wiki layer (Memory Wiki Blueprint §1.8) extends this with a
read-only injection of wiki pages. Wiki snippets are appended in front
of episodic/semantic results so the highest-precision facts win the
budget. The injection is gated by ``MEMORY_WIKI_CONTEXT_ENABLED`` and
its share of the total budget by ``MEMORY_WIKI_CONTEXT_BUDGET_RATIO``.

Separation of concerns vs. the underlying stores: EpisodicStore /
SemanticStore own their write & single-layer query paths; WorkingSet
owns the cross-layer merge and the projection into the StateView
schema. The serializer never learns about episodic/semantic — it only
sees ``MemorySnippets``, which keeps its pure-function contract intact
(Step 3 §2).

Prefix convention: content carries a visible ``[情景]`` / ``[知识]`` /
``[wiki]`` tag so the model can tell apart "事件" from "知识" from
"wiki" at read time. Score is the composite relevance score that
stores return; WorkingSet sorts on it descending before trimming to
``top_k``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.state_view import MemorySnippet, MemorySnippets
from src.memory.memory_schema import MemorySchema

if TYPE_CHECKING:
    from src.memory.episodic_store import EpisodicStore
    from src.memory.semantic_store import SemanticStore

logger = logging.getLogger("lapwing.memory.working_set")

_DEFAULT_TOP_K = 10
_SNIPPET_CHAR_LIMIT = 300
_TOTAL_CHAR_BUDGET = 2000

# Wiki snippets sort above any vector hit. Plain numeric (not inf) so
# the existing tuple sort and budgeting code stays simple.
_WIKI_SCORE_FLOOR = 1_000.0
_WIKI_SECTIONS = ("Current summary", "Stable facts")


class WorkingSet:
    """两层合并检索；输出 MemorySnippets 供 StateView 消费。

    Phase 1 wiki injection: when ``wiki_enabled`` and ``wiki_dir`` are
    configured, the caller can pass ``wiki_entities`` to ``retrieve``
    and the matching pages are read off disk and projected into
    high-priority MemorySnippets in front of the vector results.
    """

    def __init__(
        self,
        *,
        episodic_store: "EpisodicStore | None" = None,
        semantic_store: "SemanticStore | None" = None,
        wiki_dir: str | Path | None = None,
        wiki_enabled: bool = False,
        wiki_budget_ratio: float = 0.40,
        total_char_budget: int = _TOTAL_CHAR_BUDGET,
    ) -> None:
        self._episodic = episodic_store
        self._semantic = semantic_store
        self._wiki_dir = Path(wiki_dir) if wiki_dir else None
        self._wiki_enabled = bool(wiki_enabled and self._wiki_dir is not None)
        self._wiki_budget_ratio = max(0.0, min(1.0, wiki_budget_ratio))
        self._total_char_budget = total_char_budget
        self._schema = MemorySchema()

    async def retrieve(
        self,
        query_text: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        wiki_entities: list[str] | None = None,
    ) -> MemorySnippets:
        """Fetch + rank + trim across Wiki + Episodic + Semantic.

        Empty query or all sources missing → empty snippets (caller
        renders no memory layer). Per-source failures are caught and
        logged; other sources still contribute.

        ``wiki_entities`` is the Phase 1 entity list to inject (e.g.
        ``["entity.kevin", "entity.lapwing"]``). Only used when wiki is
        enabled and ``wiki_dir`` is set. Vector recall still runs
        regardless.
        """
        wiki_snippets = self._inject_wiki_snippets(wiki_entities or [])

        if not query_text or not query_text.strip():
            return MemorySnippets(snippets=tuple(wiki_snippets))
        if self._episodic is None and self._semantic is None:
            return MemorySnippets(snippets=tuple(wiki_snippets))

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

        snippets: list[MemorySnippet] = list(wiki_snippets)
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
        trimmed = _apply_budget(
            snippets, top_k, total_budget=self._total_char_budget,
        )
        return MemorySnippets(snippets=tuple(trimmed))

    # ── Wiki injection (Phase 1) ────────────────────────────────────

    def _inject_wiki_snippets(self, entity_ids: list[str]) -> list[MemorySnippet]:
        if not self._wiki_enabled or not entity_ids or self._wiki_dir is None:
            return []

        wiki_budget = int(self._total_char_budget * self._wiki_budget_ratio)
        if wiki_budget <= 0:
            return []

        out: list[MemorySnippet] = []
        used = 0
        for i, entity_id in enumerate(entity_ids):
            page_path = _entity_id_to_path(self._wiki_dir, entity_id)
            if page_path is None or not page_path.exists():
                continue
            try:
                text = page_path.read_text(encoding="utf-8")
                _, body = self._schema.parse(text)
                sections = self._schema.extract_sections(body)
                summary = sections.get("Current summary", "").strip()
                facts = sections.get("Stable facts", "").strip()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[working_set] wiki read failed for %s: %s", entity_id, exc,
                )
                continue

            content = _format_wiki_snippet(entity_id, summary, facts)
            if not content:
                continue
            content = _truncate(content)
            if used + len(content) > wiki_budget and out:
                break
            used += len(content)
            # Score = floor + (descending position) so passed-in order wins.
            score = _WIKI_SCORE_FLOOR + (len(entity_ids) - i)
            out.append(MemorySnippet(
                note_id=entity_id,
                content=content,
                score=score,
            ))
        return out


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
    snippets: list[MemorySnippet],
    top_k: int,
    *,
    total_budget: int = _TOTAL_CHAR_BUDGET,
) -> list[MemorySnippet]:
    out: list[MemorySnippet] = []
    total = 0
    for s in snippets[:top_k]:
        if total + len(s.content) > total_budget and out:
            break
        out.append(s)
        total += len(s.content)
    return out


def _entity_id_to_path(wiki_dir: Path, entity_id: str) -> Path | None:
    """Map an entity id like ``entity.kevin`` → ``entities/kevin.md``.

    Phase 1 supports two namespaces: ``entity.*`` (entities/) and
    ``knowledge.*`` (knowledge/). Other namespaces return None.
    """
    if "." not in entity_id:
        return None
    namespace, slug = entity_id.split(".", 1)
    slug = slug.replace("/", "-")
    if namespace == "entity":
        return wiki_dir / "entities" / f"{slug}.md"
    if namespace == "knowledge":
        return wiki_dir / "knowledge" / f"{slug}.md"
    return None


def _format_wiki_snippet(entity_id: str, summary: str, facts: str) -> str:
    """Render the two-section wiki extract for prompt injection."""
    summary_clean = _strip_placeholder(summary)
    facts_clean = _strip_placeholder(facts)
    if not summary_clean and not facts_clean:
        return ""
    parts = [f"[wiki / {entity_id}]"]
    if summary_clean:
        parts.append(summary_clean)
    if facts_clean:
        parts.append(facts_clean)
    return "\n".join(parts)


_PLACEHOLDER_RE = re.compile(r"^[（(]\s*暂无\s*[)）]\s*$")


def _strip_placeholder(text: str) -> str:
    if not text:
        return ""
    if _PLACEHOLDER_RE.match(text.strip()):
        return ""
    return text.strip()
