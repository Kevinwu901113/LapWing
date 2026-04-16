"""EventLogger v2 — Append-only 事件日志。SQLite 存储。

Phase 1 新基础设施。替换旧 src/logging/event_logger.py（旧版暂保留，后续 Phase 迁移）。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger("lapwing.core.event_logger_v2")


@dataclass
class Event:
    event_id: str
    timestamp: datetime
    event_type: str
    actor: str
    task_id: str | None
    source: str
    trust_level: str
    correlation_id: str
    payload: dict = field(default_factory=dict)


class EventLogger:
    """Append-only event log。SQLite 存储。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """初始化数据库连接和表结构。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._init_table()

    async def _init_table(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                task_id TEXT,
                source TEXT,
                trust_level TEXT,
                correlation_id TEXT,
                payload TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
            CREATE INDEX IF NOT EXISTS idx_events_correlation_id ON events(correlation_id);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
        """)
        await self._db.commit()

    async def log(self, event: Event) -> None:
        """写入事件。"""
        if self._db is None:
            logger.warning("EventLogger 未初始化，跳过事件写入")
            return
        await self._db.execute(
            """INSERT INTO events
               (event_id, timestamp, event_type, actor, task_id, source, trust_level, correlation_id, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.timestamp.isoformat(),
                event.event_type,
                event.actor,
                event.task_id,
                event.source,
                event.trust_level,
                event.correlation_id,
                json.dumps(event.payload, ensure_ascii=False),
            ),
        )
        await self._db.commit()

    async def query(
        self,
        event_type: str | None = None,
        task_id: str | None = None,
        after: datetime | None = None,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """查询事件。after_event_id 用于 SSE 断线重连。"""
        if self._db is None:
            return []

        conditions: list[str] = []
        params: list = []

        # after_event_id：先查出该事件的 timestamp，再查之后的事件
        if after_event_id is not None:
            async with self._db.execute(
                "SELECT timestamp FROM events WHERE event_id = ?", (after_event_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                conditions.append("timestamp > ?")
                params.append(row[0])
                # 排除该事件本身（同一 timestamp 可能有多个事件）
                conditions.append("event_id != ?")
                params.append(after_event_id)

        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if task_id is not None:
            conditions.append("task_id = ?")
            params.append(task_id)
        if after is not None:
            conditions.append("timestamp > ?")
            params.append(after.isoformat())

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM events WHERE {where} ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

        return [
            Event(
                event_id=row[0],
                timestamp=datetime.fromisoformat(row[1]),
                event_type=row[2],
                actor=row[3],
                task_id=row[4],
                source=row[5],
                trust_level=row[6],
                correlation_id=row[7],
                payload=json.loads(row[8]) if row[8] else {},
            )
            for row in rows
        ]

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @staticmethod
    def make_event(
        event_type: str,
        *,
        actor: str = "system",
        task_id: str | None = None,
        source: str = "",
        trust_level: str = "",
        correlation_id: str | None = None,
        payload: dict | None = None,
    ) -> Event:
        """创建一个新事件。"""
        event_id = uuid.uuid4().hex[:16]
        return Event(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            actor=actor,
            task_id=task_id,
            source=source,
            trust_level=trust_level,
            correlation_id=correlation_id or event_id,
            payload=payload or {},
        )
