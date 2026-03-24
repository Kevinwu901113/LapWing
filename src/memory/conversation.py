"""对话记忆管理（SQLite 持久化 + 内存缓存）。"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from config.settings import MAX_HISTORY_TURNS

logger = logging.getLogger("lapwing.memory")


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

    async def append(self, channel_id: str, role: str, content: str) -> None:
        """追加一条消息到对话历史（先写缓存，再持久化）。"""
        if channel_id not in self._store:
            self._store[channel_id] = []
        self._store[channel_id].append({"role": role, "content": content})

        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                "INSERT INTO conversations (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (channel_id, role, content, timestamp),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"对话消息写入数据库失败: {e}")

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
