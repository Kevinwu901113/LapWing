"""发现和兴趣追踪。"""

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("lapwing.memory.discoveries")


class DiscoveryRepository:
    """管理发现条目和兴趣话题的数据访问。"""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

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
        """获取指定日期的对话记录。"""
        try:
            async with self._db.execute(
                "SELECT role, content, timestamp FROM conversations "
                "WHERE chat_id = ? AND date(timestamp) = ? ORDER BY id ASC",
                (chat_id, date_str),
            ) as cursor:
                return [
                    {"role": row[0], "content": row[1], "timestamp": row[2]}
                    async for row in cursor
                ]
        except Exception as e:
            logger.error(f"按日期获取对话失败: {e}")
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
