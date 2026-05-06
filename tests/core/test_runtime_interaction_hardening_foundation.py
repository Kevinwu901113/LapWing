from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from src.adapters.base import BaseAdapter, ChannelType, NormalizedInboundMessage
from src.adapters.qq_adapter import QQAdapter
from src.agents.policy import AgentPolicy, AgentPolicyViolation
from src.agents.spec import AgentLifecyclePolicy, AgentSpec
from src.config.settings import CapabilitiesConfig, RuntimeInteractionHardeningConfig
from src.core.channel_manager import ChannelManager, StartupError
from src.core.context_governance import ReversibleContextRecord, new_reversible_record_id
from src.core.inbound import (
    BusyInputMode,
    BusySessionController,
    CommandInterceptLayer,
    InboundMessageGate,
)
from src.core.runtime_profiles import ZERO_TOOLS_PROFILE
from src.core.state_serializer import serialize
from src.core.state_view_builder import StateViewBuilder
from src.core.steering import SteeringStore
from src.core.task_runtime import TaskRuntime
from src.logging.state_mutation_log import MutationType, StateMutationLog
from src.models.message import RichMessage
from src.tools.registry import ToolRegistry
from src.tools.types import (
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolResultStatus,
    ToolSpec,
)


async def _noop_shell(_command: str):
    return None


def _context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd=str(tmp_path),
        workspace_root=str(tmp_path),
        services={},
    )


@pytest.mark.asyncio
async def test_invalid_tool_args_return_structured_validation_error_and_do_not_execute(tmp_path):
    registry = ToolRegistry()
    calls = {"count": 0}

    async def executor(request, context):
        calls["count"] += 1
        return ToolExecutionResult(success=True, payload={"ok": True})

    registry.register(ToolSpec(
        name="demo_tool",
        description="demo",
        json_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["safe"],
                    "description": "Mode hint from /home/kevin/private",
                },
                "count": {"type": "integer"},
            },
            "required": ["mode", "count"],
        },
        executor=executor,
        capability="general",
    ))

    result = await registry.execute(
        ToolExecutionRequest("demo_tool", {"mode": "unsafe", "api_key": "secret"}),
        context=_context(tmp_path),
    )

    assert calls["count"] == 0
    assert result.success is False
    assert result.payload["status"] == ToolResultStatus.VALIDATION_ERROR.value
    assert result.payload["error_code"] == ToolErrorCode.SCHEMA_VALIDATION_FAILED.value
    assert result.payload["retryable"] is True
    details = result.payload["safe_details"]
    assert details["tool_name"] == "demo_tool"
    assert "mode" in details["invalid_fields"]
    assert "/home/kevin/private" not in str(details)
    assert "secret" not in str(details)


@pytest.mark.asyncio
async def test_successful_tool_payload_is_unchanged(tmp_path):
    registry = ToolRegistry()

    async def executor(request, context):
        return ToolExecutionResult(success=True, payload={"value": 42})

    registry.register(ToolSpec(
        name="demo_success",
        description="demo",
        json_schema={"type": "object", "properties": {}, "required": []},
        executor=executor,
        capability="general",
    ))

    result = await registry.execute(
        ToolExecutionRequest("demo_success", {}),
        context=_context(tmp_path),
    )

    assert result.success is True
    assert result.payload == {"value": 42}


@pytest.mark.asyncio
async def test_dispatcher_permission_error_is_not_schema_validation(tmp_path):
    registry = ToolRegistry()

    async def executor(request, context):
        return ToolExecutionResult(success=True, payload={"ok": True})

    registry.register(ToolSpec(
        name="restricted_tool",
        description="demo",
        json_schema={"type": "object", "properties": {}, "required": []},
        executor=executor,
        capability="general",
    ))
    runtime = TaskRuntime(router=None, tool_registry=registry)

    result = await runtime.tool_dispatcher.dispatch(
        request=ToolExecutionRequest("restricted_tool", {}),
        profile=ZERO_TOOLS_PROFILE,
        workspace_root=str(tmp_path),
        services={},
    )

    assert result.success is False
    assert result.payload["status"] == ToolResultStatus.PERMISSION_ERROR.value
    assert result.payload["error_code"] == ToolErrorCode.PERMISSION_DENIED.value


class _FakeAdapter(BaseAdapter):
    channel_type = ChannelType.QQ

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_message(self, chat_id: str, message: RichMessage) -> None:
        pass

    async def is_connected(self) -> bool:
        return True


def test_adapter_capability_matrix_strict_and_non_strict():
    manager = ChannelManager()
    adapter = _FakeAdapter({"group_ids": ["100"]})
    manager.register(ChannelType.QQ, adapter)

    warnings = manager.validate_adapter_capabilities(strict=False)
    assert warnings
    assert ("qq", "private") in manager.disabled_routes
    assert ("qq", "group") in manager.disabled_routes

    with pytest.raises(StartupError):
        manager.validate_adapter_capabilities(strict=True)


def test_qq_declares_private_and_group_send_capabilities():
    adapter = QQAdapter(config={"kevin_id": "1", "group_ids": ["100"]})

    assert adapter.capabilities.can_send_private is True
    assert adapter.capabilities.can_send_group is True
    normalized = adapter.normalize_inbound({
        "post_type": "message",
        "message_type": "private",
        "user_id": 1,
        "message_id": 2,
        "message": "hello",
    })
    assert normalized is not None
    assert normalized.text == "hello"
    assert normalized.chat_id == "1"


