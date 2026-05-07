from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.adapters.base import NormalizedInboundMessage
from src.agents.spec import AgentSpec
from src.config.settings import ConcurrentBackgroundWorkConfig, OperatorConfig
from src.core.concurrent_bg_work.ingress import IngressNormalizer
from src.core.concurrent_bg_work.invariants import invariant_ids
from src.core.concurrent_bg_work.operations import reduce_operations
from src.core.concurrent_bg_work.policy import ConcurrencyPolicy
from src.core.concurrent_bg_work.speaking import SpeakingArbiter
from src.core.concurrent_bg_work.store import AgentTaskStore
from src.core.concurrent_bg_work.supervisor import SemanticMatcher, TaskSupervisor
from src.core.concurrent_bg_work.tools import (
    register_concurrent_bg_work_tools,
    start_agent_task_executor,
)
from src.core.concurrent_bg_work.types import (
    CancelAgentTaskOp,
    CancellationScope,
    RespondToAgentInputOp,
    StartAgentTaskOp,
    TaskStatus,
)
from src.core.event_queue import EventQueue
from src.core.events import MessageEvent
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class _Registry:
    async def _lookup_spec(self, name: str):
        if name in {"researcher", "coder"}:
            return AgentSpec(name=name, kind="builtin")
        return None


async def _noop_shell(_command: str):
    return None


def _ctx(supervisor) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd=".",
        services={"background_task_supervisor": supervisor},
        chat_id="chat",
        user_id="owner",
    )


def test_p0_flags_default_off_and_invariant_matrix_complete():
    flags = ConcurrentBackgroundWorkConfig()
    assert flags.enabled is False
    assert flags.p1_ingress_correctness is False
    assert flags.p2b_task_supervisor_readonly is False
    assert OperatorConfig().emergency_control_enabled is False
    assert invariant_ids() == {f"T-INV-{i}" for i in range(1, 28)}


def test_ingress_normalizer_assigns_unique_event_and_stable_dedupe_key():
    msg = NormalizedInboundMessage(
        channel="qq",
        chat_id="c1",
        user_id="u1",
        text="  Hello   ",
        message_id="m1",
    )
    normalizer = IngressNormalizer()
    first = normalizer.normalize(msg)
    second = normalizer.normalize(msg)
    assert first.event_id != second.event_id
    assert first.idempotency_key == second.idempotency_key


def test_message_event_carries_ingress_metadata():
    event = MessageEvent.from_message(
        chat_id="c",
        user_id="u",
        text="hi",
        adapter="qq",
        send_fn=lambda *_a, **_kw: None,
        auth_level=3,
        event_id="evt_1",
        idempotency_key="ingress:k",
    )
    assert event.event_id == "evt_1"
    assert event.idempotency_key == "ingress:k"


@pytest.mark.asyncio
async def test_store_startup_recovery_marks_active_tasks_failed_orphan(tmp_path):
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())
    handle = await supervisor.start_agent_task(
        spec_id="researcher",
        objective="find lunch",
        chat_id="chat",
        owner_user_id="owner",
    )
    notice = await store.startup_recovery()
    assert notice is not None
    assert notice.interrupted_tasks[0].task_id == handle.task_id
    record = await store.read(handle.task_id)
    assert record is not None
    assert record.status == TaskStatus.FAILED
    assert record.status_reason == "failed_orphan"
    await store.close()


@pytest.mark.asyncio
async def test_supervisor_idempotent_start_list_read_and_cancel(tmp_path):
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())
    first = await supervisor.start_agent_task(
        spec_id="researcher",
        objective="find lunch",
        chat_id="chat",
        owner_user_id="owner",
        parent_turn_id="turn1",
    )
    second = await supervisor.start_agent_task(
        spec_id="researcher",
        objective="find lunch",
        chat_id="chat",
        owner_user_id="owner",
        parent_turn_id="turn1",
    )
    assert first.task_id == second.task_id
    tasks = await supervisor.list_agent_tasks(chat_id="chat")
    assert [task.task_id for task in tasks] == [first.task_id]
    snapshot = await supervisor.read_agent_task(first.task_id)
    assert snapshot is not None
    assert snapshot.objective == "find lunch"
    result = await supervisor.cancel_agent_task(
        scope=CancellationScope.TASK_ID,
        chat_id="chat",
        owner_user_id="owner",
        task_id=first.task_id,
    )
    assert result.cancelled_task_ids == [first.task_id]
    await store.close()


