"""Unit tests for src/core/main_loop.py — skeleton only (M1.c).

Handler behaviour for messages / inner-ticks lands in M2/M3; here we
just verify the runtime lifecycle: start / dispatch / stop / shutdown
event / cancellation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.core.authority_gate import AuthLevel
from src.core.event_queue import EventQueue
from src.core.events import InnerTickEvent, MessageEvent, SystemEvent
from src.core.main_loop import MainLoop


@pytest.mark.asyncio
async def test_loop_starts_and_stops_via_stop():
    q = EventQueue()
    loop = MainLoop(q)
    runner = asyncio.create_task(loop.run())
    await asyncio.sleep(0.01)  # let the loop reach queue.get
    await loop.stop()
    # Unblock queue.get with any event so the run() exits.
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert runner.done()


@pytest.mark.asyncio
async def test_dispatch_routes_message_event(monkeypatch):
    q = EventQueue()
    loop = MainLoop(q)
    seen: list[str] = []

    async def fake_handle(event):
        seen.append(f"msg:{event.chat_id}")

    monkeypatch.setattr(loop, "_handle_message", fake_handle)
    runner = asyncio.create_task(loop.run())
    ev = MessageEvent.from_message(
        chat_id="kev", user_id="kev", text="hi",
        adapter="qq", send_fn=_noop, auth_level=int(AuthLevel.OWNER),
    )
    await q.put(ev)
    await asyncio.sleep(0.05)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert "msg:kev" in seen


@pytest.mark.asyncio
async def test_dispatch_routes_inner_tick(monkeypatch):
    q = EventQueue()
    loop = MainLoop(q)
    seen: list[str] = []

    async def fake_handle(event):
        seen.append(f"tick:{event.reason}")

    monkeypatch.setattr(loop, "_handle_inner_tick", fake_handle)
    runner = asyncio.create_task(loop.run())
    await q.put(InnerTickEvent.make(reason="commitment_check"))
    await asyncio.sleep(0.05)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert "tick:commitment_check" in seen


@pytest.mark.asyncio
async def test_system_shutdown_event_stops_loop():
    q = EventQueue()
    loop = MainLoop(q)
    runner = asyncio.create_task(loop.run())
    await asyncio.sleep(0.01)
    await q.put(SystemEvent.make(action="shutdown"))
    await asyncio.wait_for(runner, timeout=1.0)
    assert runner.done()


@pytest.mark.asyncio
async def test_unknown_event_kind_does_not_crash_loop(monkeypatch, caplog):
    """A non-base event subclass should be logged, not raise."""
    q = EventQueue()
    loop = MainLoop(q)

    # Construct via a SystemEvent with a recognised type so we can
    # bypass isinstance branches. Patch _dispatch to feed a bare Event.
    from src.core.events import Event

    bare = Event(priority=4, kind="weird")
    runner = asyncio.create_task(loop.run())
    await asyncio.sleep(0.01)
    await q.put(bare)
    await asyncio.sleep(0.05)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=1.0)
    assert any("Unknown event kind" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_cancel_in_flight_clears_current_task():
    q = EventQueue()
    loop = MainLoop(q)

    # Simulate an in-flight handler.
    async def long_running():
        await asyncio.sleep(10)

    loop._current_task = asyncio.create_task(long_running())  # type: ignore[attr-defined]
    await loop._cancel_in_flight(reason="test")  # type: ignore[attr-defined]
    assert loop._current_task is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancel_in_flight_noop_when_no_task():
    q = EventQueue()
    loop = MainLoop(q)
    await loop._cancel_in_flight(reason="nothing-to-do")  # type: ignore[attr-defined]
    assert loop._current_task is None  # type: ignore[attr-defined]


async def _noop(*_a, **_kw):  # pragma: no cover
    return None


async def _wait_until(predicate, *, interval: float = 0.01) -> None:
    while not predicate():
        await asyncio.sleep(interval)


# ── OWNER coalesce & preemption tests ──────────────────────────────


def _make_owner_event(chat_id="kev", text="hi", done_future=None, send_fn=_noop):
    return MessageEvent.from_message(
        chat_id=chat_id, user_id="kev", text=text,
        adapter="qq", send_fn=send_fn, auth_level=int(AuthLevel.OWNER),
        done_future=done_future,
    )


class FakeBrain:
    """Minimal brain substitute that records calls."""

    def __init__(self, delay: float = 0.0, reply: str = "ok"):
        self.calls: list[dict] = []
        self._delay = delay
        self._reply = reply

    async def think_conversational(self, **kw):
        self.calls.append(kw)
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._reply

    async def think_inner(self, **kw):
        if self._delay:
            await asyncio.sleep(self._delay)
        return "", None, False


class FailingBrain(FakeBrain):
    async def think_conversational(self, **kw):
        self.calls.append(kw)
        raise RuntimeError("brain boom")


class _FakeAdapter:
    def __init__(self, connected: bool = True):
        self.connected = connected

    async def is_connected(self):
        return self.connected


class _FakeChannelManager:
    def __init__(self):
        from src.adapters.base import ChannelType
        self.last_active_channel = ChannelType.QQ
        self.sent: list[tuple[str, str]] = []
        self.adapter = _FakeAdapter()
        self._kevin_id = "999"

    def get_adapter(self, _channel):
        return self.adapter

    def resolve_delivery_target(self, channel, raw_chat_id, *, purpose="direct"):
        from src.adapters.base import ChannelType
        if channel == ChannelType.QQ:
            try:
                int(raw_chat_id)
                return raw_chat_id
            except (ValueError, TypeError):
                pass
            if purpose not in ("agent_user_status", "owner_status"):
                return None
            try:
                int(self._kevin_id)
                return self._kevin_id
            except (ValueError, TypeError):
                pass
            return None
        return raw_chat_id

    async def send(self, _channel, chat_id, text):
        if not await self.adapter.is_connected():
            from src.core.channel_manager import ChannelOperationError, make_channel_error
            raise ChannelOperationError(make_channel_error(
                channel="qq",
                operation="send_private",
                reason="adapter_disconnected",
            ))
        self.sent.append((chat_id, text))

    async def send_to_owner(self, text, prefer_channel=None):
        self.sent.append(("owner", text))


@pytest.mark.asyncio
async def test_owner_messages_coalesce_in_burst():
    """Three OWNER messages queued within the coalesce window should
    merge into a single brain call with newline-joined text."""
    q = EventQueue()
    brain = FakeBrain(delay=0.0, reply="merged-reply")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.1  # speed up test

    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(text="msg1"))
    await q.put(_make_owner_event(text="msg2"))
    await q.put(_make_owner_event(text="msg3"))

    await asyncio.sleep(0.5)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)

    assert len(brain.calls) == 1
    assert brain.calls[0]["user_message"] == "msg1\nmsg2\nmsg3"


@pytest.mark.asyncio
async def test_owner_does_not_preempt_owner():
    """A second OWNER message must NOT cancel the handler for the first."""
    q = EventQueue()
    brain = FakeBrain(delay=0.5, reply="done")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.05

    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(text="first"))
    await asyncio.sleep(0.15)  # let handler start
    await q.put(_make_owner_event(text="second"))
    await asyncio.sleep(1.5)  # let both complete

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)

    assert len(brain.calls) == 2
    assert brain.calls[0]["user_message"] == "first"
    assert brain.calls[1]["user_message"] == "second"


@pytest.mark.asyncio
async def test_owner_over_owner_cancel_probe_replies_without_waiting_for_hung_turn():
    q = EventQueue()
    brain = FakeBrain(delay=10.0, reply="late")
    sent: list[str] = []

    async def send_fn(text: str):
        sent.append(text)

    loop = MainLoop(
        q,
        brain=brain,
        foreground_turn_timeout_seconds=60,
        owner_status_probe_grace_seconds=0,
    )
    loop.OWNER_COALESCE_SECONDS = 0.01
    loop.OWNER_WATCHER_POLL_SECONDS = 0.01

    runner = asyncio.create_task(loop.run())
    await q.put(_make_owner_event(text="帮我查午饭", send_fn=send_fn))
    await asyncio.sleep(0.05)
    await q.put(_make_owner_event(text="先别管吃的了，告诉我你现在是不是还在查。", send_fn=send_fn))

    await asyncio.wait_for(_wait_until(lambda: bool(sent)), timeout=1.0)
    assert "这次查询卡住了" in sent[0]
    await asyncio.sleep(0.05)
    assert loop._current_task is None  # type: ignore[attr-defined]

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)


@pytest.mark.asyncio
async def test_owner_over_owner_hello_probe_gets_recovery_status():
    q = EventQueue()
    brain = FakeBrain(delay=10.0, reply="late")
    sent: list[str] = []

    async def send_fn(text: str):
        sent.append(text)

    loop = MainLoop(
        q,
        brain=brain,
        foreground_turn_timeout_seconds=60,
        owner_status_probe_grace_seconds=0,
    )
    loop.OWNER_COALESCE_SECONDS = 0.01
    loop.OWNER_WATCHER_POLL_SECONDS = 0.01
    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(text="帮我查午饭", send_fn=send_fn))
    await asyncio.sleep(0.05)
    await q.put(_make_owner_event(text="喂？", send_fn=send_fn))

    await asyncio.wait_for(_wait_until(lambda: bool(sent)), timeout=1.0)
    assert "这次查询卡住了" in sent[0]

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)


@pytest.mark.asyncio
async def test_foreground_user_turn_timeout_sends_recovery_reply():
    q = EventQueue()
    brain = FakeBrain(delay=10.0, reply="late")
    sent: list[str] = []
    done = asyncio.get_event_loop().create_future()

    async def send_fn(text: str):
        sent.append(text)

    loop = MainLoop(q, brain=brain, foreground_turn_timeout_seconds=1)
    loop.OWNER_COALESCE_SECONDS = 0.01
    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(text="帮我查午饭", send_fn=send_fn, done_future=done))
    result = await asyncio.wait_for(done, timeout=2.0)

    assert "这次查询卡住了" in result
    assert sent and "这次查询卡住了" in sent[0]
    assert loop._current_task is None  # type: ignore[attr-defined]

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)


@pytest.mark.asyncio
async def test_foreground_exception_sends_user_visible_fallback_to_all_futures():
    from src.core.chat_activity import ChatActivityTracker

    q = EventQueue()
    brain = FailingBrain()
    tracker = ChatActivityTracker()
    sent: list[str] = []
    done = asyncio.get_event_loop().create_future()
    coalesced_done = asyncio.get_event_loop().create_future()

    async def send_fn(text: str):
        sent.append(text)

    await q.put(_make_owner_event(text="补一句", send_fn=send_fn, done_future=coalesced_done))
    loop = MainLoop(q, brain=brain, chat_activity_tracker=tracker)
    loop.OWNER_COALESCE_SECONDS = 0.01

    await loop._handle_message(_make_owner_event(text="会炸", send_fn=send_fn, done_future=done))

    assert sent == [MainLoop.FOREGROUND_EXCEPTION_REPLY]
    assert done.result() == MainLoop.FOREGROUND_EXCEPTION_REPLY
    assert coalesced_done.result() == MainLoop.FOREGROUND_EXCEPTION_REPLY
    snapshot = tracker.snapshot("kev")
    assert snapshot.last_terminal_status == "failed_with_user_visible_error"
    assert snapshot.has_unanswered_user_message is False


@pytest.mark.asyncio
async def test_foreground_exception_failed_fallback_keeps_user_unanswered(caplog):
    from src.core.chat_activity import ChatActivityTracker

    q = EventQueue()
    brain = FailingBrain()
    tracker = ChatActivityTracker()
    done = asyncio.get_event_loop().create_future()
    attempts: list[str] = []

    async def send_fn(text: str):
        attempts.append(text)
        raise RuntimeError("channel down")

    loop = MainLoop(q, brain=brain, chat_activity_tracker=tracker)
    loop.OWNER_COALESCE_SECONDS = 0.01
    caplog.set_level(logging.WARNING, logger="lapwing.core.system_send")

    await loop._handle_message(_make_owner_event(text="会炸", send_fn=send_fn, done_future=done))

    assert attempts == [MainLoop.FOREGROUND_EXCEPTION_REPLY]
    with pytest.raises(RuntimeError, match="brain boom"):
        await done
    snapshot = tracker.snapshot("kev")
    assert snapshot.last_terminal_status == "failed_without_user_visible_error"
    assert snapshot.has_unanswered_user_message is True
    assert "system_send foreground_exception 投递失败" in caplog.text


@pytest.mark.asyncio
async def test_owner_watcher_runs_even_when_p4_cancellation_evolution_enabled(monkeypatch):
    from src.config import reload_settings

    monkeypatch.setenv("CONCURRENT_BG_WORK_ENABLED", "true")
    monkeypatch.setenv("CONCURRENT_BG_WORK_P4_CANCELLATION_EVOLUTION", "true")
    reload_settings()
    q = EventQueue()
    loop = MainLoop(q)
    runner = None
    try:
        runner = asyncio.create_task(loop.run())
        await asyncio.sleep(0.01)
        assert loop._owner_watcher_task is not None  # type: ignore[attr-defined]
    finally:
        await loop.stop()
        await q.put(InnerTickEvent.make())
        if runner is not None:
            await asyncio.wait_for(runner, timeout=1.0)
        monkeypatch.delenv("CONCURRENT_BG_WORK_ENABLED", raising=False)
        monkeypatch.delenv("CONCURRENT_BG_WORK_P4_CANCELLATION_EVOLUTION", raising=False)
        reload_settings()


@pytest.mark.asyncio
async def test_agent_parent_turn_result_delivers_status_without_inner_tick():
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    brain.channel_manager = _FakeChannelManager()
    brain.think_inner = AsyncMock(return_value=("", None, False))  # type: ignore[attr-defined]
    loop = MainLoop(EventQueue(), brain=brain)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "find food", TaskStatus.FAILED,
        None, None, None, [], None, "tool dispatch failed", [],
        SalienceLevel.HIGH, False, None,
    )
    triggering = AgentEvent(
        "evt-1", "task-1", "123456", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "AgentTaskResult tool-dispatch failure",
        None, None, SalienceLevel.HIGH,
        {"parent_turn_id": "turn-1", "parent_event_id": "evt-parent"}, 1,
    )

    await loop._dispatch(AgentTaskResultEvent(
        task_id="task-1",
        task_snapshot=snapshot,
        triggering_event=triggering,
        effective_salience=SalienceLevel.HIGH,
        delivery_target=AgentResultDeliveryTarget.PARENT_TURN,
    ))

    assert brain.channel_manager.sent
    assert "后台任务失败" in brain.channel_manager.sent[0][1]
    assert "tool-dispatch failure" in brain.channel_manager.sent[0][1]


@pytest.mark.asyncio
async def test_complete_incident_timeline_blocks_proactive_and_suppresses_stale_diagnostic():
    from src.core.chat_activity import ChatActivityTracker
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )
    from src.core.proactive_message_gate import ProactiveMessageGate
    from src.tools.personal_tools import _send_message
    from src.tools.shell_executor import ShellResult
    from src.tools.types import ToolExecutionContext, ToolExecutionRequest

    q = EventQueue()
    tracker = ChatActivityTracker()
    brain = FakeBrain(delay=10.0, reply="late")
    channel_manager = _FakeChannelManager()
    brain.channel_manager = channel_manager
    sent: list[str] = []

    async def send_fn(text: str):
        sent.append(text)

    loop = MainLoop(
        q,
        brain=brain,
        chat_activity_tracker=tracker,
        foreground_turn_timeout_seconds=60,
        owner_status_probe_grace_seconds=0,
    )
    loop.OWNER_COALESCE_SECONDS = 0.01
    loop.OWNER_WATCHER_POLL_SECONDS = 0.01
    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(chat_id="chat", text="帮我查一下华南理工大学大学城校区附近今天中午有什么好吃的，尽量查外面的店，不要只看食堂。", send_fn=send_fn))
    await q.put(_make_owner_event(chat_id="chat", text="对了，我现在在学校。", send_fn=send_fn))
    await q.put(_make_owner_event(chat_id="chat", text="我比较想吃米饭类，别太贵。", send_fn=send_fn))
    await asyncio.sleep(0.05)
    await q.put(_make_owner_event(chat_id="chat", text="先别管吃的了，告诉我你现在是不是还在查。", send_fn=send_fn))

    await asyncio.wait_for(_wait_until(lambda: bool(sent)), timeout=1.0)
    assert "这次查询卡住了" in sent[0]
    await asyncio.sleep(0.05)
    assert loop._current_task is None  # type: ignore[attr-defined]

    class _ActiveStore:
        async def has_active_user_task_for_chat(self, chat_id):
            return chat_id == "chat"

    gate = ProactiveMessageGate(
        enabled=True,
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
        min_minutes_between=0,
    )

    async def _noop_shell(_cmd):
        return ShellResult(stdout="", stderr="", return_code=0)

    ctx = ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd="/tmp",
        services={
            "channel_manager": channel_manager,
            "owner_qq_id": "chat",
            "proactive_message_gate": gate,
            "chat_activity_tracker": tracker,
            "event_queue": q,
            "main_loop": loop,
            "background_task_store": _ActiveStore(),
        },
        runtime_profile="inner_tick",
    )
    for content in ("早安～今天周五，有什么安排吗？", "早，吃了吗"):
        result = await _send_message(
            ToolExecutionRequest("send_message", {"target": "kevin_qq", "content": content}),
            ctx,
        )
        assert result.success is False
        assert "active_user_task" in result.payload["gate_reason"]

    before = list(channel_manager.sent)
    stale_trigger = AgentEvent(
        "evt-stale", "task-stale", "chat", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "old snooker task timeout",
        None, None, SalienceLevel.HIGH,
        {"parent_turn_id": "old-turn", "parent_event_id": "old-event"}, 1,
    )
    stale_snapshot = AgentTaskSnapshot(
        "task-stale", "researcher", "snooker", TaskStatus.FAILED,
        None, None, None, [], None, "old snooker task timeout", [],
        SalienceLevel.HIGH, False, None,
    )
    await loop._dispatch(AgentTaskResultEvent(
        task_id="task-stale",
        task_snapshot=stale_snapshot,
        triggering_event=stale_trigger,
        effective_salience=SalienceLevel.HIGH,
        delivery_target=AgentResultDeliveryTarget.SILENT,
        stale=True,
    ))
    assert channel_manager.sent == before

    sent.clear()
    await q.put(_make_owner_event(chat_id="chat", text="喂？", send_fn=send_fn))
    await asyncio.wait_for(_wait_until(lambda: len(brain.calls) >= 2), timeout=1.0)

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)


@pytest.mark.asyncio
async def test_owner_still_preempts_inner_tick():
    """OWNER preemption of non-OWNER tasks (inner tick) must still work."""
    q = EventQueue()
    brain = FakeBrain(delay=2.0)
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.05

    runner = asyncio.create_task(loop.run())

    await q.put(InnerTickEvent.make())
    await asyncio.sleep(0.1)  # let inner tick handler start
    await q.put(_make_owner_event(text="urgent"))
    await asyncio.sleep(1.0)

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=3.0)

    owner_calls = [c for c in brain.calls if c.get("user_message") == "urgent"]
    assert len(owner_calls) == 1


@pytest.mark.asyncio
async def test_coalesce_only_same_chat_id():
    """OWNER messages for different chat_ids must NOT be merged."""
    q = EventQueue()
    brain = FakeBrain(delay=0.0, reply="ok")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.1

    runner = asyncio.create_task(loop.run())

    await q.put(_make_owner_event(chat_id="chatA", text="a1"))
    await q.put(_make_owner_event(chat_id="chatB", text="b1"))

    await asyncio.sleep(0.5)
    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)

    assert len(brain.calls) == 2
    texts = {c["user_message"] for c in brain.calls}
    assert "a1" in texts
    assert "b1" in texts


@pytest.mark.asyncio
async def test_coalesced_done_futures_all_resolved():
    """All done_futures from coalesced messages must receive the reply."""
    q = EventQueue()
    brain = FakeBrain(delay=0.0, reply="shared-reply")
    loop = MainLoop(q, brain=brain)
    loop.OWNER_COALESCE_SECONDS = 0.1

    runner = asyncio.create_task(loop.run())

    f1 = asyncio.get_event_loop().create_future()
    f2 = asyncio.get_event_loop().create_future()
    await q.put(_make_owner_event(text="a", done_future=f1))
    await q.put(_make_owner_event(text="b", done_future=f2))

    results = await asyncio.wait_for(
        asyncio.gather(f1, f2), timeout=2.0,
    )
    assert results == ["shared-reply", "shared-reply"]

    await loop.stop()
    await q.put(InnerTickEvent.make())
    await asyncio.wait_for(runner, timeout=2.0)


# ── Fix 5: OWNER-preempt persistence regression ──────────────────────


@pytest.mark.asyncio
async def test_agent_needs_input_state_visible_after_owner_preempt(tmp_path):
    """After OWNER preempt cancels inner tick, StateView still shows WAITING_INPUT task."""
    from datetime import datetime, timezone

    from src.core.concurrent_bg_work.store import AgentTaskStore
    from src.core.concurrent_bg_work.supervisor import TaskSupervisor
    from src.core.concurrent_bg_work.types import TaskStatus
    from src.core.state_view_builder import StateViewBuilder

    class _Reg:
        async def _lookup_spec(self, name: str):
            from src.agents.spec import AgentSpec
            return AgentSpec(name=name, kind="builtin")

    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, agent_registry=_Reg())
    handle = await supervisor.start_agent_task(
        spec_id="researcher",
        objective="find lunch",
        chat_id="chat",
        owner_user_id="owner",
        parent_turn_id="turn-1",
    )
    await store.update_status(
        handle.task_id, TaskStatus.WAITING_INPUT,
        checkpoint_id="cp1", checkpoint_question="which city?",
    )

    builder = StateViewBuilder(background_task_store=store)
    view = await builder._build_concurrent_bg_work(chat_id="chat")
    assert view is not None
    waiting = [t for t in view.in_flight_tasks if t.status == TaskStatus.WAITING_INPUT]
    assert len(waiting) == 1
    assert waiting[0].is_blocked_by_input is True
    assert waiting[0].pending_question == "which city?"

    await store.close()


# ── QQ delivery target resolution tests ──────────────────────────────


@pytest.mark.asyncio
async def test_agent_status_delivery_skips_non_numeric_qq_chat_id(caplog):
    """Non-numeric chat_id with QQ channel: skip delivery, log invalid_qq_chat_id."""
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    channel_manager = _FakeChannelManager()
    # Remove kevin_id so resolution fails completely
    channel_manager._kevin_id = ""
    brain.channel_manager = channel_manager

    loop = MainLoop(EventQueue(), brain=brain)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "test", TaskStatus.FAILED,
        None, None, None, [], None, "failure", [],
        SalienceLevel.HIGH, False, None,
    )
    triggering = AgentEvent(
        "evt-1", "task-1", "chat", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "failure",
        None, None, SalienceLevel.HIGH,
        {"parent_turn_id": "turn-1", "parent_event_id": "evt-parent"}, 1,
    )

    caplog.set_level(logging.INFO, logger="lapwing.core.main_loop")
    delivered = await loop._deliver_agent_status_event(
        AgentTaskResultEvent(
            task_id="task-1",
            task_snapshot=snapshot,
            triggering_event=triggering,
            effective_salience=SalienceLevel.HIGH,
            delivery_target=AgentResultDeliveryTarget.PARENT_TURN,
        ),
        AgentResultDeliveryTarget.PARENT_TURN,
    )

    assert delivered is False
    assert channel_manager.sent == []
    assert "reason=invalid_qq_chat_id" in caplog.text
    assert "delivery_target=" in caplog.text
    assert "channel=qq" in caplog.text


@pytest.mark.asyncio
async def test_agent_status_delivery_resolves_to_kevin_id_for_non_numeric_qq():
    """Non-numeric chat_id on QQ resolves to kevin_id and delivers normally."""
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    channel_manager = _FakeChannelManager()
    channel_manager._kevin_id = "888"
    brain.channel_manager = channel_manager

    loop = MainLoop(EventQueue(), brain=brain)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "test", TaskStatus.FAILED,
        None, None, None, [], None, "failure", [],
        SalienceLevel.HIGH, False, None,
    )
    triggering = AgentEvent(
        "evt-1", "task-1", "chat", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "failure",
        None, None, SalienceLevel.HIGH,
        {"parent_turn_id": "turn-1", "parent_event_id": "evt-parent"}, 1,
    )

    delivered = await loop._deliver_agent_status_event(
        AgentTaskResultEvent(
            task_id="task-1",
            task_snapshot=snapshot,
            triggering_event=triggering,
            effective_salience=SalienceLevel.HIGH,
            delivery_target=AgentResultDeliveryTarget.PARENT_TURN,
        ),
        AgentResultDeliveryTarget.PARENT_TURN,
    )

    assert delivered is True
    # Should have sent to kevin_id "888", not "chat"
    assert channel_manager.sent
    assert channel_manager.sent[0][0] == "888"


@pytest.mark.asyncio
async def test_agent_status_delivered_false_does_not_mark_assistant_reply():
    """When delivery returns False, ChatActivityTracker.mark_assistant_reply is not called."""
    from unittest.mock import MagicMock

    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    channel_manager = _FakeChannelManager()
    channel_manager._kevin_id = ""
    brain.channel_manager = channel_manager

    tracker = MagicMock()
    loop = MainLoop(EventQueue(), brain=brain, chat_activity_tracker=tracker)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "test", TaskStatus.FAILED,
        None, None, None, [], None, "failure", [],
        SalienceLevel.HIGH, False, None,
    )
    triggering = AgentEvent(
        "evt-1", "task-1", "chat", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "failure",
        None, None, SalienceLevel.HIGH,
        {"parent_turn_id": "turn-1", "parent_event_id": "evt-parent"}, 1,
    )

    delivered = await loop._deliver_agent_status_event(
        AgentTaskResultEvent(
            task_id="task-1",
            task_snapshot=snapshot,
            triggering_event=triggering,
            effective_salience=SalienceLevel.HIGH,
            delivery_target=AgentResultDeliveryTarget.PARENT_TURN,
        ),
        AgentResultDeliveryTarget.PARENT_TURN,
    )

    assert delivered is False
    tracker.mark_assistant_reply.assert_not_called()


@pytest.mark.asyncio
async def test_agent_status_adapter_disconnected_returns_false_and_does_not_mark_reply():
    from unittest.mock import MagicMock

    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    channel_manager = _FakeChannelManager()
    channel_manager.adapter.connected = False
    brain.channel_manager = channel_manager
    tracker = MagicMock()
    loop = MainLoop(EventQueue(), brain=brain, chat_activity_tracker=tracker)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "test", TaskStatus.FAILED,
        None, None, None, [], None, "failure", [],
        SalienceLevel.HIGH, False, None,
    )
    triggering = AgentEvent(
        "evt-1", "task-1", "123456", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "failure",
        None, None, SalienceLevel.HIGH,
        {"parent_turn_id": "turn-1"}, 1,
    )

    delivered = await loop._deliver_agent_status_event(
        AgentTaskResultEvent(
            task_id="task-1",
            task_snapshot=snapshot,
            triggering_event=triggering,
            effective_salience=SalienceLevel.HIGH,
            delivery_target=AgentResultDeliveryTarget.PARENT_TURN,
        ),
        AgentResultDeliveryTarget.PARENT_TURN,
    )

    assert delivered is False
    assert channel_manager.sent == []
    tracker.mark_assistant_reply.assert_not_called()


@pytest.mark.asyncio
async def test_agent_status_delivery_skips_when_no_parent_identifiers_in_payload(caplog):
    """CHAT_STATUS delivery without parent_turn_id or parent_event_id → skip."""
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    channel_manager = _FakeChannelManager()
    brain.channel_manager = channel_manager

    loop = MainLoop(EventQueue(), brain=brain)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "test", TaskStatus.FAILED,
        None, None, None, [], None, "failure", [],
        SalienceLevel.HIGH, False, None,
    )
    # Payload has no parent_turn_id or parent_event_id
    triggering = AgentEvent(
        "evt-1", "task-1", "123456", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "failure",
        None, None, SalienceLevel.HIGH,
        {}, 1,
    )

    caplog.set_level(logging.INFO, logger="lapwing.core.main_loop")
    delivered = await loop._deliver_agent_status_event(
        AgentTaskResultEvent(
            task_id="task-1",
            task_snapshot=snapshot,
            triggering_event=triggering,
            effective_salience=SalienceLevel.HIGH,
            delivery_target=AgentResultDeliveryTarget.CHAT_STATUS,
        ),
        AgentResultDeliveryTarget.CHAT_STATUS,
    )

    assert delivered is False
    assert channel_manager.sent == []
    assert "reason=invalid_or_ambiguous_delivery_target" in caplog.text


@pytest.mark.asyncio
async def test_agent_status_delivery_skips_when_parent_turn_missing_identifiers(caplog):
    """PARENT_TURN delivery without parent_turn_id or parent_event_id → skip."""
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentResultDeliveryTarget,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    channel_manager = _FakeChannelManager()
    brain.channel_manager = channel_manager

    loop = MainLoop(EventQueue(), brain=brain)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "test", TaskStatus.FAILED,
        None, None, None, [], None, "failure", [],
        SalienceLevel.HIGH, False, None,
    )
    triggering = AgentEvent(
        "evt-1", "task-1", "chat", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "failure",
        None, None, SalienceLevel.HIGH,
        {}, 1,
    )

    caplog.set_level(logging.INFO, logger="lapwing.core.main_loop")
    delivered = await loop._deliver_agent_status_event(
        AgentTaskResultEvent(
            task_id="task-1",
            task_snapshot=snapshot,
            triggering_event=triggering,
            effective_salience=SalienceLevel.HIGH,
            delivery_target=AgentResultDeliveryTarget.PARENT_TURN,
        ),
        AgentResultDeliveryTarget.PARENT_TURN,
    )

    assert delivered is False
    assert channel_manager.sent == []
    assert "reason=invalid_or_ambiguous_delivery_target" in caplog.text


@pytest.mark.asyncio
async def test_agent_status_delivery_skips_unknown_delivery_target(caplog):
    """Unknown delivery_target value → skip with invalid_or_ambiguous_delivery_target."""
    from src.core.concurrent_bg_work.event_bus import AgentTaskResultEvent
    from src.core.concurrent_bg_work.types import (
        AgentEvent,
        AgentEventType,
        AgentTaskSnapshot,
        SalienceLevel,
        TaskStatus,
    )

    brain = FakeBrain()
    channel_manager = _FakeChannelManager()
    brain.channel_manager = channel_manager

    loop = MainLoop(EventQueue(), brain=brain)
    snapshot = AgentTaskSnapshot(
        "task-1", "researcher", "test", TaskStatus.FAILED,
        None, None, None, [], None, "failure", [],
        SalienceLevel.HIGH, False, None,
    )
    triggering = AgentEvent(
        "evt-1", "task-1", "123456", AgentEventType.AGENT_FAILED,
        datetime.now(timezone.utc), "failure",
        None, None, SalienceLevel.HIGH,
        {"parent_turn_id": "turn-1"}, 1,
    )

    caplog.set_level(logging.INFO, logger="lapwing.core.main_loop")
    delivered = await loop._deliver_agent_status_event(
        AgentTaskResultEvent(
            task_id="task-1",
            task_snapshot=snapshot,
            triggering_event=triggering,
            effective_salience=SalienceLevel.HIGH,
            delivery_target="unknown_target",
        ),
        "unknown_target",
    )

    assert delivered is False
    assert channel_manager.sent == []
    assert "reason=invalid_or_ambiguous_delivery_target" in caplog.text
