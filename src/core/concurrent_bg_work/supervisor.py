from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.agents.service_observability import log_required_service_presence
from src.core.concurrent_bg_work.event_bus import AgentEventBus, new_agent_event
from src.core.concurrent_bg_work.policy import (
    ConcurrencyGate,
    ConcurrencyPolicy,
    NewTaskRequest,
    ResourceExhaustedError,
)
from src.core.concurrent_bg_work.runtime import AgentRuntime, CancellationToken
from src.core.concurrent_bg_work.store import AgentTaskStore, DuplicateTaskError
from src.core.concurrent_bg_work.types import (
    AgentEventType,
    AgentTaskHandle,
    AgentTaskRecord,
    AgentTaskSnapshot,
    CancellationResult,
    CancellationScope,
    NotifyPolicy,
    RespondResult,
    SalienceLevel,
    TaskStatus,
)

logger = logging.getLogger("lapwing.core.concurrent_bg_work.supervisor")


class ToolValidationError(ValueError):
    pass


class SemanticMatcher:
    SCORE_THRESHOLD_UNIQUE = 0.75
    MARGIN_THRESHOLD_UNIQUE = 0.15
    SCORE_THRESHOLD_AMBIGUOUS_MIN = 0.50

    def __init__(self, scorer: Callable[[str, list[AgentTaskSnapshot]], list[tuple[AgentTaskSnapshot, float]]] | None = None):
        self._scorer = scorer

    def decide(self, query: str, candidates: list[AgentTaskSnapshot]):
        if self._scorer is not None:
            scored = self._scorer(query, candidates)
        else:
            scored = [(c, _token_score(query, c.objective)) for c in candidates]
        scored.sort(key=lambda item: -item[1])
        if not scored or scored[0][1] < self.SCORE_THRESHOLD_AMBIGUOUS_MIN:
            return "no_match", None, []
        top, top_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else None
        if top_score >= self.SCORE_THRESHOLD_UNIQUE and (
            second_score is None or top_score - second_score >= self.MARGIN_THRESHOLD_UNIQUE
        ):
            return "unique", top, []
        ambiguous = [snapshot for snapshot, score in scored if score >= self.SCORE_THRESHOLD_AMBIGUOUS_MIN]
        return "ambiguous", None, ambiguous