def test_inbound_gate_command_and_busy_modes():
    message = NormalizedInboundMessage(
        channel="qq",
        chat_id="c1",
        user_id="u1",
        text="/models",
        message_id="m1",
    )

    gate = InboundMessageGate(allow_untrusted=True)
    assert gate.evaluate(message, auth_level=1).accepted is True
    intercepted = CommandInterceptLayer().intercept(message)
    assert intercepted.mode == BusyInputMode.COMMAND
    assert intercepted.command == "models"

    controller = BusySessionController(queue_max_per_chat=2)
    queued = controller.classify(
        NormalizedInboundMessage(
            channel="qq",
            chat_id="c1",
            user_id="u1",
            text="new task",
            message_id="m2",
        ),
        session_state="running",
    )
    assert queued.mode == BusyInputMode.QUEUE
    assert controller.queue_for("c1")[0].message.message_id == "m2"

    interrupt = controller.classify(
        NormalizedInboundMessage(
            channel="qq",
            chat_id="c1",
            user_id="u1",
            text="cancel this",
            message_id="m3",
        ),
        session_state="running",
    )
    assert interrupt.mode == BusyInputMode.INTERRUPT


@pytest.mark.asyncio
async def test_steering_store_stateview_dynamic_ack_and_expire(tmp_path):
    mutation_log = StateMutationLog(tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs")
    await mutation_log.init()
    store = SteeringStore(tmp_path / "lapwing.db", mutation_log=mutation_log)
    await store.init()
    controller = BusySessionController(queue_ttl=timedelta(seconds=30))
    source = NormalizedInboundMessage(
        channel="desktop",
        chat_id="chat",
        user_id="owner",
        text="/steer keep it brief",
        message_id="m1",
    )
    event = controller.steering_event_from_message(source, source_trust_level="owner")
    await store.add(event)

    builder = StateViewBuilder(
        soul_path=tmp_path / "soul.md",
        constitution_path=tmp_path / "constitution.md",
        steering_store=store,
        steering_max_count=1,
    )
    state = await builder.build_for_chat("chat", trajectory_turns_override=())
    assert [e.id for e in state.pending_steering_events] == [event.id]
    rendered = serialize(state).system_prompt
    assert "keep it brief" in rendered
    assert "待处理的用户转向" in rendered

    await store.acknowledge([event.id])
    state_after_ack = await builder.build_for_chat("chat", trajectory_turns_override=())
    assert state_after_ack.pending_steering_events == ()

    expired = controller.steering_event_from_message(
        NormalizedInboundMessage(
            channel="desktop",
            chat_id="chat",
            user_id="owner",
            text="/steer stale",
            message_id="m2",
        ),
        ttl=timedelta(seconds=-1),
    )
    await store.add(expired)
    assert await store.expire_stale() == 1
    assert await store.pending(chat_id="chat") == ()

    rows = await mutation_log.query_by_type(MutationType.STEERING_RECEIVED, limit=5)
    assert rows
    await store.close()
    await mutation_log.close()


def test_capability_and_runtime_flags_default_safe():
    assert CapabilitiesConfig().read_tools_enabled is False
    assert CapabilitiesConfig().retrieval_enabled is False
    assert RuntimeInteractionHardeningConfig().enabled is True
    assert RuntimeInteractionHardeningConfig().adapter_strict_mode is False


def test_reversible_context_record_safe_payload_excludes_content():
    record = ReversibleContextRecord(
        id=new_reversible_record_id(),
        record_type="identity_fact",
        content="private raw content",
        source_handles=("trajectory:1",),
        confidence=0.8,
        why_this_matters="long-term preference",
        user_intent_evidence="explicit user statement",
        reversibility_handle="proposal:1",
    )

    payload = record.as_safe_payload()
    assert payload["approval_state"] == "pending"
    assert payload["reversibility_handle"] == "proposal:1"
    assert "content" not in payload


@pytest.mark.asyncio
async def test_dynamic_agent_policy_blocks_self_grants_and_high_risk_persistence():
    class Catalog:
        async def get_by_name(self, name):
            return None

        async def count(self, kind=None):
            return 0

    policy = AgentPolicy(catalog=Catalog(), llm_router=None)

    async def safe_lint(_prompt):
        @dataclass
        class Result:
            verdict: str = "safe"
            risk_categories: list = None
            reason: str = ""
        return Result()

    policy._semantic_lint = safe_lint

    self_grant = AgentSpec(
        name="bad",
        system_prompt="help",
        lifecycle=AgentLifecyclePolicy(mode="persistent"),
        allowed_tools=["grant_tool"],
    )
    with pytest.raises(AgentPolicyViolation) as exc:
        await policy.validate_save(self_grant, run_history=["run"])
    assert exc.value.reason == "agent_allowed_tools_self_grant_denied"

    high_risk = AgentSpec(
        name="risky",
        system_prompt="help",
        lifecycle=AgentLifecyclePolicy(mode="persistent"),
        risk_level="high",
        approval_state="pending",
    )
    with pytest.raises(AgentPolicyViolation) as exc2:
        await policy.validate_save(high_risk, run_history=["run"])
    assert exc2.value.reason == "high_risk_persistent_agent_requires_approval"
