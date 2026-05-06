"""Tests for proactive outbound trajectory recording in send_message."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trajectory_store import TrajectoryEntryType
from src.tools.personal_tools import _send_message, _resolve_proactive_target_chat_id
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_qq_ctx(*, gate_decision="allow", trajectory_store=None, owner_qq_id="919231551"):
    qq = MagicMock()
    qq.send_private_message = AsyncMock()
    cm = MagicMock()
    cm.get_adapter = MagicMock(return_value=qq)
    gate = MagicMock()
    gate.evaluate = MagicMock(return_value=MagicMock(
        decision=gate_decision, reason="test", bypassed=False,
    ))
    services = {
        "channel_manager": cm,
        "owner_qq_id": owner_qq_id,
        "proactive_send_active": True,
        "proactive_message_gate": gate,
    }
    if trajectory_store is not None:
        services["trajectory_store"] = trajectory_store
    ctx = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
        runtime_profile="inner_tick",
        chat_id="chat1",
    )
    return ctx, qq


def _make_desktop_ctx(*, gate_decision="allow", trajectory_store=None, connected=True):
    desktop = MagicMock()
    desktop.send_text = AsyncMock()
    desktop.is_connected = AsyncMock(return_value=connected)
    if connected:
        desktop.connections = {"12345": MagicMock()}
    else:
        desktop.connections = {}
    desktop.config = {"kevin_id": "owner"}
    cm = MagicMock()
    cm.get_adapter = MagicMock(return_value=desktop)
    gate = MagicMock()
    gate.evaluate = MagicMock(return_value=MagicMock(
        decision=gate_decision, reason="test", bypassed=False,
    ))
    services = {
        "channel_manager": cm,
        "owner_qq_id": "919231551",
        "proactive_send_active": True,
        "proactive_message_gate": gate,
    }
    if trajectory_store is not None:
        services["trajectory_store"] = trajectory_store
    ctx = ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
        runtime_profile="inner_tick",
        chat_id="chat1",
    )
    return ctx, desktop


class TestResolveProactiveTargetChatId:
    def test_kevin_qq_resolves_to_owner_qq_id(self):
        ctx, _ = _make_qq_ctx()
        result = _resolve_proactive_target_chat_id("kevin_qq", ctx)
        assert result == "919231551"

    def test_kevin_qq_returns_none_when_no_owner_qq_id(self):
        ctx, _ = _make_qq_ctx(owner_qq_id="")
        result = _resolve_proactive_target_chat_id("kevin_qq", ctx)
        assert result is None

    def test_kevin_desktop_resolves_to_prefix_with_connection_id(self):
        ctx, _ = _make_desktop_ctx(connected=True)
        result = _resolve_proactive_target_chat_id("kevin_desktop", ctx)
        assert result == "desktop:12345"

    def test_kevin_desktop_returns_none_when_no_connections(self):
        ctx, _ = _make_desktop_ctx(connected=False)
        result = _resolve_proactive_target_chat_id("kevin_desktop", ctx)
        assert result is None

    def test_qq_group_returns_none(self):
        ctx, _ = _make_qq_ctx()
        result = _resolve_proactive_target_chat_id("qq_group:123456", ctx)
        assert result is None

    def test_unknown_target_returns_none(self):
        ctx, _ = _make_qq_ctx()
        result = _resolve_proactive_target_chat_id("unknown_target", ctx)
        assert result is None


class TestSendMessageTrajectoryWrite:
    @pytest.mark.asyncio
    async def test_successful_kevin_qq_send_writes_proactive_outbound(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "下午好～"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()
        call_args = ts.append.call_args
        assert call_args.args[0] == TrajectoryEntryType.PROACTIVE_OUTBOUND
        assert call_args.args[1] == "919231551"
        assert call_args.args[2] == "assistant"
        content = call_args.args[3]
        assert content["text"] == "下午好～"
        assert content["target"] == "kevin_qq"
        assert content["channel"] == "qq"
        assert content["kind"] == "proactive_outbound"
        assert content["source"] == "send_message"

    @pytest.mark.asyncio
    async def test_successful_kevin_desktop_send_writes_proactive_outbound(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, desktop = _make_desktop_ctx(trajectory_store=ts, connected=True)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_desktop", "content": "hello from desktop"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()
        call_args = ts.append.call_args
        assert call_args.args[1] == "desktop:12345"
        content = call_args.args[3]
        assert content["channel"] == "desktop"

    @pytest.mark.asyncio
    async def test_gate_deny_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, qq = _make_qq_ctx(gate_decision="deny", trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "should be denied"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        assert "gate_decision" in result.payload
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_defer_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, qq = _make_qq_ctx(gate_decision="defer", trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "should be deferred"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_adapter_exception_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, qq = _make_qq_ctx(trajectory_store=ts)
        qq.send_private_message = AsyncMock(side_effect=RuntimeError("QQ down"))

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "will fail"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_desktop_disconnected_does_not_write_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock()
        ctx, desktop = _make_desktop_ctx(trajectory_store=ts, connected=False)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_desktop", "content": "will fail"},
        )
        result = await _send_message(req, ctx)

        assert result.success is False
        ts.append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_urgent_bypass_success_still_writes_trajectory(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={
                "target": "kevin_qq",
                "content": "紧急提醒",
                "category": "reminder_due",
                "urgent": True,
            },
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trajectory_append_failure_does_not_fail_send_message(self):
        ts = AsyncMock()
        ts.append = AsyncMock(side_effect=RuntimeError("db down"))
        ts.has_recent_entry = AsyncMock(return_value=True)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "still delivers"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        assert result.payload["sent"] is True
        ts.append.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_recent_inbound_warns_but_still_writes(self):
        ts = AsyncMock()
        ts.append = AsyncMock(return_value=42)
        ts.has_recent_entry = AsyncMock(return_value=False)
        ctx, qq = _make_qq_ctx(trajectory_store=ts)

        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "first contact"},
        )
        result = await _send_message(req, ctx)

        assert result.success is True
        ts.append.assert_awaited_once()
        ts.has_recent_entry.assert_awaited_once()
