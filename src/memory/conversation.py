"""对话记忆管理（SQLite 持久化 + 内存缓存）。"""

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from config.settings import MAX_HISTORY_TURNS

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

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._store: dict[str, list[dict]] = {}
        self._session_store: dict[str, list[dict]] = {}  # key = session_id
        self._db: aiosqlite.Connection | None = None

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
                "ALTER TABLE conversations ADD COLUMN channel TEXT DEFAULT 'telegram'"
            )
            await self._db.commit()
        except Exception:
            pass  # Column already exists

        # Migration: add session_id column if missing
        try:
            await self._db.execute(
                "ALTER TABLE conversations ADD COLUMN session_id TEXT"
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

        # Sessions table
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id              TEXT PRIMARY KEY,
                chat_id         TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'active',
                topic_summary   TEXT NOT NULL DEFAULT '',
                topic_keywords  TEXT NOT NULL DEFAULT '[]',
                snapshot_path   TEXT,
                created_at      TEXT NOT NULL,
                last_active_at  TEXT NOT NULL,
                condensed_at    TEXT,
                message_count   INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_chat_id_status
                ON sessions(chat_id, status);
            CREATE INDEX IF NOT EXISTS idx_sessions_last_active
                ON sessions(last_active_at);
            CREATE INDEX IF NOT EXISTS idx_conversations_session_id
                ON conversations(session_id);
        """)
        await self._db.commit()

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

    async def append(self, channel_id: str, role: str, content: str, *, channel: str = "telegram") -> None:
        """追加一条消息到对话历史（先写缓存，再持久化）。"""
        if channel_id not in self._store:
            self._store[channel_id] = []
        self._store[channel_id].append({"role": role, "content": content})

        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                "INSERT INTO conversations (chat_id, role, content, timestamp, channel) VALUES (?, ?, ?, ?, ?)",
                (channel_id, role, content, timestamp, channel),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"对话消息写入数据库失败: {e}")

    def replace_history(self, channel_id: str, new_history: list[dict]) -> None:
        """替换指定频道的内存缓存（不修改数据库，供 Compactor 使用）。"""
        self._store[channel_id] = new_history

    async def remove_last(self, channel_id: str) -> None:
        """移除指定频道的最后一条消息（LLM 调用失败时回滚用）。"""
        history = self._store.get(channel_id, [])
        if history:
            history.pop()
        try:
            await self._db.execute(
                """DELETE FROM conversations WHERE id = (
                    SELECT id FROM conversations WHERE chat_id = ? ORDER BY id DESC LIMIT 1
                )""",
                (channel_id,),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"移除最后一条消息失败: {e}")

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

    async def append_to_session(
        self, chat_id: str, session_id: str, role: str, content: str, *, channel: str = "telegram"
    ) -> None:
        """追加消息到指定 session（先写缓存，再持久化）。"""
        if session_id not in self._session_store:
            self._session_store[session_id] = []
        self._session_store[session_id].append({"role": role, "content": content})

        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                "INSERT INTO conversations (chat_id, role, content, timestamp, channel, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, role, content, timestamp, channel, session_id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"Session 消息写入数据库失败: {e}")

    async def get_session_messages(self, session_id: str) -> list[dict]:
        """获取指定 session 的对话历史（从缓存读取，不存在时从 DB 加载）。"""
        if session_id not in self._session_store:
            await self._load_session_history(session_id)
        return self._session_store.get(session_id, [])

    async def _load_session_history(self, session_id: str) -> None:
        """从 DB 加载指定 session 的消息到内存缓存。"""
        max_messages = MAX_HISTORY_TURNS * 2
        try:
            async with self._db.execute(
                """SELECT role, content FROM (
                    SELECT id, role, content FROM conversations
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC""",
                (session_id, max_messages),
            ) as cursor:
                messages = [
                    {"role": row[0], "content": row[1]}
                    async for row in cursor
                ]
            if messages:
                self._session_store[session_id] = messages
        except Exception as e:
            logger.error(f"从 DB 加载 session {session_id} 历史失败: {e}")

    async def load_session_from_snapshot(self, session_id: str, messages: list[dict]) -> None:
        """从快照恢复的消息加载到内存缓存（用于 condensed session 复活）。"""
        self._session_store[session_id] = list(messages)

    async def clear_session_cache(self, session_id: str) -> None:
        """清除指定 session 的内存缓存（session 被压缩归档或删除时调用）。"""
        self._session_store.pop(session_id, None)

    def replace_session_history(self, session_id: str, new_history: list[dict]) -> None:
        """替换指定 session 的内存缓存（不修改数据库，供 Compactor 使用）。"""
        self._session_store[session_id] = new_history

    async def remove_last_session(self, session_id: str) -> None:
        """移除指定 session 的最后一条消息（LLM 调用失败时回滚用）。"""
        history = self._session_store.get(session_id, [])
        if history:
            history.pop()
        try:
            await self._db.execute(
                """DELETE FROM conversations WHERE id = (
                    SELECT id FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT 1
                )""",
                (session_id,),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"移除 session {session_id} 最后一条消息失败: {e}")

    async def clear_chat_all(self, channel_id: str) -> None:
        """清除指定频道的全部记忆（短期 + 长期）。"""
        self._store.pop(channel_id, None)
        tables = (
            "conversations",
            "user_facts",
            "discoveries",
            "interest_topics",
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

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("数据库连接已关闭")

    # ===== user_facts 相关方法（为 Task 3 预留）=====

    async def get_user_facts(self, chat_id: str) -> list[dict]:
        """获取指定用户的所有画像信息。"""
        try:
            async with self._db.execute(
                "SELECT fact_key, fact_value, updated_at FROM user_facts "
                "WHERE chat_id = ? ORDER BY updated_at DESC",
                (chat_id,),
            ) as cursor:
                return [
                    {"fact_key": row[0], "fact_value": row[1], "updated_at": row[2]}
                    async for row in cursor
                ]
        except Exception as e:
            logger.error(f"读取用户画像失败: {e}")
            return []

    async def set_user_fact(self, chat_id: str, fact_key: str, fact_value: str) -> None:
        """写入或更新一条用户画像信息。"""
        try:
            updated_at = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO user_facts (chat_id, fact_key, fact_value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, fact_key) DO UPDATE SET
                       fact_value = excluded.fact_value,
                       updated_at = excluded.updated_at""",
                (chat_id, fact_key, fact_value, updated_at),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"写入用户画像失败: {e}")

    async def delete_user_fact(self, chat_id: str, fact_key: str) -> bool:
        """删除指定用户画像条目。"""
        try:
            cursor = await self._db.execute(
                "DELETE FROM user_facts WHERE chat_id = ? AND fact_key = ?",
                (chat_id, fact_key),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除用户画像失败: {e}")
            return False

    async def get_all_chat_ids(self) -> list[str]:
        """返回所有有过对话记录的 chat_id 列表。"""
        try:
            async with self._db.execute(
                "SELECT DISTINCT chat_id FROM conversations"
            ) as cursor:
                return [row[0] async for row in cursor]
        except Exception as e:
            logger.error(f"获取 chat_id 列表失败: {e}")
            return []

    async def get_last_interaction(self, chat_id: str) -> datetime | None:
        """返回指定 chat_id 最后一条消息的时间戳，无记录时返回 None。"""
        try:
            async with self._db.execute(
                "SELECT timestamp FROM conversations WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return datetime.fromisoformat(row[0])
        except Exception as e:
            logger.error(f"获取最后交互时间失败: {e}")
            return None

    async def add_discovery(
        self,
        chat_id: str,
        source: str,
        title: str,
        summary: str,
        url: str | None,
    ) -> None:
        """写入一条新发现。"""
        try:
            discovered_at = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO discoveries (chat_id, source, title, summary, url, discovered_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chat_id, source, title, summary, url, discovered_at),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"写入 discovery 失败: {e}")

    async def get_unshared_discoveries(self, chat_id: str, limit: int = 5) -> list[dict]:
        """获取未分享的发现，按发现时间升序（最早的优先分享）。"""
        try:
            async with self._db.execute(
                """SELECT id, source, title, summary, url, discovered_at, shared_at
                   FROM discoveries
                   WHERE chat_id = ? AND shared_at IS NULL
                   ORDER BY discovered_at ASC
                   LIMIT ?""",
                (chat_id, limit),
            ) as cursor:
                return [
                    {
                        "id": row[0], "source": row[1], "title": row[2],
                        "summary": row[3], "url": row[4],
                        "discovered_at": row[5], "shared_at": row[6],
                    }
                    async for row in cursor
                ]
        except Exception as e:
            logger.error(f"获取未分享 discovery 失败: {e}")
            return []

    async def mark_discovery_shared(self, discovery_id: int) -> None:
        """将指定 discovery 标记为已分享。"""
        try:
            shared_at = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                "UPDATE discoveries SET shared_at = ? WHERE id = ?",
                (shared_at, discovery_id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"标记 discovery 已分享失败: {e}")

    async def bump_interest(self, chat_id: str, topic: str, increment: float = 1.0) -> None:
        """增加话题权重（UPSERT：首次插入，已有则累加）。"""
        try:
            last_seen = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO interest_topics (chat_id, topic, weight, last_seen)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, topic) DO UPDATE SET
                       weight = interest_topics.weight + excluded.weight,
                       last_seen = excluded.last_seen""",
                (chat_id, topic, increment, last_seen),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"增加兴趣话题失败: {e}")

    async def get_top_interests(self, chat_id: str, limit: int = 10) -> list[dict]:
        """按权重降序返回兴趣话题。"""
        try:
            async with self._db.execute(
                """SELECT topic, weight, last_seen
                   FROM interest_topics
                   WHERE chat_id = ?
                   ORDER BY weight DESC, last_seen DESC
                   LIMIT ?""",
                (chat_id, limit),
            ) as cursor:
                return [
                    {"topic": row[0], "weight": row[1], "last_seen": row[2]}
                    async for row in cursor
                ]
        except Exception as e:
            logger.error(f"读取兴趣话题失败: {e}")
            return []

    async def get_conversations_for_date(self, chat_id: str, date_str: str) -> list[dict]:
        """获取指定日期（YYYY-MM-DD）的所有对话记录（从 DB 查询）。"""
        try:
            async with self._db.execute(
                "SELECT role, content, timestamp FROM conversations "
                "WHERE chat_id = ? AND timestamp LIKE ? ORDER BY id ASC",
                (chat_id, f"{date_str}%"),
            ) as cursor:
                return [
                    {"role": row[0], "content": row[1], "timestamp": row[2]}
                    async for row in cursor
                ]
        except Exception as e:
            logger.error(f"获取 {date_str} 对话记录失败: {e}")
            return []

    async def decay_interests(self, chat_id: str, factor: float = 0.95) -> None:
        """对所有话题权重乘以 factor（衰减），保持兴趣的时效性。"""
        try:
            await self._db.execute(
                "UPDATE interest_topics SET weight = weight * ? WHERE chat_id = ?",
                (factor, chat_id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"衰减兴趣话题失败: {e}")

    async def add_todo(self, chat_id: str, content: str, due_date: str | None = None) -> int:
        """新增一条待办，返回数据库 ID。"""
        try:
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = await self._db.execute(
                """INSERT INTO todos (chat_id, content, due_date, created_at)
                   VALUES (?, ?, ?, ?)""",
                (chat_id, content, due_date, created_at),
            )
            await self._db.commit()
            return int(cursor.lastrowid or 0)
        except Exception as e:
            logger.error(f"新增待办失败: {e}")
            return 0

    async def list_todos(self, chat_id: str) -> list[dict]:
        """列出指定用户的待办，未完成优先，再按截止日和创建时间排序。"""
        try:
            async with self._db.execute(
                """SELECT id, content, due_date, done, created_at
                   FROM todos
                   WHERE chat_id = ?
                   ORDER BY
                       done ASC,
                       CASE WHEN due_date IS NULL THEN 1 ELSE 0 END ASC,
                       due_date ASC,
                       created_at ASC""",
                (chat_id,),
            ) as cursor:
                return [
                    {
                        "id": row[0],
                        "content": row[1],
                        "due_date": row[2],
                        "done": bool(row[3]),
                        "created_at": row[4],
                    }
                    async for row in cursor
                ]
        except Exception as e:
            logger.error(f"列出待办失败: {e}")
            return []

    async def mark_todo_done(self, chat_id: str, todo_id: int) -> bool:
        """将指定待办标记为完成。"""
        try:
            cursor = await self._db.execute(
                "UPDATE todos SET done = 1 WHERE chat_id = ? AND id = ?",
                (chat_id, todo_id),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"标记待办完成失败: {e}")
            return False

    async def delete_todo(self, chat_id: str, todo_id: int) -> bool:
        """删除指定待办。"""
        try:
            cursor = await self._db.execute(
                "DELETE FROM todos WHERE chat_id = ? AND id = ?",
                (chat_id, todo_id),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除待办失败: {e}")
            return False

    # ===== reminders 相关方法 =====

    async def add_reminder(
        self,
        chat_id: str,
        content: str,
        recurrence_type: str,
        next_trigger_at: datetime | str,
        weekday: int | None = None,
        time_of_day: str | None = None,
        interval_minutes: int | None = None,
    ) -> int:
        """新增提醒，返回数据库 ID。"""
        try:
            normalized_content = str(content).strip()
            if not normalized_content:
                return 0

            recurrence = str(recurrence_type).strip().lower()
            if recurrence not in _VALID_RECURRENCE_TYPES:
                return 0

            next_dt = self._ensure_utc_datetime(next_trigger_at)
            now = datetime.now(timezone.utc)
            if recurrence == "once":
                if next_dt <= now:
                    return 0
                normalized_weekday = None
                normalized_time = None
                normalized_interval = None
            elif recurrence == "interval":
                normalized_weekday = None
                normalized_time = None
                normalized_interval = int(interval_minutes) if interval_minutes else None
                if not normalized_interval or normalized_interval <= 0:
                    return 0
                if next_dt <= now:
                    next_dt = now + timedelta(minutes=normalized_interval)
            else:
                normalized_weekday = self._normalize_weekday(weekday)
                normalized_time = self._normalize_time_of_day(time_of_day) or next_dt.strftime("%H:%M")
                normalized_interval = None
                if recurrence == "weekly" and normalized_weekday is None:
                    return 0
                if next_dt <= now:
                    next_dt = self._advance_to_future(next_dt, recurrence, now)

            created_at = now.isoformat()
            cursor = await self._db.execute(
                """INSERT INTO reminders (
                       chat_id, content, recurrence_type, next_trigger_at,
                       weekday, time_of_day, interval_minutes, active, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    chat_id,
                    normalized_content,
                    recurrence,
                    next_dt.isoformat(),
                    normalized_weekday,
                    normalized_time,
                    normalized_interval,
                    created_at,
                ),
            )
            await self._db.commit()
            return int(cursor.lastrowid or 0)
        except Exception as e:
            logger.error(f"新增提醒失败: {e}")
            return 0

    async def list_reminders(self, chat_id: str, include_inactive: bool = False) -> list[dict]:
        """列出指定用户的提醒。"""
        try:
            if include_inactive:
                query = (
                    "SELECT id, chat_id, content, recurrence_type, next_trigger_at, "
                    "weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes "
                    "FROM reminders WHERE chat_id = ? "
                    "ORDER BY active DESC, next_trigger_at ASC, id ASC"
                )
                params = (chat_id,)
            else:
                query = (
                    "SELECT id, chat_id, content, recurrence_type, next_trigger_at, "
                    "weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes "
                    "FROM reminders WHERE chat_id = ? AND active = 1 "
                    "ORDER BY next_trigger_at ASC, id ASC"
                )
                params = (chat_id,)

            async with self._db.execute(query, params) as cursor:
                rows = [row async for row in cursor]
            return [self._row_to_reminder(row) for row in rows]
        except Exception as e:
            logger.error(f"列出提醒失败: {e}")
            return []

    async def cancel_reminder(self, chat_id: str, reminder_id: int) -> bool:
        """取消提醒。"""
        try:
            cancelled_at = datetime.now(timezone.utc).isoformat()
            cursor = await self._db.execute(
                """UPDATE reminders
                   SET active = 0, cancelled_at = ?
                   WHERE chat_id = ? AND id = ? AND active = 1""",
                (cancelled_at, chat_id, reminder_id),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"取消提醒失败: {e}")
            return False

    async def get_due_reminders(
        self,
        chat_id: str,
        now: datetime,
        grace_seconds: int,
        limit: int = 20,
    ) -> list[dict]:
        """获取当前到期提醒（仅包含容错窗口内的任务）。"""
        if limit <= 0:
            return []

        try:
            now_utc = self._ensure_utc_datetime(now)
            grace = max(int(grace_seconds), 0)
            oldest_allowed = now_utc - timedelta(seconds=grace)
            scan_limit = max(limit * 4, limit)

            async with self._db.execute(
                """SELECT id, chat_id, content, recurrence_type, next_trigger_at,
                          weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes
                   FROM reminders
                   WHERE chat_id = ? AND active = 1 AND next_trigger_at <= ?
                   ORDER BY next_trigger_at ASC, id ASC
                   LIMIT ?""",
                (chat_id, now_utc.isoformat(), scan_limit),
            ) as cursor:
                rows = [row async for row in cursor]

            due: list[dict] = []
            for row in rows:
                reminder = self._row_to_reminder(row)
                next_dt = self._ensure_utc_datetime(reminder["next_trigger_at"])
                if next_dt < oldest_allowed:
                    await self._drop_or_roll_forward_stale(reminder, now_utc)
                    continue
                due.append(reminder)
                if len(due) >= limit:
                    break
            return due
        except Exception as e:
            logger.error(f"获取到期提醒失败: {e}")
            return []

    async def complete_or_reschedule_reminder(self, reminder_id: int, now: datetime) -> bool:
        """标记提醒完成或重算下一次触发时间。"""
        try:
            now_utc = self._ensure_utc_datetime(now)
            async with self._db.execute(
                """SELECT id, chat_id, content, recurrence_type, next_trigger_at,
                          weekday, time_of_day, active, created_at, last_triggered_at, cancelled_at, interval_minutes
                   FROM reminders
                   WHERE id = ? AND active = 1""",
                (reminder_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                return False

            reminder = self._row_to_reminder(row)
            recurrence = reminder["recurrence_type"]
            if recurrence == "once":
                cursor = await self._db.execute(
                    """UPDATE reminders
                       SET active = 0, last_triggered_at = ?
                       WHERE id = ? AND active = 1""",
                    (now_utc.isoformat(), reminder_id),
                )
                await self._db.commit()
                return cursor.rowcount > 0

            current_next = self._ensure_utc_datetime(reminder["next_trigger_at"])
            next_trigger = self._advance_to_future(
                current_next, recurrence, now_utc,
                interval_minutes=reminder.get("interval_minutes"),
            )
            cursor = await self._db.execute(
                """UPDATE reminders
                   SET last_triggered_at = ?, next_trigger_at = ?
                   WHERE id = ? AND active = 1""",
                (now_utc.isoformat(), next_trigger.isoformat(), reminder_id),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"完成/重排提醒失败: {e}")
            return False

    async def _drop_or_roll_forward_stale(self, reminder: dict, now_utc: datetime) -> None:
        recurrence = str(reminder.get("recurrence_type", "once"))
        reminder_id = int(reminder["id"])
        if recurrence == "once":
            await self._db.execute(
                """UPDATE reminders
                   SET active = 0, cancelled_at = ?
                   WHERE id = ? AND active = 1""",
                (now_utc.isoformat(), reminder_id),
            )
            await self._db.commit()
            return

        current_next = self._ensure_utc_datetime(reminder["next_trigger_at"])
        next_trigger = self._advance_to_future(
            current_next, recurrence, now_utc,
            interval_minutes=reminder.get("interval_minutes"),
        )
        await self._db.execute(
            "UPDATE reminders SET next_trigger_at = ? WHERE id = ? AND active = 1",
            (next_trigger.isoformat(), reminder_id),
        )
        await self._db.commit()

    def _advance_to_future(
        self,
        start: datetime,
        recurrence_type: str,
        now_utc: datetime,
        interval_minutes: int | None = None,
    ) -> datetime:
        recurrence = str(recurrence_type).lower()
        if recurrence == "daily":
            step = timedelta(days=1)
        elif recurrence == "weekly":
            step = timedelta(days=7)
        elif recurrence == "interval" and interval_minutes:
            step = timedelta(minutes=interval_minutes)
        else:
            return start

        next_dt = start
        for _ in range(0, 4096):
            if next_dt > now_utc:
                return next_dt
            next_dt = next_dt + step
        return next_dt

    def _ensure_utc_datetime(self, value: datetime | str) -> datetime:
        if isinstance(value, str):
            text = value.strip()
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = value

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _normalize_weekday(self, raw_value) -> int | None:
        if raw_value in (None, "", "null"):
            return None
        try:
            weekday = int(raw_value)
        except (TypeError, ValueError):
            return None
        return weekday if 0 <= weekday <= 6 else None

    def _normalize_time_of_day(self, raw_value) -> str | None:
        if raw_value in (None, "", "null"):
            return None
        time_text = str(raw_value).strip()
        if not _TIME_OF_DAY_PATTERN.match(time_text):
            return None
        try:
            datetime.strptime(time_text, "%H:%M")
        except ValueError:
            return None
        return time_text

    def _row_to_reminder(self, row) -> dict:
        result = {
            "id": row[0],
            "chat_id": row[1],
            "content": row[2],
            "recurrence_type": row[3],
            "next_trigger_at": row[4],
            "weekday": row[5],
            "time_of_day": row[6],
            "active": bool(row[7]),
            "created_at": row[8],
            "last_triggered_at": row[9],
            "cancelled_at": row[10],
        }
        if len(row) > 11:
            result["interval_minutes"] = row[11]
        return result
