"""待办事项管理。"""

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("lapwing.memory.todos")


class TodoRepository:
    """管理待办事项的数据访问。"""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

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
