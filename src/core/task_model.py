"""TaskModel — 任务持久化模型。Phase 1 新基础设施。"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger("lapwing.core.task_model")


@dataclass
class TaskBudget:
    max_tokens: int = 50000
    max_tool_calls: int = 30
    max_time_seconds: int = 300


@dataclass
class Task:
    task_id: str
    parent_task_id: str | None
    source: str  # "kevin_qq" / "kevin_desktop" / "heartbeat" / "reminder"
    status: str  # "queued" / "running" / "blocked" / "done" / "failed" / "cancelled"
    initiator: str  # "lapwing"
    assigned_to: str | None
    request: str
    context: str
    result: str | None = None
    artifacts: list[str] = field(default_factory=list)
    budget: TaskBudget = field(default_factory=TaskBudget)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TaskStore:
    """任务持久化。SQLite。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._init_table()

    async def _init_table(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                parent_task_id TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                initiator TEXT NOT NULL,
                assigned_to TEXT,
                request TEXT NOT NULL,
                context TEXT,
                result TEXT,
                artifacts TEXT,
                budget TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
        """)
        await self._db.commit()

    async def create(self, task: Task) -> str:
        if self._db is None:
            raise RuntimeError("TaskStore not initialized")
        await self._db.execute(
            """INSERT INTO tasks
               (task_id, parent_task_id, source, status, initiator, assigned_to,
                request, context, result, artifacts, budget, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id,
                task.parent_task_id,
                task.source,
                task.status,
                task.initiator,
                task.assigned_to,
                task.request,
                task.context,
                task.result,
                json.dumps(task.artifacts, ensure_ascii=False),
                json.dumps(asdict(task.budget), ensure_ascii=False),
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
            ),
        )
        await self._db.commit()
        return task.task_id

    async def update_status(
        self, task_id: str, status: str, result: str | None = None
    ) -> None:
        if self._db is None:
            raise RuntimeError("TaskStore not initialized")
        now = datetime.now(timezone.utc).isoformat()
        if result is not None:
            await self._db.execute(
                "UPDATE tasks SET status = ?, result = ?, updated_at = ? WHERE task_id = ?",
                (status, result, now, task_id),
            )
        else:
            await self._db.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, now, task_id),
            )
        await self._db.commit()

    async def get(self, task_id: str) -> Task | None:
        if self._db is None:
            return None
        async with self._db.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    async def list_active(self) -> list[Task]:
        if self._db is None:
            return []
        async with self._db.execute(
            "SELECT * FROM tasks WHERE status IN ('queued', 'running', 'blocked') ORDER BY created_at"
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def list_by_status(self, status: str) -> list[Task]:
        if self._db is None:
            return []
        async with self._db.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at", (status,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @staticmethod
    def _row_to_task(row) -> Task:
        artifacts = json.loads(row[9]) if row[9] else []
        budget_dict = json.loads(row[10]) if row[10] else {}
        budget = TaskBudget(**budget_dict) if budget_dict else TaskBudget()
        return Task(
            task_id=row[0],
            parent_task_id=row[1],
            source=row[2],
            status=row[3],
            initiator=row[4],
            assigned_to=row[5],
            request=row[6],
            context=row[7] or "",
            result=row[8],
            artifacts=artifacts,
            budget=budget,
            created_at=datetime.fromisoformat(row[11]),
            updated_at=datetime.fromisoformat(row[12]),
        )

    @staticmethod
    def new_task_id() -> str:
        return uuid.uuid4().hex[:12]
