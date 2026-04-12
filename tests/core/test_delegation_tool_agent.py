"""Test delegation_tool routing through AgentDispatcher."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.core.agent_protocol import AgentNotify, AgentNotifyKind, AgentUrgency
from src.tools.delegation_tool import delegate_task_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_context(**overrides) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=overrides.get("services", {}),
        chat_id=overrides.get("chat_id", "test-chat"),
    )


class TestDelegateTaskWithAgentDispatcher:
    @pytest.mark.asyncio
    async def test_routes_through_dispatcher_when_available(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch.return_value = AgentNotify(
            agent_name="researcher",
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.LATER,
            headline="Found 3 papers",
            detail="Paper 1, Paper 2, Paper 3",
        )

        ctx = _make_context(services={"agent_dispatcher": mock_dispatcher})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Search papers", "context": "About AI"}],
        })

        result = await delegate_task_executor(req, ctx)
        assert result.success
        assert result.payload["result"] == "Found 3 papers"
        assert result.payload["detail"] == "Paper 1, Paper 2, Paper 3"
        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatcher_error_returns_failure(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch.return_value = AgentNotify(
            agent_name="researcher",
            kind=AgentNotifyKind.ERROR,
            urgency=AgentUrgency.SOON,
            headline="Agent failed",
            detail="Connection error",
        )

        ctx = _make_context(services={"agent_dispatcher": mock_dispatcher})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Search papers", "context": "ctx"}],
        })

        result = await delegate_task_executor(req, ctx)
        assert not result.success
        assert result.payload["error"] == "Agent failed"

    @pytest.mark.asyncio
    async def test_dispatcher_empty_goal_returns_error(self):
        mock_dispatcher = AsyncMock()
        ctx = _make_context(services={"agent_dispatcher": mock_dispatcher})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "", "context": "ctx"}],
        })

        result = await delegate_task_executor(req, ctx)
        assert not result.success
        assert "缺少任务目标" in result.payload["error"]
        mock_dispatcher.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatcher_passes_target_agent(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch.return_value = AgentNotify(
            agent_name="coder",
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.LATER,
            headline="Done",
        )

        ctx = _make_context(services={"agent_dispatcher": mock_dispatcher})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Fix bug", "context": "ctx", "agent": "coder"}],
        })

        await delegate_task_executor(req, ctx)
        call_kwargs = mock_dispatcher.dispatch.call_args[1]
        assert call_kwargs["target_agent"] == "coder"

    @pytest.mark.asyncio
    async def test_falls_back_to_delegation_manager(self):
        """When agent_dispatcher is not in services, falls back to delegation_manager."""
        mock_dm = AsyncMock()
        mock_dm.delegate.return_value = []

        ctx = _make_context(services={"delegation_manager": mock_dm})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Test", "context": "Ctx"}],
        })

        result = await delegate_task_executor(req, ctx)
        mock_dm.delegate.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_system_returns_error(self):
        """When neither dispatcher nor delegation_manager available."""
        ctx = _make_context(services={})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Test", "context": "Ctx"}],
        })

        result = await delegate_task_executor(req, ctx)
        assert not result.success
        assert "委托系统未初始化" in result.payload["error"]
