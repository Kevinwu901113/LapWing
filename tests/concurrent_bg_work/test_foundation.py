from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock

from src.adapters.base import NormalizedInboundMessage
from src.agents.base import BaseAgent
from src.agents.types import AgentResult, AgentSpec as RuntimeAgentSpec
from src.config import reload_settings
from src.agents.spec import AgentSpec
from src.config.settings import ConcurrentBackgroundWorkConfig, OperatorConfig
from src.core.main_loop import MainLoop
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
    AgentTaskHandle,
    RespondToAgentInputOp,
    StartAgentTaskOp,
    TaskStatus,
)
from src.core.event_queue import EventQueue
from src.core.events import MessageEvent
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionContext, ToolExecutionRequest
from src.tools.agent_tools import delegate_to_researcher_executor
from src.logging.state_mutation_log import MutationType


@pytest.fixture(autouse=True)
def _reset_settings_cache_after_test():
    yield
    reload_settings()


class _Registry:
    async def _lookup_spec(self, name: str):
        if name in {"researcher", "coder"}:
            return AgentSpec(name=name, kind="builtin")
        return None


class _FakeAgent:
    def __init__(self, result: AgentResult | None = None):
        self.result = result or AgentResult("task", "done", "done")
        self.messages = []

    async def execute(self, message):
        self.messages.append(message)
        return self.result


class _RuntimeRegistry:
    def __init__(self, agent):
        self.agent = agent
        self.captured_services = None

    async def _lookup_spec(self, name: str):
        if name in {"researcher", "coder"}:
            return AgentSpec(name=name, kind="builtin")
        return None

    async def get_or_create_instance(self, name: str, services_override=None):
        self.captured_services = services_override
        return self.agent


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


def _set_bg_flags(monkeypatch, **overrides):
    env_names = {
        "CONCURRENT_BG_WORK_ENABLED": False,
        "CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY": False,
        "CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC": False,
        "CONCURRENT_BG_WORK_P2D_CANCEL_AND_NEEDS_INPUT": False,
        "CONCURRENT_BG_WORK_P2_5_ARBITRATION": False,
    }
    env_names.update(overrides)
    for name, enabled in env_names.items():
        monkeypatch.setenv(name, "true" if enabled else "false")
    reload_settings()


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
    queue._queue.put_nowait(AgentTaskResultEvent(
        task_id="t1",
        task_snapshot=snapshot,
        triggering_event=event,
        effective_salience=SalienceLevel.HIGH,
    ))
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


@pytest.mark.asyncio
async def test_agent_result_event_reaches_brain_inner_turn():
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import AgentTaskSnapshot, AgentEvent, AgentEventType, SalienceLevel

    brain = AsyncMock()
    brain.think_inner = AsyncMock(return_value=("", None, False))
    snapshot = AgentTaskSnapshot("t1", "researcher", "x", TaskStatus.COMPLETED, None, None, None, [], "done", None, [], SalienceLevel.NORMAL, False, None)
    event = AgentEvent("e1", "t1", "chat", AgentEventType.AGENT_COMPLETED, datetime.now(timezone.utc), "B finished first", None, None, None, {}, 1)
    loop = MainLoop(EventQueue(), brain=brain)

    await loop._dispatch(AgentTaskResultEvent(
        task_id="t1",
        task_snapshot=snapshot,
        triggering_event=event,
        effective_salience=SalienceLevel.NORMAL,
    ))

    brain.think_inner.assert_awaited_once()
    urgent = brain.think_inner.call_args.kwargs["urgent_items"][0]
    assert urgent["type"] == "agent_task_result"
    assert urgent["task_id"] == "t1"
    assert "B finished first" in urgent["content"]


def test_background_tool_profile_overlay_is_phase_staged(monkeypatch):
    from src.core.task_runtime import TaskRuntime

    registry = ToolRegistry()
    register_concurrent_bg_work_tools(registry)
    runtime = TaskRuntime(router=object(), tool_registry=registry)

    _set_bg_flags(monkeypatch, CONCURRENT_BG_WORK_ENABLED=True, CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY=True)
    names = {tool["function"]["name"] for tool in runtime.tools_for_profile("standard")}
    assert {"list_agent_tasks", "read_agent_task"}.issubset(names)
    assert "start_agent_task" not in names
    assert "cancel_agent_task" not in names
    assert "respond_to_agent_input" not in names

    _set_bg_flags(
        monkeypatch,
        CONCURRENT_BG_WORK_ENABLED=True,
        CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY=True,
        CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC=True,
    )
    names = {tool["function"]["name"] for tool in runtime.tools_for_profile("standard")}
    assert "start_agent_task" in names
    assert "respond_to_agent_input" not in names

    _set_bg_flags(
        monkeypatch,
        CONCURRENT_BG_WORK_ENABLED=True,
        CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY=True,
        CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC=True,
        CONCURRENT_BG_WORK_P2D_CANCEL_AND_NEEDS_INPUT=True,
    )
    names = {tool["function"]["name"] for tool in runtime.tools_for_profile("standard")}
    assert {"cancel_agent_task", "respond_to_agent_input"}.issubset(names)


