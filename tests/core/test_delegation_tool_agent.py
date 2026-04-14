"""Test delegation_tool executor — agent-aware routing through DelegationManager."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.delegation import AgentRole, DelegationResult
from src.tools.delegation_tool import delegate_task_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_context(**overrides) -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=overrides.get("services", {}),
        chat_id=overrides.get("chat_id", "test-chat"),
    )


class TestDelegateTaskWithAgentName:
    @pytest.mark.asyncio
    async def test_agent_name_passed_to_delegation_task(self):
        """agent 参数应传递到 DelegationTask.agent_name。"""
        mock_dm = AsyncMock()
        mock_dm.delegate.return_value = [
            DelegationResult(
                task_index=0, role=AgentRole.GENERAL, success=True,
                summary="调研完成", duration_seconds=1.5,
                tool_calls_count=3, agent_name="researcher",
            ),
        ]

        ctx = _make_context(services={"delegation_manager": mock_dm})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"agent": "researcher", "goal": "查找 RAG 论文", "context": "2026 年"}],
        })

        result = await delegate_task_executor(req, ctx)
        assert result.success

        # 验证传给 DelegationManager 的任务包含 agent_name
        call_args = mock_dm.delegate.call_args
        tasks = call_args[1]["tasks"]
        assert len(tasks) == 1
        assert tasks[0].agent_name == "researcher"
        assert tasks[0].goal == "查找 RAG 论文"

    @pytest.mark.asyncio
    async def test_agent_label_in_output(self):
        """结果输出应显示 agent 名称。"""
        mock_dm = AsyncMock()
        mock_dm.delegate.return_value = [
            DelegationResult(
                task_index=0, role=AgentRole.GENERAL, success=True,
                summary="报告内容", duration_seconds=2.0,
                tool_calls_count=5, agent_name="researcher",
            ),
        ]

        ctx = _make_context(services={"delegation_manager": mock_dm})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"agent": "researcher", "goal": "Test", "context": "Ctx"}],
        })

        result = await delegate_task_executor(req, ctx)
        assert "researcher" in result.payload["output"]

    @pytest.mark.asyncio
    async def test_without_agent_falls_back_to_role(self):
        """不指定 agent 时回退到 role。"""
        mock_dm = AsyncMock()
        mock_dm.delegate.return_value = [
            DelegationResult(
                task_index=0, role=AgentRole.GENERAL, success=True,
                summary="ok", duration_seconds=0.5, tool_calls_count=1,
            ),
        ]

        ctx = _make_context(services={"delegation_manager": mock_dm})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Test", "context": "Ctx"}],
        })

        result = await delegate_task_executor(req, ctx)
        assert result.success
        tasks = mock_dm.delegate.call_args[1]["tasks"]
        assert tasks[0].agent_name is None
        assert tasks[0].role == AgentRole.GENERAL


class TestDelegateTaskErrors:
    @pytest.mark.asyncio
    async def test_empty_tasks_returns_error(self):
        ctx = _make_context(services={"delegation_manager": AsyncMock()})
        req = ToolExecutionRequest(name="delegate_task", arguments={"tasks": []})
        result = await delegate_task_executor(req, ctx)
        assert not result.success

    @pytest.mark.asyncio
    async def test_no_delegation_manager_returns_error(self):
        ctx = _make_context(services={})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Test", "context": "Ctx"}],
        })
        result = await delegate_task_executor(req, ctx)
        assert not result.success
        assert "委托系统未初始化" in result.payload["error"]

    @pytest.mark.asyncio
    async def test_empty_goal_skipped(self):
        mock_dm = AsyncMock()
        ctx = _make_context(services={"delegation_manager": mock_dm})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "", "context": "Ctx", "agent": "researcher"}],
        })
        result = await delegate_task_executor(req, ctx)
        assert not result.success
        assert "没有有效的任务" in result.payload["error"]

    @pytest.mark.asyncio
    async def test_max_three_tasks(self):
        """最多处理 3 个任务。"""
        mock_dm = AsyncMock()
        mock_dm.delegate.return_value = [
            DelegationResult(
                task_index=i, role=AgentRole.GENERAL, success=True,
                summary="ok", duration_seconds=0, tool_calls_count=0,
            )
            for i in range(3)
        ]

        ctx = _make_context(services={"delegation_manager": mock_dm})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [
                {"goal": f"Task {i}", "context": "Ctx", "agent": "researcher"}
                for i in range(5)
            ],
        })

        await delegate_task_executor(req, ctx)
        tasks = mock_dm.delegate.call_args[1]["tasks"]
        assert len(tasks) == 3
