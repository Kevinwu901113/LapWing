"""AmbientKnowledgeStore——带 TTL 的环境知识缓存，SQLite 后端。

存放 Lapwing "当前知道的事"：天气、赛事、新闻等。
不是长期记忆，是工作记忆/短期缓存。所有数据都有 TTL，过期自动失效。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from src.ambient.models import AmbientEntry

logger = logging.getLogger("lapwing.ambient.ambient_knowledge")

_MAX_ENTRIES = 50

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS ambient_entries (
    key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    topic TEXT NOT NULL,
    data TEXT NOT NULL,
    summary TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    used INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ambient_category ON ambient_entries(category);
CREATE INDEX IF NOT EXISTS idx_ambient_expires ON ambient_entries(expires_at);
"""


class AmbientKnowledgeStore:
    """Lapwing 的环境知识缓存。"""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def put(self, key: str, entry: AmbientEntry) -> None:
        """写入或更新一条环境知识。超过容量上限时按 LRU 驱逐。"""
        assert self._db is not None
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO ambient_entries
               (key, category, topic, data, summary, fetched_at,
                expires_at, source, confidence, used, last_accessed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
               ON CONFLICT(key) DO UPDATE SET
                 category=excluded.category, topic=excluded.topic,
                 data=excluded.data, summary=excluded.summary,
                 fetched_at=excluded.fetched_at, expires_at=excluded.expires_at,
                 source=excluded.source, confidence=excluded.confidence,
                 used=0, last_accessed_at=excluded.last_accessed_at""",
            (key, entry.category, entry.topic, entry.data, entry.summary,
             entry.fetched_at, entry.expires_at, entry.source, entry.confidence,
             now_iso),
        )
        await self._db.commit()
        await self._enforce_capacity()

    async def get(self, key: str) -> AmbientEntry | None:
        """精确查找，过期视为不存在。"""
        assert self._db is not None
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """SELECT key, category, topic, data, summary, fetched_at,
                      expires_at, source, confidence
               FROM ambient_entries
               WHERE key = ? AND expires_at > ?""",
            (key, now_iso),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        # 标记已访问
        await self._db.execute(
            "UPDATE ambient_entries SET used=1, last_accessed_at=? WHERE key=?",
            (now_iso, key),
        )
        await self._db.commit()
        return self._row_to_entry(row)

    async def get_if_fresh(self, key: str) -> AmbientEntry | None:
        """只返回未过期的条目。"""
        return await self.get(key)

    async def get_by_category(self, category: str) -> list[AmbientEntry]:
        """按分类查找所有未过期条目。"""
        assert self._db is not None
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """SELECT key, category, topic, data, summary, fetched_at,
                      expires_at, source, confidence
               FROM ambient_entries
               WHERE category = ? AND expires_at > ?
               ORDER BY fetched_at DESC""",
            (category, now_iso),
        )
        rows = await cursor.fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def get_all_fresh(self) -> tuple[AmbientEntry, ...]:
        """返回所有未过期条目（用于 system prompt 注入）。"""
        assert self._db is not None
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """SELECT key, category, topic, data, summary, fetched_at,
                      expires_at, source, confidence
               FROM ambient_entries
               WHERE expires_at > ?
               ORDER BY category, fetched_at DESC""",
            (now_iso,),
        )
        rows = await cursor.fetchall()
        return tuple(self._row_to_entry(r) for r in rows)

    async def delete(self, key: str) -> None:
        """删除指定条目。"""
        assert self._db is not None
        await self._db.execute("DELETE FROM ambient_entries WHERE key = ?", (key,))
        await self._db.commit()

    async def cleanup_expired(self) -> int:
        """清理过期条目，返回清理数量。"""
        assert self._db is not None
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM ambient_entries WHERE expires_at <= ?",
            (now_iso,),
        )
        await self._db.commit()
        return cursor.rowcount

    async def stats(self) -> dict:
        """统计信息。"""
        assert self._db is not None
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute("SELECT COUNT(*) FROM ambient_entries")
        total = (await cursor.fetchone())[0]
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM ambient_entries WHERE expires_at > ?",
            (now_iso,),
        )
        fresh = (await cursor.fetchone())[0]
        return {"total": total, "fresh": fresh}

    async def _enforce_capacity(self) -> None:
        """超过容量上限时按 LRU（last_accessed_at）驱逐最久未使用的条目。"""
        assert self._db is not None
        cursor = await self._db.execute("SELECT COUNT(*) FROM ambient_entries")
        count = (await cursor.fetchone())[0]
        if count <= _MAX_ENTRIES:
            return
        excess = count - _MAX_ENTRIES
        await self._db.execute(
            """DELETE FROM ambient_entries WHERE key IN (
                 SELECT key FROM ambient_entries
                 ORDER BY last_accessed_at ASC NULLS FIRST
                 LIMIT ?
               )""",
            (excess,),
        )
        await self._db.commit()

    @staticmethod
    def _row_to_entry(row: tuple) -> AmbientEntry:
        return AmbientEntry(
            key=row[0],
            category=row[1],
            topic=row[2],
            data=row[3],
            summary=row[4],
            fetched_at=row[5],
            expires_at=row[6],
            source=row[7],
            confidence=row[8],
        )