@pytest.mark.asyncio
async def test_delegate_to_researcher_routes_to_background_handle_under_p2c(monkeypatch):
    class FakeSupervisor:
        def __init__(self):
            self.calls = []

        async def start_agent_task(self, **kwargs):
            self.calls.append(kwargs)
            return AgentTaskHandle("task_bg", TaskStatus.PENDING, None, "/tmp/ws")

    supervisor = FakeSupervisor()
    _set_bg_flags(
        monkeypatch,
        CONCURRENT_BG_WORK_ENABLED=True,
        CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY=True,
        CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC=True,
    )
    result = await delegate_to_researcher_executor(
        ToolExecutionRequest("delegate_to_researcher", {"task": "search lunch"}),
        _ctx(supervisor),
    )

    assert result.success is True
    assert result.payload["background"] is True
    assert result.payload["task_id"] == "task_bg"
    assert supervisor.calls[0]["spec_id"] == "researcher"
    assert supervisor.calls[0]["objective"] == "search lunch"


@pytest.mark.asyncio
async def test_spawn_runtime_injects_event_bus_and_background_metadata(tmp_path):
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    registry = _RuntimeRegistry(_FakeAgent())
    supervisor = TaskSupervisor(
        store=store,
        agent_registry=registry,
        runtime_enabled=True,
    )
    handle = await supervisor.start_agent_task(
        spec_id="researcher",
        objective="find lunch",
        chat_id="chat",
        owner_user_id="owner",
        services={"existing": "yes"},
    )

    while handle.task_id in supervisor._runtime_tasks:
        await asyncio.sleep(0.01)

    assert registry.captured_services["existing"] == "yes"
    assert registry.captured_services["agent_event_bus"] is supervisor.event_bus
    assert registry.captured_services["background_task_id"] == handle.task_id
    assert registry.captured_services["background_chat_id"] == "chat"
    await store.close()


@pytest.mark.asyncio
async def test_base_agent_emit_uses_background_metadata_fallback():
    class FakeEventBus:
        def __init__(self):
            self.events = []

        async def emit(self, event):
            self.events.append(event)

    event_bus = FakeEventBus()
    agent = BaseAgent(
        RuntimeAgentSpec(
            name="researcher",
            description="",
            system_prompt="",
            model_slot="agent_execution",
        ),
        llm_router=object(),
        tool_registry=ToolRegistry(),
        mutation_log=None,
        services={
            "agent_event_bus": event_bus,
            "background_task_id": "task_bg",
            "background_chat_id": "chat",
        },
    )

    await agent._emit(MutationType.AGENT_TOOL_CALL, {"content": "progress"})

    assert len(event_bus.events) == 1
    event = event_bus.events[0]
    assert event.task_id == "task_bg"
    assert event.chat_id == "chat"
    assert event.payload["task_id"] == "task_bg"


@pytest.mark.asyncio
async def test_runtime_needs_input_saves_checkpoint_and_waits(tmp_path):
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    agent = _FakeAgent(AgentResult(
        task_id="task",
        status="needs_input",
        result="need city",
        structured_result={"question_for_lapwing": "Which city?"},
    ))
    supervisor = TaskSupervisor(
        store=store,
        agent_registry=_RuntimeRegistry(agent),
        runtime_enabled=True,
    )
    handle = await supervisor.start_agent_task(
        spec_id="researcher",
        objective="find food",
        chat_id="chat",
        owner_user_id="owner",
    )

    while handle.task_id in supervisor._runtime_tasks:
        await asyncio.sleep(0.01)

    record = await store.read(handle.task_id)
    assert record is not None
    assert record.status == TaskStatus.WAITING_INPUT
    assert record.checkpoint_id is not None
    assert record.checkpoint_question == "Which city?"
    supervisor.runtime_enabled = False
    accepted = await supervisor.respond_to_agent_input(handle.task_id, "Guangzhou")
    assert accepted.accepted is True
    assert accepted.new_status == TaskStatus.RESUMING
    await store.close()


# ── Fix 1: delegate idempotency ──────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_same_turn_id_is_idempotent(monkeypatch, tmp_path):
    """Same ctx.turn_id + same objective → same task_id, one task in store."""
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())
    _set_bg_flags(
        monkeypatch,
        CONCURRENT_BG_WORK_ENABLED=True,
        CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY=True,
        CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC=True,
    )

    ctx = ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd=".",
        services={"background_task_supervisor": supervisor},
        chat_id="chat",
        user_id="owner",
        turn_id="turn-1",
    )

    r1 = await delegate_to_researcher_executor(
        ToolExecutionRequest("delegate_to_researcher", {"task": "search lunch"}),
        ctx,
    )
    r2 = await delegate_to_researcher_executor(
        ToolExecutionRequest("delegate_to_researcher", {"task": "search lunch"}),
        ctx,
    )

    assert r1.success is True
    assert r2.success is True
    assert r1.payload["task_id"] == r2.payload["task_id"]

    tasks = await store.list_tasks(chat_id="chat")
    assert len(tasks) == 1

    await store.close()


