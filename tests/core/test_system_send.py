"""Tests for src.core.system_send — framework-level "send to user" helper."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.system_send import send_system_message


@pytest.mark.asyncio
async def test_send_system_message_delivers_and_records():
    send_fn = AsyncMock()
    trajectory = AsyncMock()
    trajectory.append = AsyncMock(return_value=42)
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    delivered = await send_system_message(
        send_fn,
        "⏰ 喝水",
        source="reminder_notify",
        chat_id="chat1",
        adapter="qq",
        trajectory_store=trajectory,
        mutation_log=mutation_log,
    )

    assert delivered is True
    send_fn.assert_awaited_once_with("⏰ 喝水")
    trajectory.append.assert_awaited_once()
    mutation_log.record.assert_awaited_once()

    # mutation_log payload carries trajectory_id + source
    _, kwargs = mutation_log.record.call_args
    assert kwargs["chat_id"] == "chat1"
    _, args, *_ = mutation_log.record.call_args
    payload = mutation_log.record.call_args.args[1]
    assert payload["source"] == "reminder_notify"
    assert payload["trajectory_id"] == 42
    assert payload["delivered"] is True


@pytest.mark.asyncio
async def test_send_system_message_returns_false_on_send_failure_but_still_records():
    async def failing_send(text: str) -> None:
        raise RuntimeError("channel down")

    trajectory = AsyncMock()
    trajectory.append = AsyncMock(return_value=None)
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock()

    delivered = await send_system_message(
        failing_send,
        "boom",
        source="llm_error",
        trajectory_store=trajectory,
        mutation_log=mutation_log,
    )

    assert delivered is False
    # Recording still happens — audit trail must capture the attempt
    trajectory.append.assert_awaited_once()
    mutation_log.record.assert_awaited_once()
    payload = mutation_log.record.call_args.args[1]
    assert payload["delivered"] is False


@pytest.mark.asyncio
async def test_send_system_message_survives_recording_failures():
    send_fn = AsyncMock()
    trajectory = AsyncMock()
    trajectory.append = AsyncMock(side_effect=RuntimeError("trajectory db down"))
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock(side_effect=RuntimeError("log db down"))

    delivered = await send_system_message(
        send_fn,
        "hello",
        source="confirmation",
        trajectory_store=trajectory,
        mutation_log=mutation_log,
    )

    # Delivery succeeded, recording failed — recording failures are swallowed
    assert delivered is True
    send_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_system_message_with_no_stores_still_works():
    send_fn = AsyncMock()

    delivered = await send_system_message(
        send_fn,
        "smoke",
        source="reminder_notify",
    )

    assert delivered is True
    send_fn.assert_awaited_once_with("smoke")