class TaskSupervisor:
    def __init__(
        self,
        *,
        store: AgentTaskStore,
        agent_registry=None,
        agent_event_bus: AgentEventBus | None = None,
        workspace_root: str | Path = "/tmp/lapwing/agent_runs",
        policy: ConcurrencyPolicy | None = None,
        runtime_enabled: bool = False,
        semantic_matcher: SemanticMatcher | None = None,
    ) -> None:
        self.store = store
        self.agent_registry = agent_registry
        self.event_bus = agent_event_bus or AgentEventBus(task_store=store)
        self.workspace_root = Path(workspace_root)
        self.gate = ConcurrencyGate(store, policy)
        self.runtime_enabled = runtime_enabled
        self.semantic_matcher = semantic_matcher or SemanticMatcher()
        self._runtime_tasks: dict[str, asyncio.Task] = {}
        self._tokens: dict[str, CancellationToken] = {}

    async def start_agent_task(
        self,
        *,
        spec_id: str,
        objective: str,
        chat_id: str,
        owner_user_id: str,
        parent_event_id: str = "",
        parent_turn_id: str | None = None,
        parent_task_id: str | None = None,
        context: dict[str, Any] | None = None,
        expected_output: str | None = None,
        notify_policy: NotifyPolicy = NotifyPolicy.AUTO,
        salience: SalienceLevel = SalienceLevel.NORMAL,
        priority: int = 0,
        replaces_task_id: str | None = None,
        idempotency_key: str | None = None,
        spawned_by: str = "lapwing",
        services: dict[str, Any] | None = None,
    ) -> AgentTaskHandle:
        if not spec_id.strip():
            raise ToolValidationError("spec_id is required")
        if not objective.strip():
            raise ToolValidationError("objective is required")
        if salience == SalienceLevel.CRITICAL:
            salience = SalienceLevel.HIGH
        await self._validate_spec_exists(spec_id)
        if replaces_task_id:
            old = await self.store.read(replaces_task_id)
            if old is None or old.status not in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                raise ToolValidationError("replaces_task_id must point to a terminal task")
        key = idempotency_key or _idempotency_key(parent_turn_id or parent_event_id, spec_id, objective)
        existing = await self.store.read_by_idempotency_key(key)
        if existing is not None:
            return AgentTaskHandle(
                task_id=existing.task_id,
                status=existing.status,
                estimated_first_progress_at=None,
                workspace_path=existing.workspace_path,
            )
        decision = await self.gate.check(NewTaskRequest(
            spec_id=spec_id,
            chat_id=chat_id,
            owner_user_id=owner_user_id,
            priority=priority,
        ))
        now = datetime.now(timezone.utc)
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        workspace_path = str(self.workspace_root / chat_id / task_id)
        Path(workspace_path).mkdir(parents=True, exist_ok=True)
        record = AgentTaskRecord(
            task_id=task_id,
            chat_id=chat_id,
            owner_user_id=owner_user_id,
            parent_event_id=parent_event_id or f"synthetic_{task_id}",
            parent_turn_id=parent_turn_id,
            parent_task_id=parent_task_id,
            root_task_id=parent_task_id or task_id,
            spawned_by=spawned_by,  # type: ignore[arg-type]
            replaces_task_id=replaces_task_id,
            spec_id=spec_id,
            spec_version=None,
            instance_id=f"{spec_id}:{task_id}",
            objective=objective,
            user_visible_summary=objective[:200],
            semantic_tags=[],
            expected_output=expected_output,
            status=decision.target_status,
            status_reason=decision.reason,
            created_at=now,
            started_at=None,
            completed_at=None,
            last_event_at=None,
            workspace_path=workspace_path,
            result_summary=None,
            error_summary=None,
            artifact_refs=[],
            last_progress_summary=None,
            checkpoint_id=None,
            checkpoint_question=None,
            cancellation_requested=False,
            cancellation_reason=None,
            notify_policy=notify_policy,
            salience=salience,
            priority=priority,
            idempotency_key=key,
        )
        try:
            await self.store.create_task(record)
        except DuplicateTaskError:
            reread = await self.store.read_by_idempotency_key(key)
            if reread is None:
                raise
            return AgentTaskHandle(reread.task_id, reread.status, None, reread.workspace_path)
        if record.status == TaskStatus.PENDING and self.runtime_enabled:
            self._spawn_runtime(record, services or {})
        return AgentTaskHandle(
            task_id=record.task_id,
            status=record.status,
            estimated_first_progress_at=None,
            workspace_path=record.workspace_path,
        )

    async def list_agent_tasks(
        self,
        *,
        chat_id: str | None = None,
        status_filter: list[TaskStatus] | None = None,
        spec_filter: list[str] | None = None,
        include_recently_completed: bool = False,
        max_results: int = 20,
    ) -> list[AgentTaskSnapshot]:
        return await self.store.list_tasks(
            chat_id=chat_id,
            statuses=status_filter,
            spec_filter=spec_filter,
            include_recently_completed=include_recently_completed,
            limit=max_results,
        )

    async def read_agent_task(self, task_id: str) -> AgentTaskSnapshot | None:
        snapshots = await self.store.list_tasks(
            statuses=[
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_RESOURCE,
                TaskStatus.WAITING_INPUT,
                TaskStatus.RESUMING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ],
            include_recently_completed=True,
            limit=1000,
        )
        for snapshot in snapshots:
            if snapshot.task_id == task_id:
                return snapshot
        return None

    async def cancel_agent_task(
        self,
        *,
        scope: CancellationScope,
        chat_id: str,
        owner_user_id: str,
        task_id: str | None = None,
        semantic_query: str | None = None,
        reason: str = "lapwing_decision",
    ) -> CancellationResult:
        now = datetime.now(timezone.utc)
        targets: list[str] = []
        if scope == CancellationScope.TASK_ID:
            if not task_id:
                raise ToolValidationError("task_id is required for task_id cancellation")
            targets = [task_id]
        elif scope == CancellationScope.SEMANTIC_MATCH:
            candidates = await self.store.list_tasks(chat_id=chat_id, limit=50)
            kind, top, ambiguous = self.semantic_matcher.decide(semantic_query or "", candidates)
            if kind == "ambiguous":
                return CancellationResult([], [], ambiguous, now)
            if kind == "unique" and top is not None:
                targets = [top.task_id]
        elif scope == CancellationScope.CHAT_BACKGROUND:
            targets = [s.task_id for s in await self.store.list_tasks(chat_id=chat_id, limit=100)]
        elif scope == CancellationScope.ALL_OWNER_TASKS:
            targets = [s.task_id for s in await self.store.list_tasks(owner_user_id=owner_user_id, limit=100)]
        else:
            targets = []

        cancelled: list[str] = []
        skipped: list[str] = []
        for tid in targets:
            record = await self.store.read(tid)
            if record is None or record.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                skipped.append(tid)
                continue
            await self.store.update_status(
                tid,
                TaskStatus.CANCELLED,
                completed_at=now,
                cancellation_requested=True,
                cancellation_reason=reason,
            )
            token = self._tokens.get(tid)
            if token is not None:
                token.cancel()
            task = self._runtime_tasks.get(tid)
            if task is not None:
                task.cancel()
            await self.event_bus.emit(new_agent_event(
                task_id=tid,
                chat_id=record.chat_id,
                type=AgentEventType.AGENT_CANCELLED,
                summary=f"Cancelled {record.objective[:160]}",
                sequence=999998,
                salience=SalienceLevel.HIGH,
            ))
            cancelled.append(tid)
        return CancellationResult(cancelled, skipped, None, now)

    async def respond_to_agent_input(self, task_id: str, answer: str | dict) -> RespondResult:
        record = await self.store.read(task_id)
        if record is None:
            return RespondResult(False, TaskStatus.FAILED)
        if record.status != TaskStatus.WAITING_INPUT:
            return RespondResult(False, record.status)
        checkpoint = await self.store.consume_checkpoint(task_id)
        if checkpoint is None:
            await self.store.update_status(task_id, TaskStatus.FAILED, status_reason="missing_checkpoint")
            return RespondResult(False, TaskStatus.FAILED)
        await self.store.update_status(task_id, TaskStatus.RESUMING)
        if self.runtime_enabled:
            updated = await self.store.read(task_id)
            if updated is not None:
                self._spawn_runtime(updated, {"checkpoint_answer": answer})
        return RespondResult(True, TaskStatus.RESUMING)

    def _spawn_runtime(self, record: AgentTaskRecord, services: dict[str, Any]) -> None:
        if self.agent_registry is None:
            return
        if record.spec_id == "researcher":
            log_required_service_presence(
                logger,
                "TaskSupervisor._spawn_runtime.input_services",
                services,
            )
        child_services = dict(services)
        child_services.update({
            "agent_event_bus": self.event_bus,
            "background_task_id": record.task_id,
            "background_chat_id": record.chat_id,
            "background_owner_user_id": record.owner_user_id,
        })
        if record.spec_id == "researcher":
            log_required_service_presence(
                logger,
                "TaskSupervisor._spawn_runtime.child_services",
                child_services,
            )
        token = CancellationToken()
        self._tokens[record.task_id] = token
        runtime = AgentRuntime(
            task_id=record.task_id,
            agent_registry=self.agent_registry,
            event_bus=self.event_bus,
            store=self.store,
            spec_id=record.spec_id,
            chat_id=record.chat_id,
            owner_user_id=record.owner_user_id,
            objective=record.objective,
            expected_output=record.expected_output,
            services=child_services,
            cancellation_token=token,
        )
        task = asyncio.create_task(runtime.run(), name=f"agent-runtime:{record.task_id}")
        self._runtime_tasks[record.task_id] = task
        task.add_done_callback(lambda _task, tid=record.task_id: self._runtime_tasks.pop(tid, None))

    async def _validate_spec_exists(self, spec_id: str) -> None:
        if self.agent_registry is None:
            return
        lookup = getattr(self.agent_registry, "_lookup_spec", None)
        if lookup is not None:
            spec = await lookup(spec_id)
            if spec is None:
                raise ToolValidationError(f"unknown spec_id: {spec_id}")
            return
        if hasattr(self.agent_registry, "get"):
            agent = self.agent_registry.get(spec_id)
            if agent is None:
                raise ToolValidationError(f"unknown spec_id: {spec_id}")


def _idempotency_key(turn_id: str, spec_id: str, objective: str) -> str:
    digest = hashlib.sha256(objective.strip().encode("utf-8")).hexdigest()[:16]
    return f"agent:{turn_id}:{spec_id}:{digest}"


def _token_score(query: str, text: str) -> float:
    q = set(query.lower().split())
    t = set(text.lower().split())
    if not q or not t:
        return 0.0
    return len(q & t) / max(len(q), len(t))