@pytest.mark.asyncio
async def test_backlog_quota_is_independent_from_active_quota(tmp_path):
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    policy = ConcurrencyPolicy(
        global_max_tasks=0,
        backlog_max_global=1,
        backlog_max_per_chat=1,
        backlog_max_per_owner=1,
    )
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry(), policy=policy)
    first = await supervisor.start_agent_task(
        spec_id="researcher",
        objective="a",
        chat_id="chat",
        owner_user_id="owner",
        parent_turn_id="t1",
    )
    assert first.status == TaskStatus.WAITING_RESOURCE
    with pytest.raises(Exception):
        await supervisor.start_agent_task(
            spec_id="researcher",
            objective="b",
            chat_id="chat",
            owner_user_id="owner",
            parent_turn_id="t2",
        )
    await store.close()


def test_semantic_cancel_ambiguous_returns_candidates_without_target():
    matcher = SemanticMatcher(scorer=lambda _q, candidates: [
        (candidates[0], 0.82),
        (candidates[1], 0.76),
    ])
    from src.core.concurrent_bg_work.types import AgentTaskSnapshot, SalienceLevel
    candidates = [
        AgentTaskSnapshot("t1", "researcher", "search food A", TaskStatus.RUNNING, None, None, None, [], None, None, [], SalienceLevel.NORMAL, False, None),
        AgentTaskSnapshot("t2", "researcher", "search food B", TaskStatus.RUNNING, None, None, None, [], None, None, [], SalienceLevel.NORMAL, False, None),
    ]
    kind, top, ambiguous = matcher.decide("cancel food", candidates)
    assert kind == "ambiguous"
    assert top is None
    assert [item.task_id for item in ambiguous] == ["t1", "t2"]


def test_reduce_operations_cancel_wins_and_duplicate_start_merges():
    ops = [
        RespondToAgentInputOp(task_id="t1", answer="x"),
        CancelAgentTaskOp(scope=CancellationScope.TASK_ID, task_id="t1"),
        StartAgentTaskOp(spec_id="researcher", objective="a", idempotency_key="same"),
        StartAgentTaskOp(spec_id="researcher", objective="a", idempotency_key="same"),
    ]
    reduced, warnings = reduce_operations(None, ops)
    assert any(isinstance(op, CancelAgentTaskOp) for op in reduced)
    assert sum(isinstance(op, StartAgentTaskOp) for op in reduced) == 1
    assert warnings


@pytest.mark.asyncio
async def test_speaking_arbiter_serializes_same_chat():
    arbiter = SpeakingArbiter()
    events: list[str] = []

    async def speak(name: str):
        async with arbiter.acquire("chat"):
            events.append(f"start:{name}")
            await asyncio.sleep(0.01)
            events.append(f"end:{name}")

    await asyncio.gather(speak("a"), speak("b"))
    assert events in (["start:a", "end:a", "start:b", "end:b"], ["start:b", "end:b", "start:a", "end:a"])


def test_event_queue_detects_high_salience_event():
    queue = EventQueue()
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import AgentTaskSnapshot, AgentEvent, AgentEventType, SalienceLevel
    snapshot = AgentTaskSnapshot("t1", "researcher", "x", TaskStatus.FAILED, None, None, None, [], None, "err", [], SalienceLevel.HIGH, False, None)
    event = AgentEvent("e1", "t1", "chat", AgentEventType.AGENT_FAILED, datetime.now(timezone.utc), "failed", None, None, SalienceLevel.HIGH, {}, 1)
    queue._queue.put_nowait(AgentTaskResultEvent("t1", snapshot, event, SalienceLevel.HIGH))
    assert queue.has_high_salience_event() is True


@pytest.mark.asyncio
async def test_tool_registration_and_start_executor(tmp_path):
    registry = ToolRegistry()
    register_concurrent_bg_work_tools(registry)
    assert registry.get("start_agent_task") is not None
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())
    result = await start_agent_task_executor(
        ToolExecutionRequest("start_agent_task", {
            "spec_id": "researcher",
            "objective": "find lunch",
        }),
        _ctx(supervisor),
    )
    assert result.success is True
    assert result.payload["status"] == "pending"
    await store.close()
