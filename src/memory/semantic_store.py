"""SemanticStore — category-organised distilled knowledge.

Blueprint v2.0 Step 7 §M1.c. The RAPTOR-inspired upper layer: each entry
is a persistent fact Lapwing has distilled from multiple episodic events.
Files live in ``data/memory/semantic/<category>.md``, one category per
file. Each fact is a section inside its category file with the fact as
the heading.

Categories in v1:
- ``kevin``  — facts about the owner
- ``lapwing``— facts about herself (self-model)
- ``world``  — external facts that carry across conversations

Extension path: if a category grows large (> ~200 facts), split into
subcategories via subdirectories (``semantic/kevin/preferences.md``).
Step 7 keeps the flat layout; the ``category`` field in metadata is a
path-friendly slug so the split is a mechanical rewrite later.

Retrieval shares the ``MemoryVectorStore`` collection with EpisodicStore
and the manual NoteStore; metadata filter ``note_type="semantic"``
keeps WorkingSet queries scoped. See ``step7_memory_reuse.md`` §3.1.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.time_utils import local_tz

if TYPE_CHECKING:
    from src.memory.vector_store import MemoryVectorStore

logger = logging.getLogger("lapwing.memory.semantic_store")

_TAIPEI = local_tz()
_NOTE_TYPE = "semantic"

_CATEGORY_SLUG = re.compile(r"[^a-z0-9_\-]+")
# Default similarity threshold for de-duplication (cosine similarity on
# the vector store's embedding). Above this, treat as "same fact, skip".
_DEFAULT_DEDUP_THRESHOLD = 0.85


@dataclass(frozen=True, slots=True)
class SemanticEntry:
    """One fact surfaced by ``query``."""

    fact_id: str
    category: str
    content: str
    score: float
    semantic_similarity: float
    source_episodes: tuple[str, ...]


class SemanticStore:
    """语义记忆存储：按分类 markdown + 共享向量索引 + 写入时去重。"""

    def __init__(
        self,
        *,
        memory_dir: Path | str = "data/memory/semantic",
        vector_store: "MemoryVectorStore",
        dedup_threshold: float = _DEFAULT_DEDUP_THRESHOLD,
    ) -> None:
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._vector_store = vector_store
        self._dedup_threshold = dedup_threshold
        self._file_lock = asyncio.Lock()

    # ── Write path ──────────────────────────────────────────────────

    async def add_fact(
        self,
        *,
        category: str,
        content: str,
        source_episodes: list[str] | tuple[str, ...] = (),
    ) -> SemanticEntry | None:
        """Record one semantic fact; return None when deduplicated.

        Before writing we probe the vector store for near-duplicates; if
        any existing semantic entry exceeds ``dedup_threshold``
        similarity, we skip the write and return None. The probe uses
        the same collection scoped by ``note_type="semantic"``.
        """
        if not content.strip():
            raise ValueError("content 不能为空")
        slug = _slugify(category) or "misc"
        fact_text = content.strip()

        # ── Dedup probe ──
        try:
            existing = await self._vector_store.recall(
                fact_text,
                top_k=3,
                where={"note_type": _NOTE_TYPE},
            )
        except Exception as exc:
            logger.warning("[semantic] dedup probe failed: %s", exc)
            existing = []

        for hit in existing:
            if hit.semantic_similarity >= self._dedup_threshold:
                logger.debug(
                    "[semantic] dedup skip: new fact ~= existing %s (sim=%.3f)",
                    hit.note_id, hit.semantic_similarity,
                )
                return None

        # ── ID + metadata ──
        now_tpe = datetime.now(tz=_TAIPEI)
        fact_id = _build_fact_id(now_tpe, fact_text)

        metadata: dict = {
            "note_type": _NOTE_TYPE,
            "trust": "self",
            "created_at": now_tpe.isoformat(),
            "category": slug,
            "file_path": str(self._category_path(slug).resolve()),
            "source_episodes": ",".join(source_episodes),
        }

        # ── File write ──
        async with self._file_lock:
            await asyncio.to_thread(
                self._append_section_sync,
                category=slug,
                content=fact_text,
                fact_id=fact_id,
                source_episodes=tuple(source_episodes),
                created_at=now_tpe,
            )

        # ── Vector index ──
        try:
            await self._vector_store.add(
                note_id=fact_id,
                content=fact_text,
                metadata=metadata,
            )
        except Exception as exc:
            logger.warning(
                "[semantic] vector add failed for %s: %s",
                fact_id, exc,
            )

        return SemanticEntry(
            fact_id=fact_id,
            category=slug,
            content=fact_text,
            score=1.0,
            semantic_similarity=1.0,
            source_episodes=tuple(source_episodes),
        )

    # ── Read path ───────────────────────────────────────────────────

    async def query(
        self, query_text: str, top_k: int = 5
    ) -> list[SemanticEntry]:
        """Semantic search restricted to semantic entries."""
        if not query_text.strip():
            return []
        raw = await self._vector_store.recall(
            query_text,
            top_k=top_k,
            where={"note_type": _NOTE_TYPE},
        )
        out: list[SemanticEntry] = []
        for hit in raw:
            meta = hit.metadata or {}
            out.append(
                SemanticEntry(
                    fact_id=hit.note_id,
                    category=meta.get("category") or "misc",
                    content=hit.content,
                    score=hit.score,
                    semantic_similarity=hit.semantic_similarity,
                    source_episodes=_parse_episodes(
                        meta.get("source_episodes")
                    ),
                )
            )
        return out

    # ── File I/O helpers ────────────────────────────────────────────

    def _category_path(self, slug: str) -> Path:
        return self._dir / f"{slug}.md"

    def _append_section_sync(
        self,
        *,
        category: str,
        content: str,
        fact_id: str,
        source_episodes: tuple[str, ...],
        created_at: datetime,
    ) -> None:
        path = self._category_path(category)
        # Section title is the fact itself (first sentence) so the file
        # reads like a bullet list of assertions.
        first_line = content.splitlines()[0][:120]
        stamp = created_at.strftime("%Y-%m-%d %H:%M")
        sources_note = ""
        if source_episodes:
            sources_note = (
                "\n\n> sources: " + ", ".join(source_episodes)
            )
        section = (
            f"## {first_line}\n\n"
            f"<!-- fact_id: {fact_id}, created_at: {stamp} -->\n\n"
            f"{content}"
            f"{sources_note}\n\n"
        )
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if not existing.endswith("\n"):
                existing += "\n"
            path.write_text(existing + section, encoding="utf-8")
        else:
            header = f"# {category} — 语义记忆\n\n"
            path.write_text(header + section, encoding="utf-8")


# ── Module-private helpers ──────────────────────────────────────────

def _slugify(category: str) -> str:
    """Map an arbitrary category label to a filesystem-safe slug."""
    lower = category.strip().lower()
    return _CATEGORY_SLUG.sub("_", lower).strip("_-")


def _build_fact_id(when_tpe: datetime, content: str) -> str:
    stamp = when_tpe.strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:6]
    return f"sem_{stamp}_{digest}"


def _parse_episodes(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())
