"""FocusManager — topic-level attention over the trajectory timeline."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite

from config.settings import (
    FOCUS_CLOSED_TTL_HOURS,
    FOCUS_DORMANT_TTL_HOURS,
    FOCUS_ENABLED,
    FOCUS_MAX_DORMANT,
    FOCUS_MIN_ENTRIES_TO_KEEP,
    FOCUS_RAPID_GAP_SECONDS,
    FOCUS_REACTIVATE_THRESHOLD,
    FOCUS_TIMEOUT_SECONDS,
)
from src.core.prompt_loader import load_prompt
from src.core.trajectory_store import TrajectoryEntry, TrajectoryEntryType
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    current_iteration_id,
)

logger = logging.getLogger("lapwing.core.focus_manager")

FOCUS_COLLECTION_NAME = "focus_summaries"


class FocusStatus(str, Enum):
    ACTIVE = "active"
    DORMANT = "dormant"
    CLOSED = "closed"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Focus:
    id: str
    summary: str | None
    keywords: tuple[str, ...]
    primary_chat_id: str | None
    status: FocusStatus
    started_at: float
    last_active_at: float
    closed_at: float | None
    entry_count: int
    parent_focus_id: str | None
    archive_ref_id: str | None


class FocusManager:
    """Owns focus lifecycle and the in-memory active/dormant indexes."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        trajectory_store: Any,
        attention_manager: Any,
        llm_router: Any,
        vector_store: Any,
        archiver: Any,
        episodic_extractor: Any | None = None,
        mutation_log: StateMutationLog | None = None,
        enabled: bool = FOCUS_ENABLED,
    ) -> None:
        self._db_path = Path(db_path)
        self._trajectory_store = trajectory_store
        self._attention_manager = attention_manager
        self._llm_router = llm_router
        self._vector_store = vector_store
        self._archiver = archiver
        self._episodic_extractor = episodic_extractor
        self._mutation_log = mutation_log
        self.enabled = enabled

        self._db: aiosqlite.Connection | None = None
        self._active_focuses: dict[str, str] = {}
        self._dormant_focuses: list[Focus] = []
        self._locks: dict[str, asyncio.Lock] = {}

    async def init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS focuses (
                id TEXT PRIMARY KEY,
                summary TEXT,
                keywords_json TEXT,
                primary_chat_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                started_at REAL NOT NULL,
                last_active_at REAL NOT NULL,
                closed_at REAL,
                entry_count INTEGER NOT NULL DEFAULT 0,
                parent_focus_id TEXT,
                archive_ref_id TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_focuses_status_chat
                ON focuses(status, primary_chat_id);
            CREATE INDEX IF NOT EXISTS idx_focuses_status_last_active
                ON focuses(status, last_active_at);
            """
        )
        if await self._add_column_if_table_exists("trajectory", "focus_id", "TEXT"):
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_trajectory_focus "
                "ON trajectory(focus_id, timestamp)"
            )
        await self._add_column_if_table_exists(
            "commitments", "source_focus_id", "TEXT"
        )
        await self._db.commit()

    async def close_db(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def startup_load(self) -> None:
        if self._db is None or not self.enabled:
            return
        self._active_focuses.clear()
        self._dormant_focuses.clear()

        active_rows = await self._fetch_focuses(
            "status = ?",
            (FocusStatus.ACTIVE.value,),
            order="last_active_at DESC",
        )
        for focus in active_rows:
            if time.time() - focus.last_active_at > FOCUS_TIMEOUT_SECONDS:
                await self.deactivate(focus.id)
            elif focus.primary_chat_id:
                self._active_focuses[focus.primary_chat_id] = focus.id

        dormant = await self._fetch_focuses(
            "status = ?",
            (FocusStatus.DORMANT.value,),
            order="last_active_at DESC",
            limit=FOCUS_MAX_DORMANT,
        )
        self._dormant_focuses = sorted(
            dormant, key=lambda item: item.last_active_at
        )

    async def resolve_focus(self, chat_id: str, user_message: str) -> Focus:
        if not self.enabled:
            return await self._create_focus(chat_id)
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            focus = await self.get_active_focus(chat_id)
            if focus is None:
                matched = await self._find_matching_dormant(user_message)
                if matched is not None:
                    return await self.reactivate(matched.id, chat_id)
                return await self._create_focus(chat_id)

            if time.time() - focus.last_active_at > FOCUS_TIMEOUT_SECONDS:
                await self.deactivate(focus.id)
                matched = await self._find_matching_dormant(user_message)
                if matched is not None:
                    return await self.reactivate(matched.id, chat_id)
                return await self._create_focus(chat_id)

            topic_changed = await self._detect_topic_change(focus, user_message)
            if topic_changed:
                await self.deactivate(focus.id)
                matched = await self._find_matching_dormant(user_message)
                if matched is not None:
                    return await self.reactivate(matched.id, chat_id)
                return await self._create_focus(chat_id)

            return await self._touch(focus.id)

    async def accumulate(self, focus_id: str) -> None:
        if self._db is None or not focus_id:
            return
        now = time.time()
        await self._db.execute(
            """
            UPDATE focuses
            SET entry_count = entry_count + 1, last_active_at = ?
            WHERE id = ? AND status = ?
            """,
            (now, focus_id, FocusStatus.ACTIVE.value),
        )
        await self._db.commit()
        focus = await self._get_focus(focus_id)
        if focus and focus.primary_chat_id:
            self._active_focuses[focus.primary_chat_id] = focus.id
        await self._set_attention_focus(focus_id)

    async def deactivate(self, focus_id: str) -> None:
        focus = await self._get_focus(focus_id)
        if focus is None or focus.status not in (FocusStatus.ACTIVE, FocusStatus.DORMANT):
            return
        if focus.entry_count < FOCUS_MIN_ENTRIES_TO_KEEP:
            await self.close(focus_id)
            return

        entries = await self._trajectory_store.entries_by_focus(focus_id, n=20)
        summary, keywords = await self._generate_summary(entries)
        await self._update_focus(
            focus_id,
            status=FocusStatus.DORMANT.value,
            summary=summary,
            keywords_json=json.dumps(list(keywords), ensure_ascii=False),
            closed_at=None,
        )
        updated = await self._get_focus(focus_id)
        if updated is None:
            return

        if focus.primary_chat_id:
            self._active_focuses.pop(focus.primary_chat_id, None)
        self._dormant_focuses = [
            item for item in self._dormant_focuses if item.id != focus_id
        ]
        self._dormant_focuses.append(updated)
        self._dormant_focuses.sort(key=lambda item: item.last_active_at)

        await self._upsert_focus_summary(updated)
        await self._trigger_episodic_extraction(entries)

        while len(self._dormant_focuses) > FOCUS_MAX_DORMANT:
            oldest = self._dormant_focuses.pop(0)
            await self.close(oldest.id)

        await self._record_event(
            "focus_deactivated",
            {"focus_id": focus_id, "summary": summary},
            chat_id=focus.primary_chat_id,
        )

    async def reactivate(self, focus_id: str, chat_id: str) -> Focus:
        focus = await self._get_focus(focus_id)
        if focus is None:
            return await self._create_focus(chat_id)
        now = time.time()
        await self._update_focus(
            focus_id,
            status=FocusStatus.ACTIVE.value,
            primary_chat_id=chat_id,
            last_active_at=now,
            closed_at=None,
        )
        self._dormant_focuses = [
            item for item in self._dormant_focuses if item.id != focus_id
        ]
        self._active_focuses[chat_id] = focus_id
        await self._set_attention_focus(focus_id)
        await self._record_event(
            "focus_reactivated",
            {"focus_id": focus_id, "chat_id": chat_id},
            chat_id=chat_id,
        )
        updated = await self._get_focus(focus_id)
        return updated if updated is not None else focus

    async def close(self, focus_id: str) -> None:
        focus = await self._get_focus(focus_id)
        if focus is None or focus.status == FocusStatus.ARCHIVED:
            return
        now = time.time()
        await self._update_focus(
            focus_id,
            status=FocusStatus.CLOSED.value,
            closed_at=now,
        )
        if focus.primary_chat_id:
            self._active_focuses.pop(focus.primary_chat_id, None)
        self._dormant_focuses = [
            item for item in self._dormant_focuses if item.id != focus_id
        ]
        if self._attention_manager is not None:
            state = self._attention_manager.get()
            if getattr(state, "current_focus_id", None) == focus_id:
                await self._attention_manager.update(current_focus_id=None)
        await self._record_event(
            "focus_closed",
            {"focus_id": focus_id},
            chat_id=focus.primary_chat_id,
        )

    async def archive(self, focus_id: str) -> None:
        focus = await self._get_focus(focus_id)
        if focus is None or focus.status == FocusStatus.ARCHIVED:
            return
        entries = await self._trajectory_store.entries_by_focus(focus_id, n=200)
        archive_ref = await self._archiver.archive(
            entries,
            {
                "focus_id": focus.id,
                "summary": focus.summary,
                "keywords": list(focus.keywords),
            },
        )
        await self._update_focus(
            focus_id,
            status=FocusStatus.ARCHIVED.value,
            archive_ref_id=archive_ref,
            closed_at=focus.closed_at or time.time(),
        )
        await self._record_event(
            "focus_archived",
            {"focus_id": focus_id, "archive_ref_id": archive_ref},
            chat_id=focus.primary_chat_id,
        )

    async def reap_expired(self) -> int:
        if self._db is None:
            return 0
        now = time.time()
        count = 0
        dormant_cutoff = now - FOCUS_DORMANT_TTL_HOURS * 3600
        closed_cutoff = now - FOCUS_CLOSED_TTL_HOURS * 3600

        dormant = await self._fetch_focuses(
            "status = ? AND last_active_at < ?",
            (FocusStatus.DORMANT.value, dormant_cutoff),
            order="last_active_at ASC",
        )
        for focus in dormant:
            await self.close(focus.id)
            count += 1

        closed = await self._fetch_focuses(
            "status = ? AND COALESCE(closed_at, last_active_at) < ?",
            (FocusStatus.CLOSED.value, closed_cutoff),
            order="COALESCE(closed_at, last_active_at) ASC",
        )
        for focus in closed:
            await self.archive(focus.id)
            count += 1
        return count

    async def deactivate_expired_active(self) -> int:
        count = 0
        for _chat_id, focus_id in list(self._active_focuses.items()):
            focus = await self._get_focus(focus_id)
            if focus and time.time() - focus.last_active_at > FOCUS_TIMEOUT_SECONDS:
                await self.deactivate(focus_id)
                count += 1
        return count

    async def get_active_focus(self, chat_id: str) -> Focus | None:
        focus_id = self._active_focuses.get(chat_id)
        if focus_id:
            focus = await self._get_focus(focus_id)
            if focus and focus.status == FocusStatus.ACTIVE:
                return focus
            self._active_focuses.pop(chat_id, None)
        rows = await self._fetch_focuses(
            "status = ? AND primary_chat_id = ?",
            (FocusStatus.ACTIVE.value, chat_id),
            order="last_active_at DESC",
            limit=1,
        )
        if rows:
            self._active_focuses[chat_id] = rows[0].id
            return rows[0]
        return None

    async def get_dormant_summaries(self, n: int = 3) -> list[str]:
        dormant = sorted(
            self._dormant_focuses, key=lambda item: item.last_active_at, reverse=True
        )
        return [item.summary for item in dormant[:n] if item.summary]

    async def search_history(self, query: str, n: int = 3) -> list[Focus]:
        if not query.strip():
            return []
        try:
            hits = await self._vector_store.query_collection(
                collection=FOCUS_COLLECTION_NAME,
                query_text=query,
                n_results=n,
            )
        except Exception:
            logger.debug("focus vector search failed", exc_info=True)
            hits = []
        out: list[Focus] = []
        for hit in hits:
            focus_id = hit.metadata.get("focus_id") or hit.doc_id
            focus = await self._get_focus(str(focus_id))
            if focus is not None:
                out.append(focus)
        return out

    async def list_focuses(self, status: FocusStatus) -> list[Focus]:
        return await self._fetch_focuses(
            "status = ?",
            (status.value,),
            order="last_active_at DESC",
        )

    async def _create_focus(self, chat_id: str | None) -> Focus:
        if self._db is None:
            raise RuntimeError("FocusManager not initialized; call init_db() first")
        now = time.time()
        focus = Focus(
            id=str(uuid.uuid4()),
            summary=None,
            keywords=(),
            primary_chat_id=chat_id,
            status=FocusStatus.ACTIVE,
            started_at=now,
            last_active_at=now,
            closed_at=None,
            entry_count=0,
            parent_focus_id=None,
            archive_ref_id=None,
        )
        await self._db.execute(
            """
            INSERT INTO focuses
                (id, summary, keywords_json, primary_chat_id, status,
                 started_at, last_active_at, closed_at, entry_count,
                 parent_focus_id, archive_ref_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                focus.id,
                focus.summary,
                json.dumps([], ensure_ascii=False),
                focus.primary_chat_id,
                focus.status.value,
                focus.started_at,
                focus.last_active_at,
                focus.closed_at,
                focus.entry_count,
                focus.parent_focus_id,
                focus.archive_ref_id,
                now,
            ),
        )
        await self._db.commit()
        if chat_id:
            self._active_focuses[chat_id] = focus.id
        await self._set_attention_focus(focus.id)
        await self._record_event(
            "focus_created",
            {"focus_id": focus.id, "chat_id": chat_id},
            chat_id=chat_id,
        )
        return focus

    async def _touch(self, focus_id: str) -> Focus:
        await self._update_focus(focus_id, last_active_at=time.time())
        focus = await self._get_focus(focus_id)
        if focus is None:
            raise KeyError(f"focus {focus_id!r} not found")
        if focus.primary_chat_id:
            self._active_focuses[focus.primary_chat_id] = focus.id
        await self._set_attention_focus(focus_id)
        return focus

    async def _detect_topic_change(
        self,
        focus: Focus,
        user_message: str,
    ) -> bool:
        gap = time.time() - focus.last_active_at
        if gap < FOCUS_RAPID_GAP_SECONDS:
            return False
        try:
            recent_entries = await self._trajectory_store.entries_by_focus(
                focus.id, n=10
            )
            prompt = self._build_continuity_prompt(
                focus, recent_entries, user_message
            )
            result = await self._llm_router.complete(
                [{"role": "user", "content": prompt}],
                slot="memory_processing",
                max_tokens=10,
                origin="focus_manager.topic_detect",
            )
        except Exception:
            logger.debug("focus topic detection failed; default SAME", exc_info=True)
            return False
        text = (result or "SAME").strip().upper()
        return text.startswith("NEW")

    async def _find_matching_dormant(self, user_message: str) -> Focus | None:
        if not self._dormant_focuses:
            return None
        try:
            hits = await self._vector_store.query_collection(
                collection=FOCUS_COLLECTION_NAME,
                query_text=user_message,
                n_results=1,
            )
            if hits and hits[0].score >= FOCUS_REACTIVATE_THRESHOLD:
                matched_id = str(hits[0].metadata.get("focus_id") or hits[0].doc_id)
                return next(
                    (item for item in self._dormant_focuses if item.id == matched_id),
                    None,
                )
            return None
        except Exception:
            logger.debug("focus dormant vector match failed; using LLM fallback", exc_info=True)
            return await self._find_matching_dormant_llm(user_message)

    async def _find_matching_dormant_llm(self, user_message: str) -> Focus | None:
        if not self._dormant_focuses:
            return None
        dormant_list = "\n".join(
            f"{idx + 1}. {focus.summary or '未命名'} [{', '.join(focus.keywords)}]"
            for idx, focus in enumerate(self._dormant_focuses)
        )
        try:
            template = load_prompt("focus_match_dormant")
        except Exception:
            template = _FOCUS_MATCH_FALLBACK
        prompt = template.format(dormant_list=dormant_list, user_message=user_message)
        try:
            result = await self._llm_router.complete(
                [{"role": "user", "content": prompt}],
                slot="memory_processing",
                max_tokens=10,
                origin="focus_manager.dormant_match_llm",
            )
        except Exception:
            return None
        text = (result or "NONE").strip()
        if text.upper() == "NONE":
            return None
        try:
            idx = int(text) - 1
        except ValueError:
            return None
        if 0 <= idx < len(self._dormant_focuses):
            return self._dormant_focuses[idx]
        return None

    async def _generate_summary(
        self,
        entries: list[TrajectoryEntry],
    ) -> tuple[str, tuple[str, ...]]:
        conversation_text = _format_entries(entries)
        if not conversation_text:
            return "空焦点", ("空焦点",)
        try:
            template = load_prompt("focus_summarize")
        except Exception:
            template = _FOCUS_SUMMARIZE_FALLBACK
        prompt = template.format(conversation_text=conversation_text)
        try:
            raw = await self._llm_router.complete(
                [{"role": "user", "content": prompt}],
                slot="memory_processing",
                max_tokens=120,
                origin="focus_manager.summarize",
            )
        except Exception:
            raw = ""
        summary, keywords = _parse_summary(raw)
        if not summary:
            summary = _fallback_summary(conversation_text)
        if not keywords:
            keywords = tuple(_fallback_keywords(conversation_text))
        return summary[:10], tuple(keywords[:5])

    def _build_continuity_prompt(
        self,
        focus: Focus,
        recent_entries: list[TrajectoryEntry],
        user_message: str,
    ) -> str:
        try:
            template = load_prompt("focus_continuity")
        except Exception:
            template = _FOCUS_CONTINUITY_FALLBACK
        return template.format(
            focus_summary=focus.summary or "刚开始的对话",
            recent_entries=_format_entries(recent_entries),
            user_message=user_message,
        )

    async def _trigger_episodic_extraction(
        self,
        entries: list[TrajectoryEntry],
    ) -> None:
        extractor = self._episodic_extractor
        if extractor is None:
            return
        try:
            if hasattr(extractor, "extract_from_entries"):
                await extractor.extract_from_entries(entries)
        except Exception:
            logger.warning("focus dormant episodic extraction failed", exc_info=True)

    async def _upsert_focus_summary(self, focus: Focus) -> None:
        text = f"{focus.summary or ''} {' '.join(focus.keywords)}".strip()
        if not text:
            return
        try:
            await self._vector_store.upsert_collection(
                collection=FOCUS_COLLECTION_NAME,
                doc_id=focus.id,
                text=text,
                metadata={"focus_id": focus.id, "summary": focus.summary or ""},
            )
        except Exception:
            logger.debug("focus summary vector upsert failed", exc_info=True)

    async def _get_focus(self, focus_id: str) -> Focus | None:
        rows = await self._fetch_focuses("id = ?", (focus_id,), limit=1)
        return rows[0] if rows else None

    async def _fetch_focuses(
        self,
        where: str,
        params: tuple[Any, ...],
        *,
        order: str = "last_active_at DESC",
        limit: int | None = None,
    ) -> list[Focus]:
        if self._db is None:
            return []
        sql = (
            "SELECT id, summary, keywords_json, primary_chat_id, status, "
            "started_at, last_active_at, closed_at, entry_count, "
            "parent_focus_id, archive_ref_id FROM focuses "
            f"WHERE {where} ORDER BY {order}"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_focus(row) for row in rows]

    async def _update_focus(self, focus_id: str, **fields: Any) -> None:
        if self._db is None or not fields:
            return
        allowed = {
            "summary", "keywords_json", "primary_chat_id", "status",
            "started_at", "last_active_at", "closed_at", "entry_count",
            "parent_focus_id", "archive_ref_id",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown focus fields: {sorted(unknown)}")
        assignments = ", ".join(f"{field} = ?" for field in fields)
        values = list(fields.values())
        values.append(focus_id)
        await self._db.execute(
            f"UPDATE focuses SET {assignments} WHERE id = ?",
            values,
        )
        await self._db.commit()

    async def _add_column_if_table_exists(
        self,
        table: str,
        column: str,
        sql_type: str,
    ) -> bool:
        if self._db is None:
            return False
        async with self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ) as cur:
            exists = await cur.fetchone()
        if not exists:
            return False
        async with self._db.execute(f"PRAGMA table_info({table})") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if column not in cols:
            await self._db.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
            )
        return True

    async def _set_attention_focus(self, focus_id: str | None) -> None:
        if self._attention_manager is None:
            return
        try:
            await self._attention_manager.update(current_focus_id=focus_id)
        except Exception:
            logger.debug("attention focus update failed", exc_info=True)

    async def _record_event(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        chat_id: str | None = None,
    ) -> None:
        if self._mutation_log is None:
            return
        try:
            await self._mutation_log.record(
                MutationType.FOCUS_CHANGED,
                {"event": event, **payload},
                iteration_id=current_iteration_id(),
                chat_id=chat_id,
            )
        except Exception:
            logger.debug("focus mutation record failed", exc_info=True)

    @staticmethod
    def _row_to_focus(row: tuple) -> Focus:
        try:
            keywords = tuple(json.loads(row[2] or "[]"))
        except (TypeError, json.JSONDecodeError):
            keywords = ()
        status = FocusStatus(row[4])
        return Focus(
            id=row[0],
            summary=row[1],
            keywords=keywords,
            primary_chat_id=row[3],
            status=status,
            started_at=row[5],
            last_active_at=row[6],
            closed_at=row[7],
            entry_count=int(row[8] or 0),
            parent_focus_id=row[9],
            archive_ref_id=row[10],
        )


