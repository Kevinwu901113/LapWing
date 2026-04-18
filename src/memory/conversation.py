"""对话记忆管理（SQLite 持久化 + 内存缓存）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from src.core.trajectory_store import TrajectoryStore

logger = logging.getLogger("lapwing.memory.conversation")

_VALID_RECURRENCE_TYPES = {"once", "daily", "weekly", "interval"}
_TIME_OF_DAY_PATTERN = re.compile(r"^\d{2}:\d{2}$")

class ConversationMemory:
    """管理对话历史的存取。

    采用内存缓存 + SQLite 持久化的混合架构：
    - 启动时从 DB 加载最近历史到内存缓存
    - 读取走缓存（快）
    - 每次写入同时更新缓存和 DB（持久）
    - DB 操作失败只记录日志，不影响当前会话
    """

    ACTIVE_WINDOW_DAYS = 1
    RECENT_ARCHIVE_DAYS = 7

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._store: dict[str, list[dict]] = {}
        self._db: aiosqlite.Connection | None = None
        # Domain repositories (initialized in init_db)
        self._todos: "TodoRepository | None" = None
        self._reminders_repo: "ReminderRepository | None" = None
        # v2.0 Step 2f: optional dual-write target. Injected by AppContainer
        # after TrajectoryStore is initialized. When set, every conversations
        # insert also lands as a trajectory entry; writes to the legacy
        # table remain the truth-source until Step 2g flips reads.
        self._trajectory: "TrajectoryStore | None" = None

    async def init_db(self) -> None:
        """初始化数据库：创建目录、建表。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()
        logger.info(f"对话记忆已初始化（SQLite 模式），数据库: {self._db_path}")

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    TEXT NOT NULL,
                fact_key   TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(chat_id, fact_key)
            );
            CREATE INDEX IF NOT EXISTS idx_user_facts_chat_id
                ON user_facts(chat_id);

            CREATE TABLE IF NOT EXISTS discoveries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id       TEXT NOT NULL,
                source        TEXT NOT NULL,
                title         TEXT NOT NULL,
                summary       TEXT NOT NULL,
                url           TEXT,
                discovered_at TEXT NOT NULL,
                shared_at     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_discoveries_chat_id
                ON discoveries(chat_id);
            CREATE INDEX IF NOT EXISTS idx_discoveries_shared
                ON discoveries(chat_id, shared_at);

            CREATE TABLE IF NOT EXISTS interest_topics (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id   TEXT NOT NULL,
                topic     TEXT NOT NULL,
                weight    REAL NOT NULL DEFAULT 1.0,
                last_seen TEXT NOT NULL,
                UNIQUE(chat_id, topic)
            );
            CREATE INDEX IF NOT EXISTS idx_interest_topics_chat_id
                ON interest_topics(chat_id);

            CREATE TABLE IF NOT EXISTS todos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    TEXT NOT NULL,
                content    TEXT NOT NULL,
                due_date   TEXT,
                done       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_todos_chat_id
                ON todos(chat_id);
            CREATE INDEX IF NOT EXISTS idx_todos_chat_done_due
                ON todos(chat_id, done, due_date, created_at);

            CREATE TABLE IF NOT EXISTS reminders (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id           TEXT NOT NULL,
                content           TEXT NOT NULL,
                recurrence_type   TEXT NOT NULL,
                next_trigger_at   TEXT NOT NULL,
                weekday           INTEGER,
                time_of_day       TEXT,
                active            INTEGER NOT NULL DEFAULT 1,
                created_at        TEXT NOT NULL,
                last_triggered_at TEXT,
                cancelled_at      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_chat_id
                ON reminders(chat_id);
            CREATE INDEX IF NOT EXISTS idx_reminders_active_next
                ON reminders(active, next_trigger_at);
        """)
        await self._db.commit()

        # Migration: add interval_minutes column to reminders if missing
        try:
            await self._db.execute(
                "ALTER TABLE reminders ADD COLUMN interval_minutes INTEGER"
            )
            await self._db.commit()
        except Exception:
            pass  # Column already exists

        # Migration: add execution_mode column to reminders if missing
        try:
            await self._db.execute(
                "ALTER TABLE reminders ADD COLUMN execution_mode TEXT DEFAULT 'notify'"
            )
            await self._db.commit()
        except Exception:
            pass  # Column already exists

    async def get(self, channel_id: str) -> list[dict]:
        """获取指定频道的对话历史（从缓存读取）。"""
        if channel_id not in self._store:
            self._store[channel_id] = []
        return self._store[channel_id]

    def set_trajectory(self, trajectory: "TrajectoryStore | None") -> None:
        """v2.0 Step 2f: wire dual-write target. Safe to call multiple times."""
        self._trajectory = trajectory

    async def append(
        self, channel_id: str, role: str, content: str, *,
        channel: str = "qq", source: str = "qq",
        trust_level: int = 3, actor_id: str | None = None,
    ) -> None:
        """追加一条消息。

        v2.0 Step 2h: the legacy ``conversations`` table is no longer written
        to. When a ``TrajectoryStore`` is wired (production), the message is
        persisted there; the in-memory ``_store`` cache is updated only as a
        fallback for unit tests and phase-0 contexts with no trajectory.

        The parameters ``channel``, ``source``, ``trust_level`` and
        ``actor_id`` remain in the signature for caller compatibility;
        ``trust_level`` is unused in trajectory (Step 3 StateSerializer
        re-derives it from AuthorityGate context on demand).
        """
        if self._trajectory is not None:
            await self._mirror_to_trajectory(
                channel_id, role, content,
                channel=channel, source=source,
                actor_id=actor_id,
            )
            return

        # Fallback: no trajectory (phase 0, unit tests). Update the cache so
        # brain._load_history's fallback branch can still read history.
        if channel_id not in self._store:
            self._store[channel_id] = []
        self._store[channel_id].append({"role": role, "content": content})

    def replace_history(self, channel_id: str, new_history: list[dict]) -> None:
        """Update the legacy in-memory cache only.

        v2.0 Step 2h: compactor no longer calls this in production
        (Step 2g switched compactor's read path to trajectory). Kept as a
        cache hook for phase-0 / unit-test scenarios. Does not touch
        trajectory — compaction of the event-sourced timeline is Step 7.
        """
        self._store[channel_id] = new_history

    async def remove_last(self, channel_id: str) -> None:
        """No-op in v2.0 Step 2h. Trajectory is append-only.

        Previously rolled back the last appended row on LLM failure. Under
        event sourcing, a failed LLM turn is still part of the behavioural
        record (``mutation_log`` already captures the ``LLM_REQUEST`` /
        ``LLM_RESPONSE`` or its exception), so no retraction is needed.
        The cache is trimmed if a trajectory-less fallback context is in
        use (phase-0 / unit tests) so those paths keep their pre-Step-2
        semantics.
        """
        if self._trajectory is None:
            history = self._store.get(channel_id, [])
            if history:
                history.pop()
            return
        logger.debug(
            "remove_last ignored for chat %s — trajectory is append-only "
            "(LLM failure tracked in mutation_log)",
            channel_id,
        )

    async def clear(self, channel_id: str) -> None:
        """清除指定频道的内存对话缓存。

        The durable conversation history lives in TrajectoryStore after
        Step 2h, and trajectory is append-only by Blueprint contract.
        ``clear()`` therefore only resets the in-process cache — it no
        longer deletes anything in the database. Callers that truly
        want to wipe historical rows must go through a scripted
        migration with an audit trail, not a runtime clear.
        """
        self._store.pop(channel_id, None)
        logger.info(f"已清除频道 {channel_id} 的内存对话缓存")

    async def _mirror_to_trajectory(
        self,
        chat_id: str,
        role: str,
        content: str,
        *,
        channel: str,
        source: str,
        actor_id: str | None,
    ) -> None:
        """Write the same logical event to trajectory. No-op if no trajectory
        wired (unit tests, phase 0, pre-container boot).

        Inner-tick writes (chat_id == "__inner__", set by consciousness.py
        after v2.0 Step 2i) are mapped to INNER_THOUGHT so the output
        matches the Step 2e migration categorisation. Step 4's main-loop
        unification replaces this sentinel-based dispatch with a dedicated
        inner-turn entry point on brain.
        """
        if self._trajectory is None:
            return
        try:
            from src.core.trajectory_store import TrajectoryEntryType

            is_consciousness = chat_id == "__inner__"

            if is_consciousness:
                entry_type = TrajectoryEntryType.INNER_THOUGHT
                source_chat_id = "__inner__"
                if role == "assistant":
                    actor = "lapwing"
                elif role == "user":
                    actor = "system"
                else:
                    logger.warning(
                        "trajectory mirror skipped — unknown consciousness role %r",
                        role,
                    )
                    return
                payload: dict = {
                    "text": content,
                    "trigger_type": "live_dual_write",
                }
            elif role == "user":
                entry_type = TrajectoryEntryType.USER_MESSAGE
                source_chat_id = chat_id
                actor = "user"
                payload = {"text": content, "adapter": channel, "source": source}
            elif role == "assistant":
                entry_type = TrajectoryEntryType.ASSISTANT_TEXT
                source_chat_id = chat_id
                actor = "lapwing"
                payload = {"text": content, "adapter": channel, "source": source}
            else:
                logger.warning(
                    "trajectory mirror skipped — unknown role %r for chat %s",
                    role, chat_id,
                )
                return

            if actor_id:
                payload["user_id"] = actor_id

            await self._trajectory.append(
                entry_type, source_chat_id, actor, payload,
            )
        except Exception:
            logger.warning(
                "trajectory mirror write failed for chat %s (role=%s)",
                chat_id, role, exc_info=True,
            )

    async def clear_chat_all(self, channel_id: str) -> None:
        """清除指定频道的可变记忆：内存缓存 + todos/reminders。

        Trajectory rows stay put (append-only). Callers that want to
        erase trajectory history must run a dedicated migration.
        """
        self._store.pop(channel_id, None)
        tables = (
            "todos",
            "reminders",
        )
        try:
            for table in tables:
                await self._db.execute(
                    f"DELETE FROM {table} WHERE chat_id = ?",
                    (channel_id,),
                )
            await self._db.commit()
            logger.info(f"已清除频道 {channel_id} 的可变记忆（缓存 + todos + reminders）")
        except Exception as e:
            logger.error(f"清除频道 {channel_id} 可变记忆失败: {e}")

    async def clear_all(self) -> None:
        """清除所有内存对话缓存。Trajectory 不动（append-only）。"""
        self._store.clear()
        logger.info("已清除所有内存对话缓存")

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("数据库连接已关闭")

    # ===== Facade delegation to domain repositories =====

    async def add_todo(self, chat_id: str, content: str, due_date: str | None = None) -> int:
        return await self._todos.add_todo(chat_id, content, due_date)

    async def list_todos(self, chat_id: str) -> list[dict]:
        return await self._todos.list_todos(chat_id)

    async def mark_todo_done(self, chat_id: str, todo_id: int) -> bool:
        return await self._todos.mark_todo_done(chat_id, todo_id)

    async def delete_todo(self, chat_id: str, todo_id: int) -> bool:
        return await self._todos.delete_todo(chat_id, todo_id)

    async def add_reminder(self, chat_id: str, content: str, recurrence_type: str, next_trigger_at, weekday: int | None = None, time_of_day: str | None = None, interval_minutes: int | None = None) -> int:
        return await self._reminders_repo.add_reminder(chat_id, content, recurrence_type, next_trigger_at, weekday, time_of_day, interval_minutes)

    async def list_reminders(self, chat_id: str, include_inactive: bool = False) -> list[dict]:
        return await self._reminders_repo.list_reminders(chat_id, include_inactive)

    async def cancel_reminder(self, chat_id: str, reminder_id: int) -> bool:
        return await self._reminders_repo.cancel_reminder(chat_id, reminder_id)

    async def get_due_reminders(self, chat_id: str, now, grace_seconds: int, limit: int = 20) -> list[dict]:
        return await self._reminders_repo.get_due_reminders(chat_id, now, grace_seconds, limit)

    async def complete_or_reschedule_reminder(self, reminder_id: int, now) -> bool:
        return await self._reminders_repo.complete_or_reschedule_reminder(reminder_id, now)

    async def get_reminder_by_id(self, reminder_id: int) -> dict | None:
        return await self._reminders_repo.get_reminder_by_id(reminder_id)
