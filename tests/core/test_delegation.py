"""DelegationManager 单元测试。"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.delegation import (
    AgentRole,
    BLOCKED_TOOLS,
    DELEGATION_BLOCKED_CAPABILITIES,
    DelegationManager,
    DelegationResult,
    DelegationTask,
    MAX_DELEGATION_DEPTH,
    ROLE_TOOLSETS,
)


# ── 数据类测试 ─────────────────────────────────────────────────────────────────

class TestDelegationTask:
    def test_default_role(self):
        t = DelegationTask(goal="test", context="ctx")
        assert t.role == AgentRole.GENERAL

    def test_default_max_iterations(self):
        t = DelegationTask(goal="test", context="ctx")
        assert t.max_iterations == 20

    def test_custom_role(self):
        t = DelegationTask(goal="g", context="c", role=AgentRole.RESEARCHER)
        assert t.role == AgentRole.RESEARCHER


# ── 工具集常量测试 ──────────────────────────────────────────────────────────────

class TestRoleToolsets:
    def test_all_roles_have_toolsets(self):
        for role in AgentRole:
            assert role in ROLE_TOOLSETS, f"{role} 缺少工具集定义"

    def test_no_role_allows_blocked_tools(self):
        for role, tools in ROLE_TOOLSETS.items():
            overlap = tools & BLOCKED_TOOLS
            assert not overlap, f"{role} 允许了被阻止的工具: {overlap}"

    def test_researcher_has_web_search(self):
        assert "web_search" in ROLE_TOOLSETS[AgentRole.RESEARCHER]

    def test_coder_has_execute_shell(self):
        assert "execute_shell" in ROLE_TOOLSETS[AgentRole.CODER]

    def test_sensitive_tools_blocked_for_all_roles(self):
        """记忆写入、调度、递归委托等敏感操作对所有角色都不可用。"""
        sensitive = {
            "delegate_task", "memory_note", "memory_edit", "memory_delete",
            "schedule_task", "cancel_scheduled_task",
        }
        all_role_tools = set()
        for tools in ROLE_TOOLSETS.values():
            all_role_tools |= tools
        assert not (all_role_tools & sensitive), (
            f"敏感工具泄漏到角色工具集: {all_role_tools & sensitive}"
        )


# ── DelegationManager 测试 ──────────────────────────────────────────────────────

def _make_manager(**overrides) -> DelegationManager:
    router = MagicMock()
    registry = MagicMock()
    registry.list_tools.return_value = []
    registry.get.return_value = None
    return DelegationManager(
        router=overrides.get("router", router),
        tool_registry=overrides.get("tool_registry", registry),
        event_bus=overrides.get("event_bus", None),
    )


class TestDelegate:
    @pytest.mark.asyncio
    async def test_empty_tasks(self):
        mgr = _make_manager()
        results = await mgr.delegate([], chat_id="test")
        assert results == []

    @pytest.mark.asyncio
    async def test_truncates_to_max_concurrent(self):
        mgr = _make_manager()
        tasks = [DelegationTask(goal=f"task{i}", context="c") for i in range(10)]
        mgr._execute_child = AsyncMock(
            side_effect=lambda i, t, c: DelegationResult(
                task_index=i, role=t.role, success=True,
                summary="ok", duration_seconds=0, tool_calls_count=0,
            )
        )
        results = await mgr.delegate(tasks, chat_id="test")
        assert len(results) == mgr.MAX_CONCURRENT

    @pytest.mark.asyncio
    async def test_results_sorted_by_index(self):
        mgr = _make_manager()
        tasks = [DelegationTask(goal=f"t{i}", context="c") for i in range(2)]

        async def fake_child(index, task, chat_id):
            # 故意让第二个先完成
            if index == 0:
                await asyncio.sleep(0.01)
            return DelegationResult(
                task_index=index, role=task.role, success=True,
                summary=f"done{index}", duration_seconds=0, tool_calls_count=0,
            )

        mgr._execute_child = fake_child
        results = await mgr.delegate(tasks, chat_id="test")
        assert [r.task_index for r in results] == [0, 1]

    @pytest.mark.asyncio
    async def test_exception_wrapped_in_result(self):
        mgr = _make_manager()
        tasks = [DelegationTask(goal="fail", context="c")]
        mgr._execute_child = AsyncMock(side_effect=RuntimeError("boom"))
        results = await mgr.delegate(tasks, chat_id="test")
        assert len(results) == 1
        assert not results[0].success
        assert "boom" in results[0].error


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancels_registered_tasks(self):
        mgr = _make_manager()
        mock_task = MagicMock()
        mgr._active_tasks["k1"] = mock_task
        mgr._active_tasks["k2"] = MagicMock()
        await mgr.cancel_all()
        mock_task.cancel.assert_called_once()
        assert len(mgr._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_active_tasks_cleaned_after_delegate(self):
        """delegate() 完成后 _active_tasks 应被清空。"""
        mgr = _make_manager()
        tasks = [DelegationTask(goal="t", context="c")]
        mgr._execute_child = AsyncMock(
            return_value=DelegationResult(
                task_index=0, role=AgentRole.GENERAL, success=True,
                summary="ok", duration_seconds=0, tool_calls_count=0,
            )
        )
        await mgr.delegate(tasks, chat_id="test")
        assert len(mgr._active_tasks) == 0


class TestBuildChildSystemPrompt:
    def test_contains_goal_and_context(self):
        mgr = _make_manager()
        task = DelegationTask(goal="查找 RAG 论文", context="关注 2026 年")
        prompt = mgr._build_child_system_prompt(task)
        assert "查找 RAG 论文" in prompt
        assert "2026" in prompt


class TestExecuteChildTool:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        mgr = _make_manager()
        tc = MagicMock()
        tc.name = "nonexistent_tool"
        tc.arguments = {}
        result = await mgr._execute_child_tool(tc, chat_id="test")
        assert "未知工具" in result

    @pytest.mark.asyncio
    async def test_successful_tool_execution(self):
        from src.tools.types import ToolExecutionResult, ToolSpec

        mock_executor = AsyncMock(return_value=ToolExecutionResult(
            success=True, payload={"output": "hello world"},
        ))
        mock_spec = MagicMock()
        mock_spec.executor = mock_executor

        registry = MagicMock()
        registry.get.return_value = mock_spec
        registry.list_tools.return_value = []
        mgr = _make_manager(tool_registry=registry)

        tc = MagicMock()
        tc.name = "web_search"
        tc.arguments = {"query": "test"}
        result = await mgr._execute_child_tool(tc, chat_id="c1")
        assert result == "hello world"
        # 验证 context 参数
        call_ctx = mock_executor.call_args[0][1]
        assert call_ctx.auth_level == 2
        assert call_ctx.chat_id == "c1"

    @pytest.mark.asyncio
    async def test_tool_exception_caught(self):
        mock_spec = MagicMock()
        mock_spec.executor = AsyncMock(side_effect=ValueError("oops"))

        registry = MagicMock()
        registry.get.return_value = mock_spec
        registry.list_tools.return_value = []
        mgr = _make_manager(tool_registry=registry)

        tc = MagicMock()
        tc.name = "web_search"
        tc.arguments = {}
        result = await mgr._execute_child_tool(tc, chat_id="c1")
        assert "异常" in result


# ── 安全加固测试 ─────────────────────────────────────────────────────────────────

class TestDelegationSafety:
    def test_blocked_capabilities_defined(self):
        assert "memory" in DELEGATION_BLOCKED_CAPABILITIES
        assert "schedule" in DELEGATION_BLOCKED_CAPABILITIES

    def test_blocked_tools_includes_delegation(self):
        assert "delegate_task" in BLOCKED_TOOLS

    def test_blocked_tools_includes_memory(self):
        assert "memory_note" in BLOCKED_TOOLS
        assert "memory_edit" in BLOCKED_TOOLS
        assert "memory_delete" in BLOCKED_TOOLS

    def test_blocked_tools_includes_schedule(self):
        assert "schedule_task" in BLOCKED_TOOLS

    def test_max_depth_is_2(self):
        assert MAX_DELEGATION_DEPTH == 2

    def test_web_search_not_blocked(self):
        assert "web_search" not in BLOCKED_TOOLS

    def test_shell_not_blocked(self):
        assert "execute_shell" not in BLOCKED_TOOLS

    @pytest.mark.asyncio
    async def test_depth_exceeded_returns_failure(self):
        mgr = _make_manager()
        tasks = [DelegationTask(goal="test", context="ctx")]
        results = await mgr.delegate(tasks, "chat1", depth=MAX_DELEGATION_DEPTH)
        assert len(results) == 1
        assert results[0].success is False
        assert "深度超限" in results[0].summary

    @pytest.mark.asyncio
    async def test_depth_zero_proceeds(self):
        """depth=0 应正常执行（不被深度检查拦截）"""
        mgr = _make_manager()
        # 会因 LLM mock 失败，但不会被深度检查拦截
        results = await mgr.delegate(
            [DelegationTask(goal="test", context="ctx")],
            "chat1",
            depth=0,
        )
        # 应该尝试执行（可能因 mock 失败，但不是 depth error）
        assert len(results) == 1
        if not results[0].success:
            assert "深度超限" not in results[0].summary
