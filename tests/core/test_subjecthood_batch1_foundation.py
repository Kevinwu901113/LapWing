from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

from src.core.concurrent_bg_work.store import AgentTaskStore
from src.core.concurrent_bg_work.supervisor import TaskSupervisor
from src.core.task_runtime import TaskRuntime
from src.core.topic_lineage import infer_topic_key


def test_task_runtime_agent_context_carries_tool_dispatch_services():
    runtime = TaskRuntime(router=MagicMock())

    ctx = runtime.create_agent_context("researcher")

    assert ctx.services["tool_registry"] is runtime.tool_registry
    assert ctx.services["tool_dispatcher"] is runtime.tool_dispatcher


def test_weather_topic_key_is_deterministic_for_incident_location():
    intent_key, topic_key = infer_topic_key(
        text="查一下广州大学城天气",
        chat_id="chat1",
        user_id="kevin",
    )

    assert intent_key == "weather:guangzhou-university-city"
    assert topic_key == "weather:guangzhou-university-city"


@pytest.mark.asyncio
async def test_topic_stop_suppresses_same_generation_sibling_tasks(tmp_path):
    store = AgentTaskStore(tmp_path / "lapwing.db")
    await store.init()
    supervisor = TaskSupervisor(store=store, runtime_enabled=False)
    try:
        handles = []
        for idx in range(3):
            handle = await supervisor.start_agent_task(
                spec_id="researcher",
                objective=f"weather sibling {idx}",
                chat_id="chat1",
                owner_user_id="kevin",
                parent_event_id=f"event-{idx}",
                intent_key="weather:guangzhou-university-city",
                topic_key="weather:guangzhou-university-city",
                generation=7,
            )
            handles.append(handle)

        stopped_generation = await store.stop_topic(
            chat_id="chat1",
            topic_key="weather:guangzhou-university-city",
            stopped_at_generation=7,
            reason="user_cancelled_weather_topic",
        )

        assert stopped_generation == 7
        assert await store.stopped_generation(
            chat_id="chat1",
            topic_key="weather:guangzhou-university-city",
        ) == 7
        for handle in handles:
            record = await store.read(handle.task_id)
            assert record is not None
            assert record.cancellation_requested is True
            assert record.cancellation_reason == "user_cancelled_weather_topic"

        new_handle = await supervisor.start_agent_task(
            spec_id="researcher",
            objective="weather new generation",
            chat_id="chat1",
            owner_user_id="kevin",
            parent_event_id="event-new",
            intent_key="weather:guangzhou-university-city",
            topic_key="weather:guangzhou-university-city",
            generation=8,
        )
        new_record = await store.read(new_handle.task_id)
        assert new_record is not None
        assert new_record.cancellation_requested is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_intent_cancellation_flag_off_skips_topic_marker(monkeypatch):
    from src.config.settings import get_settings
    from src.core.event_queue import EventQueue
    from src.core.main_loop import MainLoop

    monkeypatch.setenv("INTENT_CANCELLATION_ENABLED", "false")
    get_settings.cache_clear()
    try:
        store = AsyncMock()
        brain = MagicMock()
        brain._background_task_store_ref = store
        loop = MainLoop(EventQueue(), brain)

        await loop._maybe_record_topic_stop("chat1", "停止所有天气查询")

        store.stop_topic_prefix.assert_not_called()
    finally:
        monkeypatch.delenv("INTENT_CANCELLATION_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_self_projection_flag_off_skips_outbound_context(monkeypatch):
    from src.config.settings import get_settings
    from src.core.brain import LapwingBrain

    monkeypatch.setenv("SELF_PROJECTION_OUTBOUND_CONTEXT_INJECTION_ENABLED", "false")
    get_settings.cache_clear()
    try:
        trajectory_store = AsyncMock()
        dummy = SimpleNamespace(trajectory_store=trajectory_store)

        result = await LapwingBrain._outbound_self_projection_message(dummy, "chat1")

        assert result is None
        trajectory_store.recent_user_visible_outbound.assert_not_called()
    finally:
        monkeypatch.delenv("SELF_PROJECTION_OUTBOUND_CONTEXT_INJECTION_ENABLED", raising=False)
        get_settings.cache_clear()


# ── 8-step incident replay with dispatcher fault injection ─────────────────

_INFRA_UNAVAILABLE_PAYLOAD = {
    "error": "tool_infra_unavailable",
    "status": "dependency_error",
    "error_code": "tool.infra_unavailable",
    "error_class": "dependency",
    "retryable": True,
    "organ": "tool_dispatcher",
    "infra_failure_class": "tool_infra_unavailable",
    "reason": "missing_tool_dispatcher",
    "command": "",
    "cwd": "/tmp",
    "blocked": True,
    "safe_details": {
        "organ": "tool_dispatcher",
        "reason": "missing_tool_dispatcher",
        "infra_failure_class": "tool_infra_unavailable",
    },
    "details_schema_version": "tool_error.v1",
}


def _build_step_ctx(send_fn, *, chat_id="chat1", task_id="task-infra"):
    """Build a minimal ToolLoopContext-like namespace for _run_step tests."""
    return SimpleNamespace(
        messages=[{"role": "user", "content": "test"}],
        tools=[{"type": "function", "function": {"name": "delegate_to_researcher"}}],
        constraints=SimpleNamespace(
            is_write_request=False,
            objective="generic",
            original_user_message="test",
            has_hard_path_constraints=False,
        ),
        chat_id=chat_id,
        task_id=task_id,
        deps=SimpleNamespace(
            shell_default_cwd="/tmp",
            execute_shell=AsyncMock(),
            policy=None,
            shell_allow_sudo=False,
        ),
        profile_obj=SimpleNamespace(name="standard", shell_policy_enabled=False, include_internal=False),
        status_callback=None,
        event_bus=None,
        on_consent_required=None,
        on_interim_text=None,
        on_typing=None,
        services={},
        adapter="owner",
        user_id="kevin",
        send_fn=send_fn,
        focus_id=None,
        state=SimpleNamespace(
            consent_required=False,
            completed=False,
            failure_reason=None,
            constraints=SimpleNamespace(
                is_write_request=False,
                objective="generic",
                has_hard_path_constraints=False,
            ),
            record_failure=lambda *a: None,
            success_message=lambda: "ok",
            failure_message=lambda: "fail",
            consent_message=lambda: "consent",
        ),
        loop_detection_state=SimpleNamespace(history=[]),
        recovery=SimpleNamespace(
            record_transition=lambda *a: None,
            reset_api_errors=lambda: None,
            can_reactive_compact=lambda: False,
            can_retry_api=lambda: False,
            can_output_recovery=lambda: False,
            reactive_compact_attempts=0,
            max_output_recovery_count=0,
            consecutive_api_errors=0,
            total_result_chars=0,
            turn_count=0,
            transition_reason="",
            MAX_REACTIVE_COMPACT=2,
            MAX_CONSECUTIVE_API_ERRORS=3,
            MAX_OUTPUT_RECOVERY=2,
        ),
        no_action_budget=SimpleNamespace(
            consume=lambda: False,
            exhausted=False,
            default=3,
            remaining=3,
            reset=lambda: None,
        ),
        error_guard=SimpleNamespace(
            record_success=lambda: None,
            record_error=lambda *_: False,
            threshold=3,
            summary="",
        ),
        last_payload=None,
        final_reply=None,
        has_used_tools=False,
        simulated_tool_retries=0,
    )


@pytest.mark.asyncio
async def test_incident_replay_dispatcher_fault_emits_single_fallback():
    """8-step incident replay.

    Step 1-2: user volatile question → tool loop delegates to researcher.
    Step 3: ToolDispatcher detects tool_dispatcher missing → infra_unavailable.
    Step 4: TaskRuntime emits ONE framework_fallback via send_fn.
    Step 5: Loop stops (completed=True), no cascade.
    Step 6: No raw AGENTNEEDSINPUT / internal state leak.
    """
    from src.core.task_runtime import TaskRuntime

    router = MagicMock()
    router.complete_with_tools = AsyncMock(return_value=SimpleNamespace(
        text=None,
        tool_calls=[SimpleNamespace(
            name="delegate_to_researcher",
            arguments={"question": "广州大学城天气怎么样"},
            id="tc-1",
        )],
        continuation_message=None,
    ))
    router.build_tool_result_message = MagicMock(return_value={
        "role": "user", "content": "tool result",
    })

    outbound_log: list[dict] = []

    async def send_fn(text, *, source="direct_reply", metadata=None):
        outbound_log.append({"text": text, "source": source, "metadata": metadata or {}})

    runtime = TaskRuntime(router=router)
    runtime._latency_monitor = MagicMock()

    # Mock _execute_tool_call to return an infra_unavailable result
    infra_text = "工具系统这边暂时不可用（tool dispatcher）。我先停止这次查询，避免继续重复派发后台任务。"
    monkeypatch_target = runtime
    original_execute = runtime._execute_tool_call
    runtime._execute_tool_call = AsyncMock(
        return_value=(infra_text, _INFRA_UNAVAILABLE_PAYLOAD, False),
    )

    ctx = _build_step_ctx(send_fn, task_id="task-incident")

    result = await runtime.run_task_loop(
        max_rounds=3,
        step_runner=lambda _round: runtime._run_step(ctx, _round),
    )

    # Step 4-5: exactly one framework_fallback, loop stopped
    assert len(outbound_log) == 1
    assert outbound_log[0]["source"] == "framework_fallback"
    assert "不可用" in outbound_log[0]["text"]
    assert result.completed is True

    # Step 6: no raw internal tokens leaked
    for entry in outbound_log:
        assert "AGENTNEEDSINPUT" not in entry["text"]
        assert "AGENT_NEEDS_INPUT" not in entry["text"]

    # Step 8: payload propagated
    assert result.last_payload.get("infra_failure_class") == "tool_infra_unavailable"


@pytest.mark.asyncio
async def test_incident_replay_step7_late_background_completion_suppressed():
    """Step 7: late same-topic background completion is suppressed by ExpressionGate."""
    from src.core.expression_gate import ExpressionGate, OutboundSource

    gate = ExpressionGate()
    send_fn = AsyncMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    # Simulate a late background completion from a cancelled topic
    delivered = await gate.send(
        "天气查询已完成：广州大学城今天晴",
        source=OutboundSource.BACKGROUND_COMPLETION,
        chat_id="chat1",
        send_fn=send_fn,
        mutation_log=mutation_log,
        metadata={
            "topic_key": "weather:guangzhou-university-city",
            "generation": 7,
            "stopped_at_generation": 7,
        },
    )

    assert delivered is False
    send_fn.assert_not_called()
    assert mutation_log.record.await_count == 1
    call_args = mutation_log.record.call_args
    assert call_args is not None
    from src.logging.state_mutation_log import MutationType
    assert call_args.args[0] == MutationType.EXPRESSION_GATE_SUPPRESSED


@pytest.mark.asyncio
async def test_incident_replay_step8_fallback_text_captured_in_trajectory():
    """Step 8: the framework_fallback text is recorded in trajectory store."""
    from src.core.expression_gate import ExpressionGate, OutboundSource

    gate = ExpressionGate()
    send_fn = AsyncMock(return_value=True)
    trajectory_store = AsyncMock()
    trajectory_store.append = AsyncMock(return_value=42)
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    delivered = await gate.send(
        "工具系统这边暂时不可用（tool dispatcher）。",
        source=OutboundSource.FRAMEWORK_FALLBACK,
        chat_id="chat1",
        send_fn=send_fn,
        trajectory_store=trajectory_store,
        mutation_log=mutation_log,
        metadata={
            "infra_failure_class": "tool_infra_unavailable",
            "organ": "tool_dispatcher",
        },
    )

    assert delivered is True
    trajectory_store.append.assert_called_once()
    append_args = trajectory_store.append.call_args
    payload = append_args.args[3]
    assert payload["text"] == "工具系统这边暂时不可用（tool dispatcher）。"
    assert payload["source"] == "framework_fallback"
    assert payload["delivered"] is True
    # text_hash must be present and deterministic
    import hashlib
    expected_hash = hashlib.sha256("工具系统这边暂时不可用（tool dispatcher）。".encode("utf-8")).hexdigest()[:16]
    assert payload["text_hash"] == expected_hash


# ── Fail-fast no cascade ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_fast_infra_breaker_blocks_cascade():
    """After first infra failure, breaker opens → second call blocked → no cascade."""
    from src.core.infra_breaker import InfraCircuitBreaker

    breaker = InfraCircuitBreaker(cooldown_schedule_seconds=(60.0, 120.0, 300.0))

    # First failure opens the breaker
    breaker.record_failure("tool_dispatcher")
    assert breaker.snapshot("tool_dispatcher")["state"] == "open"

    # Second call is blocked — no cascade possible
    allowed, reason = breaker.should_allow("tool_dispatcher")
    assert allowed is False
    assert reason == "infra_breaker_open"


@pytest.mark.asyncio
async def test_fail_fast_framework_fallback_stops_tool_loop():
    """First infra failure sends one framework_fallback, then loop stops immediately."""
    from src.core.task_runtime import TaskRuntime

    router = MagicMock()
    router.complete_with_tools = AsyncMock(return_value=SimpleNamespace(
        text=None,
        tool_calls=[SimpleNamespace(
            name="research",
            arguments={"question": "test"},
            id="tc-1",
        )],
        continuation_message=None,
    ))
    router.build_tool_result_message = MagicMock(return_value={
        "role": "user", "content": "result",
    })

    outbound: list[dict] = []

    async def send_fn(text, *, source="direct_reply", metadata=None):
        outbound.append({"text": text, "source": source})

    runtime = TaskRuntime(router=router)
    runtime._latency_monitor = MagicMock()

    infra_text = "工具系统这边暂时不可用（tool dispatcher）。我先停止这次查询，避免继续重复派发后台任务。"
    runtime._execute_tool_call = AsyncMock(
        return_value=(infra_text, _INFRA_UNAVAILABLE_PAYLOAD, False),
    )

    ctx = _build_step_ctx(send_fn, task_id="task-failfast")

    result = await runtime.run_task_loop(
        max_rounds=5,
        step_runner=lambda _round: runtime._run_step(ctx, _round),
    )

    # Only one fallback message, loop stopped immediately
    assert len(outbound) == 1
    assert outbound[0]["source"] == "framework_fallback"
    assert "不可用" in outbound[0]["text"]
    assert result.completed is True
    assert result.attempts == 1


# ── Taxonomy split: dispatcher vs tool_dispatcher ──────────────────────────


def test_taxonomy_split_dispatcher_and_tool_dispatcher_are_distinct():
    """dispatcher (pub/sub) and tool_dispatcher (tool-call) must be distinct keys."""
    from src.core.task_runtime import TaskRuntime
    from src.core.tool_dispatcher import ToolDispatcher

    runtime = TaskRuntime(router=MagicMock())
    ctx = runtime.create_agent_context("test-agent")

    # tool_dispatcher is the ToolDispatcher (tool-call organ)
    assert isinstance(ctx.services["tool_dispatcher"], ToolDispatcher)
    assert hasattr(ctx.services["tool_dispatcher"], "dispatch")

    # tool_registry is present
    assert "tool_registry" in ctx.services

    # dispatcher (pub/sub) is NOT in agent context — agents don't need event bus
    assert "dispatcher" not in ctx.services

    # The two keys must not alias the same object
    assert ctx.services.get("tool_dispatcher") is not ctx.services.get("dispatcher")
