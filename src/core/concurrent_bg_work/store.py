from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite

from src.core.concurrent_bg_work.types import (
    AgentEvent,
    AgentEventType,
    AgentNeedsInputPayload,
    AgentRuntimeCheckpoint,
    AgentTaskRecord,
    AgentTaskSnapshot,
    InterruptedTaskInfo,
    RecoveryNotice,
    SalienceLevel,
    TaskStatus,
)

logger = logging.getLogger("lapwing.core.concurrent_bg_work.store")

_ACTIVE_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.RUNNING.value,
    TaskStatus.RESUMING.value,
}
_RECOVERABLE_STATUSES = _ACTIVE_STATUSES | {
    TaskStatus.WAITING_RESOURCE.value,
    TaskStatus.WAITING_INPUT.value,
}
_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


class AgentTaskStoreError(RuntimeError):
    pass


class DuplicateTaskError(AgentTaskStoreError):
    pass


WriteOp = Callable[[aiosqlite.Connection], Awaitable[Any]]


class AgentTaskStoreWriter:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._queue: asyncio.Queue[tuple[WriteOp | None, asyncio.Future | None]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="agent-task-store-writer")
        await self._ready.wait()

    async def close(self) -> None:
        if self._task is None:
            return
        await self._queue.put((None, None))
        await self._task
        self._task = None

    async def submit(self, op: WriteOp) -> Any:
        await self.start()
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((op, fut))
        return await fut

    async def _run(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            await _configure(conn)
            await _apply_migration(conn)
            self._ready.set()
            while True:
                op, fut = await self._queue.get()
                if op is None:
                    break
                try:
                    await conn.execute("BEGIN")
                    result = await op(conn)
                    await conn.commit()
                    if fut is not None and not fut.done():
                        fut.set_result(result)
                except Exception as exc:
                    await conn.rollback()
                    if fut is not None and not fut.done():
                        fut.set_exception(exc)


class AgentTaskStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._writer = AgentTaskStoreWriter(self.db_path)
        self.pending_recovery_notice: RecoveryNotice | None = None

    async def init(self) -> None:
        await self._writer.start()

    async def close(self) -> None:
        await self._writer.close()

    async def create_task(self, record: AgentTaskRecord) -> AgentTaskRecord:
        async def op(conn: aiosqlite.Connection):
            try:
                await conn.execute(
                    """
                    INSERT INTO agent_tasks (
                        task_id, chat_id, owner_user_id, parent_event_id,
                        parent_turn_id, parent_task_id, root_task_id,
                        spawned_by, replaces_task_id, spec_id, spec_version,
                        instance_id, objective, user_visible_summary,
                        semantic_tags, expected_output, status, status_reason,
                        created_at, started_at, completed_at, last_event_at,
                        workspace_path, result_summary, error_summary,
                        artifact_refs, last_progress_summary, checkpoint_id,
                        checkpoint_question, cancellation_requested,
                        cancellation_reason, notify_policy, salience, priority,
                        idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _record_values(record),
                )
            except aiosqlite.IntegrityError as exc:
                raise DuplicateTaskError(record.idempotency_key) from exc
            return record

        return await self._writer.submit(op)

    async def read(self, task_id: str) -> AgentTaskRecord | None:
        async with self._reader() as conn:
            async with conn.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_record(row) if row is not None else None

    async def read_by_idempotency_key(self, key: str) -> AgentTaskRecord | None:
        async with self._reader() as conn:
            async with conn.execute(
                "SELECT * FROM agent_tasks WHERE idempotency_key = ?", (key,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_record(row) if row is not None else None

    async def append_event(self, event: AgentEvent) -> None:
        async def op(conn: aiosqlite.Connection):
            await conn.execute(
                """
                INSERT INTO agent_events (
                    event_id, task_id, chat_id, type, occurred_at,
                    summary_for_lapwing, summary_for_owner, raw_payload_ref,
                    salience, payload_json, sequence_in_task
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.chat_id,
                    event.type.value,
                    event.occurred_at.isoformat(),
                    event.summary_for_lapwing,
                    event.summary_for_owner,
                    event.raw_payload_ref,
                    event.salience.value if event.salience else None,
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                    event.sequence_in_task,
                ),
            )
            updates: dict[str, Any] = {
                "last_event_at": event.occurred_at.isoformat(),
            }
            if event.type == AgentEventType.AGENT_PROGRESS_SUMMARY:
                updates["last_progress_summary"] = event.summary_for_lapwing
            elif event.type == AgentEventType.AGENT_COMPLETED:
                updates["status"] = TaskStatus.COMPLETED.value
                updates["completed_at"] = event.occurred_at.isoformat()
                updates["result_summary"] = event.summary_for_lapwing
            elif event.type == AgentEventType.AGENT_FAILED:
                updates["status"] = TaskStatus.FAILED.value
                updates["completed_at"] = event.occurred_at.isoformat()
                updates["error_summary"] = event.summary_for_lapwing
            elif event.type == AgentEventType.AGENT_CANCELLED:
                updates["status"] = TaskStatus.CANCELLED.value
                updates["completed_at"] = event.occurred_at.isoformat()
            elif event.type == AgentEventType.AGENT_NEEDS_INPUT:
                updates["status"] = TaskStatus.WAITING_INPUT.value
                question = event.payload.get("question_for_lapwing")
                if question:
                    updates["checkpoint_question"] = str(question)
            await _update_task_fields(conn, event.task_id, updates)

        await self._writer.submit(op)

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        status_reason: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        result_summary: str | None = None,
        error_summary: str | None = None,
        checkpoint_id: str | None = None,
        checkpoint_question: str | None = None,
        cancellation_requested: bool | None = None,
        cancellation_reason: str | None = None,
    ) -> None:
        async def op(conn: aiosqlite.Connection):
            updates: dict[str, Any] = {"status": status.value}
            optional = {
                "status_reason": status_reason,
                "started_at": started_at.isoformat() if started_at else None,
                "completed_at": completed_at.isoformat() if completed_at else None,
                "result_summary": result_summary,
                "error_summary": error_summary,
                "checkpoint_id": checkpoint_id,
                "checkpoint_question": checkpoint_question,
                "cancellation_reason": cancellation_reason,
            }
            for key, value in optional.items():
                if value is not None:
                    updates[key] = value
            if cancellation_requested is not None:
                updates["cancellation_requested"] = 1 if cancellation_requested else 0
            await _update_task_fields(conn, task_id, updates)

        await self._writer.submit(op)

    async def save_checkpoint(self, checkpoint: AgentRuntimeCheckpoint) -> None:
        async def op(conn: aiosqlite.Connection):
            await conn.execute(
                """
                INSERT OR REPLACE INTO agent_runtime_checkpoints (
                    checkpoint_id, task_id, created_at, conversation_state_json,
                    scratchpad_summary, pending_question_json, tool_context_json,
                    workspace_snapshot_ref, rounds_consumed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.task_id,
                    checkpoint.created_at.isoformat(),
                    json.dumps(checkpoint.conversation_state, ensure_ascii=False, default=str),
                    checkpoint.scratchpad_summary,
                    json.dumps(_needs_input_payload_to_dict(checkpoint.pending_question), ensure_ascii=False, default=str),
                    json.dumps(checkpoint.tool_context, ensure_ascii=False, default=str),
                    checkpoint.workspace_snapshot_ref,
                    checkpoint.rounds_consumed,
                ),
            )
            await _update_task_fields(conn, checkpoint.task_id, {
                "status": TaskStatus.WAITING_INPUT.value,
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_question": checkpoint.pending_question.question_for_lapwing,
            })

        await self._writer.submit(op)

    async def consume_checkpoint(self, task_id: str) -> AgentRuntimeCheckpoint | None:
        async def op(conn: aiosqlite.Connection):
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM agent_runtime_checkpoints WHERE task_id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            await conn.execute(
                "DELETE FROM agent_runtime_checkpoints WHERE task_id = ?",
                (task_id,),
            )
            await _update_task_fields(conn, task_id, {
                "status": TaskStatus.RESUMING.value,
                "checkpoint_id": None,
                "checkpoint_question": None,
            })
            return _row_to_checkpoint(row)

        return await self._writer.submit(op)

    async def list_tasks(
        self,
        *,
        chat_id: str | None = None,
        owner_user_id: str | None = None,
        statuses: list[TaskStatus] | None = None,
        spec_filter: list[str] | None = None,
        include_recently_completed: bool = False,
        limit: int = 20,
    ) -> list[AgentTaskSnapshot]:
        clauses: list[str] = []
        params: list[Any] = []
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if owner_user_id is not None:
            clauses.append("owner_user_id = ?")
            params.append(owner_user_id)
        if statuses:
            clauses.append("status IN (%s)" % ",".join("?" for _ in statuses))
            params.extend([s.value for s in statuses])
        elif not include_recently_completed:
            clauses.append("status NOT IN ('completed','failed','cancelled')")
        if spec_filter:
            clauses.append("spec_id IN (%s)" % ",".join("?" for _ in spec_filter))
            params.extend(spec_filter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM agent_tasks {where} "
            "ORDER BY priority DESC, created_at DESC LIMIT ?"
        )
        params.append(limit)
        async with self._reader() as conn:
            async with conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
            snapshots = []
            for row in rows:
                events = await self._recent_event_summaries(conn, row["task_id"])
                snapshots.append(_row_to_snapshot(row, events))
        return snapshots

    async def count_for_policy(self, *, spec_id: str, chat_id: str, owner_user_id: str) -> dict[str, int]:
        async with self._reader() as conn:
            return {
                "global_active": await _count(conn, "status IN ('pending','running','resuming')", ()),
                "owner_active": await _count(conn, "owner_user_id = ? AND status IN ('pending','running','resuming')", (owner_user_id,)),
                "chat_active": await _count(conn, "chat_id = ? AND status IN ('pending','running','resuming')", (chat_id,)),
                "spec_active": await _count(conn, "spec_id = ? AND status IN ('pending','running','resuming')", (spec_id,)),
                "global_backlog": await _count(conn, "status = 'waiting_resource'", ()),
                "owner_backlog": await _count(conn, "owner_user_id = ? AND status = 'waiting_resource'", (owner_user_id,)),
                "chat_backlog": await _count(conn, "chat_id = ? AND status = 'waiting_resource'", (chat_id,)),
            }

    async def startup_recovery(self) -> RecoveryNotice | None:
        now = datetime.now(timezone.utc)
        async with self._reader() as conn:
            async with conn.execute(
                "SELECT * FROM agent_tasks WHERE status IN ('pending','running','waiting_resource','waiting_input','resuming')"
            ) as cur:
                rows = await cur.fetchall()
        infos: list[InterruptedTaskInfo] = []
        for row in rows:
            record = _row_to_record(row)
            ran_for = None
            if record.started_at is not None:
                ran_for = max(0.0, (now - record.started_at).total_seconds())
            await self.update_status(
                record.task_id,
                TaskStatus.FAILED,
                status_reason="failed_orphan",
                error_summary="System restarted while this task was active.",
                completed_at=now,
            )
            await self.append_event(AgentEvent(
                event_id=f"agent_evt_recovery_{record.task_id}",
                task_id=record.task_id,
                chat_id=record.chat_id,
                type=AgentEventType.AGENT_FAILED,
                occurred_at=now,
                summary_for_lapwing=f"Task '{record.objective}' was interrupted by system restart.",
                summary_for_owner=None,
                raw_payload_ref=None,
                salience=SalienceLevel.HIGH,
                payload={"reason": "failed_orphan"},
                sequence_in_task=999999,
            ))
            infos.append(InterruptedTaskInfo(
                task_id=record.task_id,
                spec_id=record.spec_id,
                objective=record.objective,
                previous_status=record.status,
                ran_for_seconds=ran_for,
                last_progress_summary=record.last_progress_summary,
                recovered_status="failed_orphan",
                recovered_at=now,
            ))
        await self.lifecycle_log("recovery_marked", {"count": len(infos)})
        if not infos:
            self.pending_recovery_notice = None
            return None
        notice = RecoveryNotice(
            interrupted_tasks=infos,
            last_shutdown_at=await self._last_shutdown_time(),
            recovery_at=now,
        )
        self.pending_recovery_notice = notice
        return notice

    async def lifecycle_log(self, event_type: str, metadata: dict[str, Any] | None = None) -> None:
        async def op(conn: aiosqlite.Connection):
            await conn.execute(
                "INSERT INTO system_lifecycle (event_type, occurred_at, metadata) VALUES (?, ?, ?)",
                (
                    event_type,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(metadata or {}, ensure_ascii=False, default=str),
                ),
            )

        await self._writer.submit(op)

    async def _last_shutdown_time(self) -> datetime | None:
        async with self._reader() as conn:
            async with conn.execute(
                "SELECT occurred_at FROM system_lifecycle WHERE event_type = 'shutdown' ORDER BY occurred_at DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
        return _parse_dt(row["occurred_at"]) if row else None

    async def _recent_event_summaries(self, conn: aiosqlite.Connection, task_id: str) -> list[str]:
        async with conn.execute(
            "SELECT summary_for_lapwing FROM agent_events WHERE task_id = ? ORDER BY sequence_in_task DESC LIMIT 10",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [r["summary_for_lapwing"] for r in reversed(rows)]

    def _reader(self):
        return _ReaderConnection(self.db_path)


class _ReaderConnection:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await _configure(self._conn)
        return self._conn

    async def __aexit__(self, *_exc) -> None:
        if self._conn is not None:
            await self._conn.close()


async def _configure(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")


async def _apply_migration(conn: aiosqlite.Connection) -> None:
    migration = Path(__file__).parent / "migrations" / "004_agent_tasks.sql"
    await conn.executescript(migration.read_text(encoding="utf-8"))
    await conn.commit()


def _record_values(record: AgentTaskRecord) -> tuple[Any, ...]:
    return (
        record.task_id,
        record.chat_id,
        record.owner_user_id,
        record.parent_event_id,
        record.parent_turn_id,
        record.parent_task_id,
        record.root_task_id,
        record.spawned_by,
        record.replaces_task_id,
        record.spec_id,
        record.spec_version,
        record.instance_id,
        record.objective,
        record.user_visible_summary,
        json.dumps(record.semantic_tags, ensure_ascii=False),
        record.expected_output,
        record.status.value,
        record.status_reason,
        record.created_at.isoformat(),
        record.started_at.isoformat() if record.started_at else None,
        record.completed_at.isoformat() if record.completed_at else None,
        record.last_event_at.isoformat() if record.last_event_at else None,
        record.workspace_path,
        record.result_summary,
        record.error_summary,
        json.dumps(record.artifact_refs, ensure_ascii=False),
        record.last_progress_summary,
        record.checkpoint_id,
        record.checkpoint_question,
        1 if record.cancellation_requested else 0,
        record.cancellation_reason,
        record.notify_policy.value,
        record.salience.value,
        record.priority,
        record.idempotency_key,
    )


def _row_to_record(row: aiosqlite.Row) -> AgentTaskRecord:
    from src.core.concurrent_bg_work.types import NotifyPolicy

    return AgentTaskRecord(
        task_id=row["task_id"],
        chat_id=row["chat_id"],
        owner_user_id=row["owner_user_id"],
        parent_event_id=row["parent_event_id"],
        parent_turn_id=row["parent_turn_id"],
        parent_task_id=row["parent_task_id"],
        root_task_id=row["root_task_id"],
        spawned_by=row["spawned_by"],
        replaces_task_id=row["replaces_task_id"],
        spec_id=row["spec_id"],
        spec_version=row["spec_version"],
        instance_id=row["instance_id"],
        objective=row["objective"],
        user_visible_summary=row["user_visible_summary"],
        semantic_tags=json.loads(row["semantic_tags"] or "[]"),
        expected_output=row["expected_output"],
        status=TaskStatus(row["status"]),
        status_reason=row["status_reason"],
        created_at=_parse_dt(row["created_at"]),
        started_at=_parse_dt(row["started_at"]),
        completed_at=_parse_dt(row["completed_at"]),
        last_event_at=_parse_dt(row["last_event_at"]),
        workspace_path=row["workspace_path"],
        result_summary=row["result_summary"],
        error_summary=row["error_summary"],
        artifact_refs=json.loads(row["artifact_refs"] or "[]"),
        last_progress_summary=row["last_progress_summary"],
        checkpoint_id=row["checkpoint_id"],
        checkpoint_question=row["checkpoint_question"],
        cancellation_requested=bool(row["cancellation_requested"]),
        cancellation_reason=row["cancellation_reason"],
        notify_policy=NotifyPolicy(row["notify_policy"]),
        salience=SalienceLevel(row["salience"]),
        priority=int(row["priority"]),
        idempotency_key=row["idempotency_key"],
    )


def _row_to_snapshot(row: aiosqlite.Row, events: list[str]) -> AgentTaskSnapshot:
    created = _parse_dt(row["created_at"])
    completed = _parse_dt(row["completed_at"])
    started = _parse_dt(row["started_at"])
    end = completed or datetime.now(timezone.utc)
    elapsed = (end - started).total_seconds() if started else None
    return AgentTaskSnapshot(
        task_id=row["task_id"],
        spec_id=row["spec_id"],
        objective=row["objective"],
        status=TaskStatus(row["status"]),
        started_at=started,
        elapsed_seconds=elapsed,
        last_progress_summary=row["last_progress_summary"],
        recent_events_summary=events,
        result_summary=row["result_summary"],
        error_summary=row["error_summary"],
        artifact_refs=json.loads(row["artifact_refs"] or "[]"),
        salience=SalienceLevel(row["salience"]),
        is_blocked_by_input=row["status"] == TaskStatus.WAITING_INPUT.value,
        pending_question=row["checkpoint_question"],
    )


def _row_to_checkpoint(row: aiosqlite.Row) -> AgentRuntimeCheckpoint:
    payload = json.loads(row["pending_question_json"])
    pending = AgentNeedsInputPayload(
        question_for_lapwing=payload["question_for_lapwing"],
        question_for_owner=payload.get("question_for_owner"),
        expected_answer_shape=payload.get("expected_answer_shape"),
        blocking=bool(payload.get("blocking", True)),
        timeout_at=_parse_dt(payload.get("timeout_at")),
    )
    return AgentRuntimeCheckpoint(
        checkpoint_id=row["checkpoint_id"],
        task_id=row["task_id"],
        created_at=_parse_dt(row["created_at"]),
        conversation_state=json.loads(row["conversation_state_json"]),
        scratchpad_summary=row["scratchpad_summary"],
        pending_question=pending,
        tool_context=json.loads(row["tool_context_json"]),
        workspace_snapshot_ref=row["workspace_snapshot_ref"],
        rounds_consumed=int(row["rounds_consumed"]),
    )


def _needs_input_payload_to_dict(payload: AgentNeedsInputPayload) -> dict[str, Any]:
    data = asdict(payload)
    if payload.timeout_at is not None:
        data["timeout_at"] = payload.timeout_at.isoformat()
    return data


async def _update_task_fields(conn: aiosqlite.Connection, task_id: str, updates: dict[str, Any]) -> None:
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values()) + [task_id]
    await conn.execute(f"UPDATE agent_tasks SET {assignments} WHERE task_id = ?", tuple(params))


async def _count(conn: aiosqlite.Connection, where: str, params: tuple[Any, ...]) -> int:
    async with conn.execute(f"SELECT COUNT(*) AS n FROM agent_tasks WHERE {where}", params) as cur:
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