@pytest.mark.asyncio
async def test_delegate_different_turn_id_creates_new_task(monkeypatch, tmp_path):
    """Different ctx.turn_id + same objective → different task_id, two tasks in store."""
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())
    _set_bg_flags(
        monkeypatch,
        CONCURRENT_BG_WORK_ENABLED=True,
        CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY=True,
        CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC=True,
    )

    ctx1 = ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd=".",
        services={"background_task_supervisor": supervisor},
        chat_id="chat",
        user_id="owner",
        turn_id="turn-1",
    )
    ctx2 = ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd=".",
        services={"background_task_supervisor": supervisor},
        chat_id="chat",
        user_id="owner",
        turn_id="turn-2",
    )

    r1 = await delegate_to_researcher_executor(
        ToolExecutionRequest("delegate_to_researcher", {"task": "search lunch"}),
        ctx1,
    )
    r2 = await delegate_to_researcher_executor(
        ToolExecutionRequest("delegate_to_researcher", {"task": "search lunch"}),
        ctx2,
    )

    assert r1.success is True
    assert r2.success is True
    assert r1.payload["task_id"] != r2.payload["task_id"]

    tasks = await store.list_tasks(chat_id="chat")
    assert len(tasks) == 2

    await store.close()


@pytest.mark.asyncio
async def test_delegate_missing_turn_id_creates_unique_tasks(monkeypatch, tmp_path):
    """Missing ctx.turn_id must never dedupe across calls — each call is a new task."""
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())
    _set_bg_flags(
        monkeypatch,
        CONCURRENT_BG_WORK_ENABLED=True,
        CONCURRENT_BG_WORK_P2B_TASK_SUPERVISOR_READONLY=True,
        CONCURRENT_BG_WORK_P2C_AGENT_RUNTIME_ASYNC=True,
    )

    ctx = ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd=".",
        services={"background_task_supervisor": supervisor},
        chat_id="chat",
        user_id="owner",
        turn_id="",  # missing
    )

    r1 = await delegate_to_researcher_executor(
        ToolExecutionRequest("delegate_to_researcher", {"task": "search lunch"}),
        ctx,
    )
    r2 = await delegate_to_researcher_executor(
        ToolExecutionRequest("delegate_to_researcher", {"task": "search lunch"}),
        ctx,
    )

    assert r1.success is True
    assert r2.success is True
    assert r1.payload["task_id"] != r2.payload["task_id"]

    tasks = await store.list_tasks(chat_id="chat")
    assert len(tasks) == 2

    await store.close()


@pytest.mark.asyncio
async def test_direct_start_agent_task_missing_turn_id_creates_unique_tasks(monkeypatch, tmp_path):
    """Direct start_agent_task with missing ctx.turn_id must not reuse old task."""
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())

    ctx = ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd=".",
        services={"background_task_supervisor": supervisor},
        chat_id="chat",
        user_id="owner",
        turn_id="",  # missing
    )

    r1 = await start_agent_task_executor(
        ToolExecutionRequest("start_agent_task", {"spec_id": "researcher", "objective": "find food"}),
        ctx,
    )
    r2 = await start_agent_task_executor(
        ToolExecutionRequest("start_agent_task", {"spec_id": "researcher", "objective": "find food"}),
        ctx,
    )

    assert r1.success is True
    assert r2.success is True
    assert r1.payload["task_id"] != r2.payload["task_id"]

    tasks = await store.list_tasks(chat_id="chat")
    assert len(tasks) == 2

    await store.close()


# ── Fix 2: audit coverage ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_cancelled_and_needs_input_records_mutation(tmp_path):
    """Mutation log records AGENT_CANCELLED and AGENT_NEEDS_INPUT events."""
    from src.core.concurrent_bg_work.event_bus import AgentEventBus, new_agent_event
    from src.core.concurrent_bg_work.types import AgentEventType, SalienceLevel

    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Registry())

    handle = await supervisor.start_agent_task(
        spec_id="researcher", objective="test", chat_id="chat",
        owner_user_id="owner", parent_turn_id="turn-1",
    )
    tid = handle.task_id

    bus = AgentEventBus(task_store=store, mutation_log=mutation_log)

    cancelled_ev = new_agent_event(
        task_id=tid, chat_id="chat", type=AgentEventType.AGENT_CANCELLED,
        summary="cancelled", sequence=1, salience=SalienceLevel.HIGH,
    )
    await bus.emit(cancelled_ev)
    mutation_log.record.assert_called()
    first_call_type = mutation_log.record.call_args_list[0][0][0]
    assert first_call_type == MutationType.AGENT_CANCELLED

    mutation_log.record.reset_mock()
    await store.update_status(tid, TaskStatus.WAITING_INPUT)

    needs_input_ev = new_agent_event(
        task_id=tid, chat_id="chat", type=AgentEventType.AGENT_NEEDS_INPUT,
        summary="need city", sequence=2, salience=SalienceLevel.HIGH,
        payload={"question_for_lapwing": "Which city?"},
    )
    await bus.emit(needs_input_ev)
    mutation_log.record.assert_called()
    second_call_type = mutation_log.record.call_args_list[0][0][0]
    assert second_call_type == MutationType.AGENT_NEEDS_INPUT

    await store.close()