def _format_entries(entries: list[TrajectoryEntry]) -> str:
    lines: list[str] = []
    for entry in entries:
        text = _entry_text(entry)
        if not text:
            continue
        if entry.entry_type == TrajectoryEntryType.USER_MESSAGE.value:
            label = "Kevin"
        elif entry.entry_type in (
            TrajectoryEntryType.TELL_USER.value,
            TrajectoryEntryType.ASSISTANT_TEXT.value,
        ):
            label = "我"
        elif entry.entry_type == TrajectoryEntryType.TOOL_CALL.value:
            label = "工具调用"
        elif entry.entry_type == TrajectoryEntryType.TOOL_RESULT.value:
            label = "工具结果"
        else:
            continue
        lines.append(f"{label}：{text}")
    return "\n".join(lines)


def _entry_text(entry: TrajectoryEntry) -> str:
    content = entry.content or {}
    if entry.entry_type == TrajectoryEntryType.TELL_USER.value:
        messages = content.get("messages")
        if isinstance(messages, list) and messages:
            return "\n".join(str(item) for item in messages)
    if entry.entry_type == TrajectoryEntryType.TOOL_CALL.value:
        return str(content.get("tool_name") or content.get("text") or "")
    if entry.entry_type == TrajectoryEntryType.TOOL_RESULT.value:
        return str(content.get("result_preview") or content.get("text") or "")
    text = content.get("text")
    return text if isinstance(text, str) else ""


