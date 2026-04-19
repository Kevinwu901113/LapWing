"""EpisodicStore — day-organised event memory.

Blueprint v2.0 Step 7 §M1.b. The RAPTOR-inspired lower layer: each entry is
one event Lapwing lived through (a conversation slice, a tool outcome, a
decision that mattered). Files live in ``data/memory/episodic/`` as
``YYYY-MM-DD.md`` — one file per day, multiple sections within for multiple
episodes. Files are human-readable by Kevin; sections carry a timestamp
header so the stream is browsable in chronological order.

Retrieval goes through the shared ``MemoryVectorStore`` (single Chroma
collection for the whole memory layer) with metadata filter
``note_type="episodic"``. The decision to share the collection rather than
stand up a dedicated one is documented in
``docs/refactor_v2/step7_memory_reuse.md`` §3.1.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from src.memory.vector_store import MemoryVectorStore

logger = logging.getLogger("lapwing.memory.episodic_store")

_TAIPEI = ZoneInfo("Asia/Taipei")
_NOTE_TYPE = "episodic"

# Header-detection regex for section splitting during read-back. Accepts
# ``## HH:MM — title`` or ``## HH:MM - title`` (ASCII dash tolerated).
_SECTION_HEADER = re.compile(r"^## (\d{2}:\d{2}) [—-] (.+)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class EpisodicEntry:
    """One episode surfaced by ``query``.

    ``episode_id`` is the stable identifier used both in ChromaDB and in
    the markdown file (sections aren't keyed by id in the file — id lives
    in the Chroma metadata). ``date`` is ISO 8601. ``score`` is the
    composite relevance from ``MemoryVectorStore.recall`` (semantic
    similarity + recency + trust + ...).
    """

    episode_id: str
    date: str
    title: str
    summary: str
    score: float
    semantic_similarity: float
    source_trajectory_ids: tuple[int, ...]


class EpisodicStore:
    """情景记忆存储：markdown 文件 + 共享向量索引。"""

    def __init__(
        self,
        *,
        memory_dir: Path | str = "data/memory/episodic",
        vector_store: "MemoryVectorStore",
    ) -> None:
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._vector_store = vector_store
        # File writes are synchronous I/O; serialise across concurrent
        # ``add_episode`` calls for the same day to keep section ordering
        # monotonic. One lock per process is enough given the low write
        # rate (≤ one episode per conversation).
        self._file_lock = asyncio.Lock()

    # ── Write path ──────────────────────────────────────────────────

    async def add_episode(
        self,
        *,
        summary: str,
        source_trajectory_ids: list[int] | tuple[int, ...] = (),
        occurred_at: datetime | None = None,
        title: str | None = None,
    ) -> EpisodicEntry:
        """Record one episode.

        ``occurred_at`` pins the timestamp; default is ``datetime.now``
        (Asia/Taipei). ``title`` is the section heading — if absent we
        derive one from the first line of ``summary``. ``summary`` is
        the body text (multi-line OK).

        Writes the markdown section first, then the vector index. If the
        vector write fails the markdown is already on disk (next rebuild
        can re-index); logged as a warning.
        """
        if not summary.strip():
            raise ValueError("summary 不能为空")

        when = occurred_at or datetime.now(tz=_TAIPEI)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_TAIPEI)
        when_tpe = when.astimezone(_TAIPEI)

        day = when_tpe.date()
        derived_title = (title or summary.splitlines()[0]).strip()[:80]
        episode_id = _build_episode_id(when_tpe, derived_title)

        # ── File write ──
        async with self._file_lock:
            await asyncio.to_thread(
                self._append_section_sync,
                day=day,
                when_tpe=when_tpe,
                title=derived_title,
                summary=summary.strip(),
                episode_id=episode_id,
            )

        # ── Vector index ──
        metadata: dict = {
            "note_type": _NOTE_TYPE,
            "trust": "self",
            "created_at": when_tpe.isoformat(),
            "date": day.isoformat(),
            "title": derived_title,
            "file_path": str(self._day_path(day).resolve()),
            "source_trajectory_ids": ",".join(
                str(i) for i in source_trajectory_ids
            ),
        }
        try:
            await self._vector_store.add(
                note_id=episode_id,
                content=f"{derived_title}\n\n{summary.strip()}",
                metadata=metadata,
            )
        except Exception as exc:
            logger.warning(
                "[episodic] vector add failed for %s: %s — markdown is on disk, "
                "next rebuild will catch up",
                episode_id, exc,
            )

        return EpisodicEntry(
            episode_id=episode_id,
            date=day.isoformat(),
            title=derived_title,
            summary=summary.strip(),
            score=1.0,
            semantic_similarity=1.0,
            source_trajectory_ids=tuple(source_trajectory_ids),
        )

    # ── Read path ───────────────────────────────────────────────────

    async def query(
        self, query_text: str, top_k: int = 5
    ) -> list[EpisodicEntry]:
        """Semantic search restricted to episodic entries."""
        if not query_text.strip():
            return []
        raw = await self._vector_store.recall(
            query_text,
            top_k=top_k,
            where={"note_type": _NOTE_TYPE},
        )
        out: list[EpisodicEntry] = []
        for hit in raw:
            meta = _hit_metadata(hit)
            trajectory_ids = _parse_trajectory_ids(
                meta.get("source_trajectory_ids")
            )
            title = meta.get("title") or ""
            summary = hit.content
            if title and summary.startswith(title):
                summary = summary[len(title):].lstrip("\n")
            out.append(
                EpisodicEntry(
                    episode_id=hit.note_id,
                    date=meta.get("date") or "",
                    title=title,
                    summary=summary.strip(),
                    score=hit.score,
                    semantic_similarity=hit.semantic_similarity,
                    source_trajectory_ids=trajectory_ids,
                )
            )
        return out

    # ── File I/O helpers ────────────────────────────────────────────

    def _day_path(self, day: date) -> Path:
        return self._dir / f"{day.isoformat()}.md"

    def _append_section_sync(
        self,
        *,
        day: date,
        when_tpe: datetime,
        title: str,
        summary: str,
        episode_id: str,
    ) -> None:
        path = self._day_path(day)
        hhmm = when_tpe.strftime("%H:%M")
        section = (
            f"## {hhmm} — {title}\n\n"
            f"<!-- episode_id: {episode_id} -->\n\n"
            f"{summary}\n\n"
        )
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if not existing.endswith("\n"):
                existing += "\n"
            path.write_text(existing + section, encoding="utf-8")
        else:
            header = f"# {day.isoformat()} 情景记录\n\n"
            path.write_text(header + section, encoding="utf-8")


# ── Module-private helpers ──────────────────────────────────────────

def _build_episode_id(when_tpe: datetime, title: str) -> str:
    stamp = when_tpe.strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:6]
    return f"ep_{stamp}_{digest}"


def _parse_trajectory_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    parts = [p for p in raw.split(",") if p.strip()]
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            continue
    return tuple(out)


def _hit_metadata(hit) -> dict:
    """Extract the full metadata dict from a RecallResult hit (Step 7)."""
    meta = getattr(hit, "metadata", None) or {}
    return meta if isinstance(meta, dict) else {}
