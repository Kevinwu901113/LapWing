from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.expression_gate import ExpressionGate, OutboundSource
from src.core.system_send import send_system_message
from src.logging.state_mutation_log import MutationType


@pytest.mark.asyncio
async def test_direct_reply_fail_open_does_not_double_send(monkeypatch):
    gate = ExpressionGate()
    send_fn = AsyncMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    async def broken_record(*_args, **_kwargs):
        raise RuntimeError("gate log broken")

    monkeypatch.setattr(gate, "_record_delivered", broken_record)

    delivered = await gate.send(
        "hello",
        source=OutboundSource.DIRECT_REPLY,
        chat_id="chat1",
        send_fn=send_fn,
        mutation_log=mutation_log,
    )

    assert delivered is True
    send_fn.assert_awaited_once_with("hello")
    assert gate.fail_open_count == 1
    assert mutation_log.record.await_args.args[0] == MutationType.EXPRESSION_GATE_FAIL_OPEN


@pytest.mark.asyncio
async def test_internal_only_source_rejected_and_audited():
    gate = ExpressionGate()
    send_fn = AsyncMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    delivered = await gate.send(
        "AGENTNEEDSINPUT",
        source=OutboundSource.AGENT_NEEDS_INPUT,
        chat_id="chat1",
        send_fn=send_fn,
        mutation_log=mutation_log,
    )

    assert delivered is False
    send_fn.assert_not_called()
    assert mutation_log.record.await_args.args[0] == MutationType.EXPRESSION_GATE_REJECTED
    assert mutation_log.record.await_args.args[1]["reason"] == "internal_only_source"


@pytest.mark.asyncio
async def test_internal_token_blacklist_rejects_user_visible_capable_source():
    gate = ExpressionGate()
    send_fn = AsyncMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    delivered = await gate.send(
        "raw AGENT_NEEDS_INPUT leaked",
        source=OutboundSource.BACKGROUND_FAILURE,
        chat_id="chat1",
        send_fn=send_fn,
        mutation_log=mutation_log,
    )

    assert delivered is False
    send_fn.assert_not_called()
    assert mutation_log.record.await_args.args[0] == MutationType.EXPRESSION_GATE_REJECTED
    assert mutation_log.record.await_args.args[1]["reason"] == "internal_state_leak"


@pytest.mark.asyncio
async def test_duplicate_infra_failure_suppressed_by_topic_scoped_key():
    now = [100.0]
    gate = ExpressionGate(now_fn=lambda: now[0])
    send_fn = AsyncMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()
    metadata = {
        "infra_failure_class": "tool_infra_unavailable",
        "organ": "tool_dispatcher",
        "topic_key": "weather:guangzhou-university-city",
        "dedup_window_seconds": 300,
    }

    first = await gate.send(
        "工具不可用",
        source=OutboundSource.FRAMEWORK_FALLBACK,
        chat_id="chat1",
        send_fn=send_fn,
        mutation_log=mutation_log,
        metadata=metadata,
    )
    second = await gate.send(
        "工具不可用",
        source=OutboundSource.FRAMEWORK_FALLBACK,
        chat_id="chat1",
        send_fn=send_fn,
        mutation_log=mutation_log,
        metadata=metadata,
    )

    assert first is True
    assert second is False
    send_fn.assert_awaited_once_with("工具不可用")
    assert mutation_log.record.await_args.args[0] == MutationType.EXPRESSION_GATE_SUPPRESSED
    assert mutation_log.record.await_args.args[1]["reason"] == "duplicate-infra-failure"


@pytest.mark.asyncio
async def test_cancelled_lineage_suppresses_background_completion():
    gate = ExpressionGate()
    send_fn = AsyncMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    delivered = await gate.send(
        "后台任务完成",
        source=OutboundSource.BACKGROUND_COMPLETION,
        chat_id="chat1",
        send_fn=send_fn,
        mutation_log=mutation_log,
        metadata={
            "topic_key": "weather:guangzhou-university-city",
            "generation": 3,
            "stopped_at_generation": 3,
        },
    )

    assert delivered is False
    send_fn.assert_not_called()
    assert mutation_log.record.await_args.args[0] == MutationType.EXPRESSION_GATE_SUPPRESSED
    assert mutation_log.record.await_args.args[1]["reason"] == "cancelled-task-result"


@pytest.mark.asyncio
async def test_expression_gate_enabled_false_uses_legacy_system_send(monkeypatch):
    from src.config.settings import get_settings

    monkeypatch.setenv("EXPRESSION_GATE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        send_fn = AsyncMock()
        mutation_log = AsyncMock()
        mutation_log.record = AsyncMock()

        delivered = await send_system_message(
            send_fn,
            "legacy",
            source="reminder_notify",
            chat_id="chat1",
            mutation_log=mutation_log,
        )

        assert delivered is True
        send_fn.assert_awaited_once_with("legacy")
        assert mutation_log.record.await_args.args[1]["source"] == "reminder_notify"
    finally:
        monkeypatch.delenv("EXPRESSION_GATE_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_direct_reply_fail_open_flag_false_surfaces_gate_error(monkeypatch):
    from src.config.settings import get_settings

    monkeypatch.setenv("EXPRESSION_GATE_FAIL_OPEN_DIRECT_REPLY", "false")
    get_settings.cache_clear()
    gate = ExpressionGate()
    send_fn = AsyncMock()

    async def broken_record(*_args, **_kwargs):
        raise RuntimeError("gate log broken")

    monkeypatch.setattr(gate, "_record_delivered", broken_record)
    try:
        with pytest.raises(RuntimeError, match="gate log broken"):
            await gate.send(
                "hello",
                source=OutboundSource.DIRECT_REPLY,
                chat_id="chat1",
                send_fn=send_fn,
            )
    finally:
        monkeypatch.delenv("EXPRESSION_GATE_FAIL_OPEN_DIRECT_REPLY", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_direct_reply_through_gate_flag_false_uses_legacy_path(monkeypatch):
    from src.config.settings import get_settings

    monkeypatch.setenv("EXPRESSION_GATE_DIRECT_REPLY_THROUGH_GATE", "false")
    get_settings.cache_clear()
    gate = ExpressionGate()
    legacy = AsyncMock(return_value=True)
    monkeypatch.setattr(gate, "_legacy_send_and_log", legacy)
    try:
        delivered = await gate.send(
            "hello",
            source=OutboundSource.DIRECT_REPLY,
            chat_id="chat1",
            send_fn=AsyncMock(),
        )
        assert delivered is True
        legacy.assert_awaited_once()
    finally:
        monkeypatch.delenv("EXPRESSION_GATE_DIRECT_REPLY_THROUGH_GATE", raising=False)
        get_settings.cache_clear()