def _parse_summary(raw: str | None) -> tuple[str, tuple[str, ...]]:
    if not raw:
        return "", ()
    line = raw.strip().splitlines()[0].strip()
    if "|" not in line:
        return line[:10], ()
    summary, keywords_raw = line.split("|", 1)
    keywords: tuple[str, ...] = ()
    try:
        parsed = json.loads(keywords_raw.strip())
        if isinstance(parsed, list):
            keywords = tuple(str(item).strip() for item in parsed if str(item).strip())
    except json.JSONDecodeError:
        keywords = tuple(
            item.strip() for item in keywords_raw.split(",") if item.strip()
        )
    return summary.strip(), keywords


def _fallback_summary(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:10] or "未命名焦点"


def _fallback_keywords(text: str) -> list[str]:
    tokens = [
        token.strip("：:，,。.!?？[]（）()")
        for token in text.replace("\n", " ").split()
    ]
    out: list[str] = []
    for token in tokens:
        if len(token) < 2 or token in out:
            continue
        out.append(token[:12])
        if len(out) >= 5:
            break
    return out or ["对话"]


_FOCUS_CONTINUITY_FALLBACK = """你是话题判断器。判断新消息是否在继续当前焦点。

当前焦点：{focus_summary}
最近对话：
{recent_entries}

新消息：{user_message}

只回答一个词：SAME 或 NEW
"""

_FOCUS_MATCH_FALLBACK = """以下是休眠中的焦点：
{dormant_list}

新消息：{user_message}

这条消息是否在回到某个休眠焦点？
回答编号（如 1），或 NONE。只回答一行。
"""

_FOCUS_SUMMARIZE_FALLBACK = """以下是一段对话：
{conversation_text}

请用 10 个字以内概括核心话题，并提取 3-5 个关键词。
格式：话题描述|["关键词1","关键词2","关键词3"]
"""
