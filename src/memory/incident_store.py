"""IncidentStore — 失败/异常事件的隔离记忆存储。

失败的工具调用、Agent 超时、循环检测触发等产生的记忆条目写入此处，
与正常的 episodic 记忆隔离。WorkingSet 默认不检索此层，仅在
显式反思（Lapwing 主动分析失败经验）时纳入。

文件存储在 data/memory/incidents/YYYY-MM-DD.md，向量索引使用
note_type="incident" 与 episodic/semantic 区分。
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

logger = logging.getLogger("lapwing.memory.incident_store")

_TAIPEI = local_tz()
_NOTE_TYPE = "incident"

_FAILURE_KEYWORDS = re.compile(
    r"(error|fail|timeout|超时|失败|异常|断路|circuit.?breaker|loop.?detect"
    r"|refused|denied|blocked|崩溃|crash|exception)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class IncidentEntry:
    incident_id: str
    date: str
    title: str
    summary: str
    score: float = 1.0


class IncidentStore:
    """失败记忆存储：markdown 文件 + 共享向量索引（note_type=incident）。"""

    def __init__(
        self,
        *,
        memory_dir: Path | str = "data/memory/incidents",
        vector_store: "MemoryVectorStore | None" = None,
    ) -> None:
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._vector_store = vector_store
        self._file_lock = asyncio.Lock()

    async def add_incident(
        self,
        *,
        summary: str,
        title: str | None = None,
        source: str = "",
        occurred_at: datetime | None = None,
    ) -> IncidentEntry:
        if not summary.strip():
            raise ValueError("summary 不能为空")

        when = occurred_at or datetime.now(tz=_TAIPEI)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_TAIPEI)
        when_tpe = when.astimezone(_TAIPEI)

        day = when_tpe.date()
        derived_title = (title or summary.splitlines()[0]).strip()[:80]
        incident_id = _build_id(when_tpe, derived_title)

        async with self._file_lock:
            await asyncio.to_thread(
                self._append_section_sync,
                day=day,
                when_tpe=when_tpe,
                title=derived_title,
                summary=summary.strip(),
                source=source,
            )

        if self._vector_store is not None:
            metadata = {
                "note_type": _NOTE_TYPE,
                "trust": "self",
                "created_at": when_tpe.isoformat(),
                "date": day.isoformat(),
                "title": derived_title,
                "source": source,
            }
            try:
                await self._vector_store.add(
                    note_id=incident_id,
                    content=f"{derived_title}\n\n{summary.strip()}",
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning("[incident] vector add failed: %s", exc)

        return IncidentEntry(
            incident_id=incident_id,
            date=day.isoformat(),
            title=derived_title,
            summary=summary.strip(),
        )

    async def query(
        self, query_text: str, top_k: int = 5,
    ) -> list[IncidentEntry]:
        if not query_text.strip() or self._vector_store is None:
            return []
        raw = await self._vector_store.recall(
            query_text, top_k=top_k,
            where={"note_type": _NOTE_TYPE},
        )
        return [
            IncidentEntry(
                incident_id=hit.note_id,
                date=(getattr(hit, "metadata", None) or {}).get("date", ""),
                title=(getattr(hit, "metadata", None) or {}).get("title", ""),
                summary=hit.content,
                score=hit.score,
            )
            for hit in raw
        ]

    def _append_section_sync(
        self, *, day, when_tpe, title, summary, source,
    ) -> None:
        path = self._dir / f"{day.isoformat()}.md"
        hhmm = when_tpe.strftime("%H:%M")
        src_tag = f" [{source}]" if source else ""
        section = (
            f"## {hhmm} — {title}{src_tag}\n\n"
            f"{summary}\n\n"
        )
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if not existing.endswith("\n"):
                existing += "\n"
            path.write_text(existing + section, encoding="utf-8")
        else:
            header = f"# {day.isoformat()} 异常事件记录\n\n"
            path.write_text(header + section, encoding="utf-8")


def looks_like_failure(text: str) -> bool:
    """Rule-based check: does text look like a failure/error report?"""
    return bool(_FAILURE_KEYWORDS.search(text))


def _build_id(when_tpe: datetime, title: str) -> str:
    stamp = when_tpe.strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:6]
    return f"inc_{stamp}_{digest}"
