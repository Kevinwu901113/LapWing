"""对话记忆管理（SQLite 持久化 + 内存缓存）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from config.settings import MAX_HISTORY_TURNS

if TYPE_CHECKING:
    from src.core.trajectory_store import TrajectoryStore

logger = logging.getLogger("lapwing.memory.conversation")

_VALID_RECURRENCE_TYPES = {"once", "daily", "weekly", "interval"}
_TIME_OF_DAY_PATTERN = re.compile(r"^\d{2}:\d{2}$")

# CJK 字符范围：基本汉字 + 扩展 A + 兼容汉字
_CJK_RE = re.compile(r"([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff])")


def _cjk_tokenize(text: str) -> str:
    """在 CJK 字符之间插入空格，使 FTS5 unicode61 tokenizer 能正确分词。"""
    return _CJK_RE.sub(r" \1 ", text).strip()


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
        """初始化数据库：创建目录、建表、加载历史。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()
        await self._load_recent_history()
        logger.info(f"对话记忆已初始化（SQLite 模式），数据库: {self._db_path}")

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id   TEXT NOT NULL,
                role      TEXT NOT NULL,
                content   TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversations_chat_id
                ON conversations(chat_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_timestamp
                ON conversations(timestamp);

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

        # Migration: add channel column if missing
        try:
            await self._db.execute(
                "ALTER TABLE conversations ADD COLUMN channel TEXT DEFAULT 'qq'"
            )
            await self._db.commit()
        except Exception:
            pass  # Column already exists

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

        # v2.0 Step 2j: the ``sessions`` table and its ``conversations.session_id``
        # column are no longer created by new installs. Existing rows are
        # archived + dropped separately by scripts/drop_sessions_table.py.
        # Step 4 re-introduces session semantics bound to attention focus.

        # Phase 1 Migration: conversations 新增 source / trust_level / actor_id
        for col in (
            "source TEXT DEFAULT 'qq'",
            "trust_level INTEGER DEFAULT 3",
            "actor_id TEXT",
        ):
            try:
                await self._db.execute(f"ALTER TABLE conversations ADD COLUMN {col}")
                await self._db.commit()
            except Exception:
                pass  # Column already exists

    async def _load_recent_history(self) -> None:
        """从数据库加载每个对话的最近历史到内存缓存。"""
        max_messages = MAX_HISTORY_TURNS * 2
        async with self._db.execute(
            "SELECT DISTINCT chat_id FROM conversations"
        ) as cursor:
            chat_ids = [row[0] async for row in cursor]

        for chat_id in chat_ids:
            async with self._db.execute(
                """SELECT role, content FROM (
                    SELECT id, role, content FROM conversations
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC""",
                (chat_id, max_messages),
            ) as cursor:
                messages = [
                    {"role": row[0], "content": row[1]}
                    async for row in cursor
                ]
            if messages:
                self._store[chat_id] = messages
                logger.debug(f"已从 DB 加载频道 {chat_id} 的 {len(messages)} 条历史消息")

    async def get(self, channel_id: str) -> list[dict]:
        """获取指定频道的对话历史（从缓存读取）。"""
        if channel_id not in self._store:
            self._store[channel_id] = []
        return self._store[channel_id]

    async def get_messages(
        self,
        chat_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict]:
        """获取分页对话历史（从数据库读取，按时间倒序）。"""
        limit = max(1, min(limit, 500))
        try:
            if before:
                async with self._db.execute(
                    "SELECT id, chat_id, role, content, timestamp "
                    "FROM conversations WHERE chat_id = ? AND timestamp < ? "
                    "ORDER BY id DESC LIMIT ?",
                    (chat_id, before, limit),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with self._db.execute(
                    "SELECT id, chat_id, role, content, timestamp "
                    "FROM conversations WHERE chat_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (chat_id, limit),
                ) as cursor:
                    rows = await cursor.fetchall()

            return [
                {
                    "id": str(row[0]),
                    "chat_id": row[1],
                    "role": row[2],
                    "content": row[3],
                    "timestamp": row[4],
                }
                for row in reversed(rows)  # 返回时按时间正序
            ]
        except Exception as e:
            logger.error(f"获取对话消息失败: {e}")
            return []

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
        """清除指定频道的对话历史。"""
        self._store.pop(channel_id, None)
        try:
            await self._db.execute(
                "DELETE FROM conversations WHERE chat_id = ?",
                (channel_id,),
            )
            await self._db.commit()
            logger.info(f"已清除频道 {channel_id} 的对话记忆")
        except Exception as e:
            logger.error(f"清除频道 {channel_id} 记忆失败: {e}")

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
        """清除指定频道的全部记忆（短期 + 长期）。"""
        self._store.pop(channel_id, None)
        tables = (
            "conversations",
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
            logger.info(f"已清除频道 {channel_id} 的全部记忆（长短期）")
        except Exception as e:
            logger.error(f"清除频道 {channel_id} 全部记忆失败: {e}")

    async def clear_all(self) -> None:
        """清除所有对话历史。"""
        self._store.clear()
        try:
            await self._db.execute("DELETE FROM conversations")
            await self._db.commit()
            logger.info("已清除所有对话记忆")
        except Exception as e:
            logger.error(f"清除所有记忆失败: {e}")

    async def get_active(self, chat_id: str, limit: int = 30) -> list[dict]:
        """获取活跃对话（最近 1 天）用于上下文注入，按时间正序返回。"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.ACTIVE_WINDOW_DAYS)).isoformat()
        try:
            async with self._db.execute(
                "SELECT role, content, timestamp FROM conversations "
                "WHERE chat_id = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT ?",
                (chat_id, cutoff, limit),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                {"role": row[0], "content": row[1], "timestamp": row[2]}
                for row in reversed(rows)
            ]
        except Exception as e:
            logger.error(f"get_active 查询失败: {e}")
            return []

    async def search_deep_archive(self, chat_id: str, query: str, limit: int = 10) -> list[dict]:
        """在深度归档（7 天前）中按关键词搜索对话，按时间倒序返回。"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.RECENT_ARCHIVE_DAYS)).isoformat()
        try:
            async with self._db.execute(
                "SELECT role, content, timestamp FROM conversations "
                "WHERE chat_id = ? AND timestamp < ? AND content LIKE ? ORDER BY timestamp DESC LIMIT ?",
                (chat_id, cutoff, f"%{query}%", limit),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                {"role": row[0], "content": row[1], "timestamp": row[2]}
                for row in rows
            ]
        except Exception as e:
            logger.error(f"search_deep_archive 查询失败: {e}")
            return []

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
