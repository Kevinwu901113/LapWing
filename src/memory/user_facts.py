"""用户画像存取。"""

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("lapwing.memory.user_facts")


def filter_visible_facts(facts: list[dict]) -> list[dict]:
    """过滤掉内部摘要类画像，只保留用户可见的条目。"""
    return [
        fact for fact in facts
        if not str(fact.get("fact_key", "")).startswith("memory_summary_")
    ]


class UserFactsRepository:
    """管理用户画像（facts）的数据访问。"""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def get_user_facts(self, chat_id: str) -> list[dict]:
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
        try:
            async with self._db.execute(
                "SELECT DISTINCT chat_id FROM conversations"
            ) as cursor:
                return [row[0] async for row in cursor]
        except Exception as e:
            logger.error(f"获取 chat_id 列表失败: {e}")
            return []

    async def get_last_interaction(self, chat_id: str) -> datetime | None:
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
