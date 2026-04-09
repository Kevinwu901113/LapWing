"""Lapwing 子 Agent 委托系统。

让 Lapwing 拆解复杂任务并行委托给专项子 agent（Researcher / Coder 等）。
子 agent 使用全新对话上下文、受限工具集，不继承人格。

关键设计差异（vs Hermes）：
- 委托由 Lapwing 自主决定，用户看到的是"她在安排人做事"
- 子 agent 有角色名，呈现在 UI 事件中
- 结果摘要由 Lapwing 用自己的语气重新表达
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.api.event_bus import DesktopEventBus
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry

logger = logging.getLogger("lapwing.core.delegation")


class AgentRole(Enum):
    RESEARCHER = "researcher"     # 信息搜集和调研
    CODER = "coder"               # 写代码和调试
    BROWSER = "browser"           # 浏览网页和内容提取
    FILE_AGENT = "file_agent"     # 文件操作
    GENERAL = "general"           # 通用任务


# 每个角色允许的工具名集合
ROLE_TOOLSETS: dict[AgentRole, set[str]] = {
    AgentRole.RESEARCHER: {"web_search", "web_fetch", "read_file"},
    AgentRole.CODER: {"execute_shell", "read_file", "write_file", "run_python_code"},
    AgentRole.BROWSER: {"web_search", "web_fetch"},
    AgentRole.FILE_AGENT: {"read_file", "write_file", "execute_shell"},
    AgentRole.GENERAL: {"web_search", "web_fetch", "read_file", "execute_shell"},
}

# 所有子 agent 一律禁止的工具
BLOCKED_TOOLS: set[str] = {
    "delegate_task",        # 禁止递归委托
    "memory_note",          # 不允许写 Lapwing 的记忆
    "memory_edit",          # 不允许改记忆
    "memory_delete",        # 不允许删记忆
    "memory_list",
    "memory_read",
    "memory_search",
    "schedule_task",        # 不允许设提醒
    "list_scheduled_tasks",
    "cancel_scheduled_task",
    "send_image",           # 不允许发图
    "trace_mark",           # 不允许标记轨迹
    "activate_skill",       # 不允许激活技能
    "session_search",       # 不需要搜索历史
}


@dataclass
class DelegationTask:
    """单个委托任务的定义。"""
    goal: str
    context: str
    role: AgentRole = AgentRole.GENERAL
    max_iterations: int = 20


@dataclass
class DelegationResult:
    """子 agent 执行结果。"""
    task_index: int
    role: AgentRole
    success: bool
    summary: str
    duration_seconds: float
    tool_calls_count: int
    error: str | None = None


class DelegationManager:
    """管理子 agent 的创建、执行、结果收集。

    每个子 agent：
    - 使用全新对话（隔离的 messages 列表）
    - 只获得角色允许的工具集（排除 BLOCKED_TOOLS）
    - 不注入 Lapwing 人格（是工具人，不是分身）
    - 通过 event_bus 发布进度事件
    """

    MAX_CONCURRENT = 3

    def __init__(
        self,
        router: "LLMRouter",
        tool_registry: "ToolRegistry",
        event_bus: "DesktopEventBus | None" = None,
    ) -> None:
        self._router = router
        self._tool_registry = tool_registry
        self._event_bus = event_bus
        self._active_tasks: dict[str, asyncio.Task] = {}

    async def delegate(
        self,
        tasks: list[DelegationTask],
        chat_id: str,
    ) -> list[DelegationResult]:
        """执行一组委托任务，最多 MAX_CONCURRENT 个并行。

        Returns:
            按 task_index 排序的结果列表。
        """
        tasks = tasks[:self.MAX_CONCURRENT]

        # 发布委托开始事件
        if self._event_bus:
            await self._event_bus.publish("delegation.started", {
                "chat_id": chat_id,
                "children": [
                    {"index": i, "role": t.role.value, "goal": t.goal[:100]}
                    for i, t in enumerate(tasks)
                ],
            })

        # 并行执行
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

        async def run_one(index: int, task: DelegationTask) -> DelegationResult:
            async with semaphore:
                return await self._execute_child(index, task, chat_id)

        raw_results = await asyncio.gather(
            *[run_one(i, t) for i, t in enumerate(tasks)],
            return_exceptions=True,
        )

        # 处理异常
        final_results: list[DelegationResult] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, Exception):
                final_results.append(DelegationResult(
                    task_index=i,
                    role=tasks[i].role,
                    success=False,
                    summary=f"执行失败: {r}",
                    duration_seconds=0,
                    tool_calls_count=0,
                    error=str(r),
                ))
            else:
                final_results.append(r)

        final_results.sort(key=lambda r: r.task_index)

        # 发布完成事件
        if self._event_bus:
            await self._event_bus.publish("delegation.completed", {
                "chat_id": chat_id,
                "results": [
                    {"index": r.task_index, "success": r.success, "role": r.role.value}
                    for r in final_results
                ],
            })

        return final_results

    async def _execute_child(
        self,
        index: int,
        task: DelegationTask,
        chat_id: str,
    ) -> DelegationResult:
        """执行单个子 agent。"""
        start = time.monotonic()

        # 构建子 agent 的 system prompt（聚焦任务，不含人格）
        system_prompt = self._build_child_system_prompt(task)

        # 过滤工具集
        allowed_names = ROLE_TOOLSETS.get(task.role, set()) - BLOCKED_TOOLS
        child_tools = [
            spec.to_function_tool()
            for spec in self._tool_registry.list_tools()
            if spec.name in allowed_names and spec.visibility == "model"
        ]

        # 隔离的消息列表
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task.goal},
        ]

        tool_calls_count = 0
        final_text = ""

        for _round in range(task.max_iterations):
            try:
                turn = await self._router.complete_with_tools(
                    messages=messages,
                    tools=child_tools,
                    slot="agent_execution",
                    max_tokens=2048,
                    session_key=f"delegation:{chat_id}:{index}",
                    origin=f"delegation.child.{task.role.value}",
                )
            except Exception as e:
                logger.warning("子 agent [%d:%s] LLM 调用失败: %s", index, task.role.value, e)
                return DelegationResult(
                    task_index=index,
                    role=task.role,
                    success=False,
                    summary=f"LLM 调用失败: {e}",
                    duration_seconds=time.monotonic() - start,
                    tool_calls_count=tool_calls_count,
                    error=str(e),
                )

            # 无 tool call → 完成
            if turn.text and not turn.tool_calls:
                final_text = turn.text
                break

            if not turn.tool_calls:
                final_text = turn.text or ""
                break

            # 追加 assistant 消息
            if turn.continuation_message:
                messages.append(turn.continuation_message)

            # 执行工具
            for tc in turn.tool_calls:
                tool_calls_count += 1

                if self._event_bus:
                    await self._event_bus.publish("delegation.tool_call", {
                        "child_index": index,
                        "role": task.role.value,
                        "tool": tc.name,
                        "round": _round,
                    })

                # 安全检查：确认工具在允许列表中
                if tc.name not in allowed_names:
                    tool_result = f"工具 {tc.name} 不允许在此上下文中使用。"
                else:
                    tool_result = await self._execute_child_tool(tc)

                # 构建工具结果消息
                result_msg = self._router.build_tool_result_message(
                    [(tc, tool_result)],
                    slot="agent_execution",
                    session_key=f"delegation:{chat_id}:{index}",
                )
                if isinstance(result_msg, list):
                    messages.extend(result_msg)
                else:
                    messages.append(result_msg)

        elapsed = time.monotonic() - start

        return DelegationResult(
            task_index=index,
            role=task.role,
            success=True,
            summary=final_text or "（子 agent 未产出文本结果）",
            duration_seconds=elapsed,
            tool_calls_count=tool_calls_count,
        )

    async def _execute_child_tool(self, tc) -> str:
        """执行子 agent 的工具调用。"""
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest

        spec = self._tool_registry.get(tc.name)
        if spec is None:
            return f"未知工具: {tc.name}"

        request = ToolExecutionRequest(name=tc.name, arguments=tc.arguments)

        # 构建最小化的执行上下文（不含 memory 写入权限）
        from src.tools.shell_executor import execute as shell_execute
        from config.settings import SHELL_DEFAULT_CWD

        context = ToolExecutionContext(
            execute_shell=shell_execute,
            shell_default_cwd=str(SHELL_DEFAULT_CWD),
            auth_level=0,  # 最低权限
        )

        try:
            result = await spec.executor(request, context)
            # 返回摘要文本
            if result.payload and "output" in result.payload:
                return str(result.payload["output"])[:4000]
            if result.payload and "error" in result.payload:
                return f"错误: {result.payload['error']}"
            return result.reason or "执行完成"
        except Exception as e:
            return f"工具执行异常: {e}"

    def _build_child_system_prompt(self, task: DelegationTask) -> str:
        """构建子 agent 的 system prompt（聚焦任务，不含人格）。"""
        return f"""你是一个专项任务助手，被委派来完成以下任务。

## 任务目标
{task.goal}

## 背景信息
{task.context}

## 要求
1. 使用可用工具完成任务
2. 完成后，提供一份清晰的摘要，包含：
   - 做了什么
   - 发现了什么
   - 修改了哪些文件（如果有）
   - 遇到的问题（如果有）
3. 不要试图与用户对话，直接完成任务
4. 如果信息不足以完成任务，说明缺少什么信息"""

    async def cancel_all(self) -> None:
        """中断所有活跃的子 agent。"""
        for task_id, task in self._active_tasks.items():
            task.cancel()
        self._active_tasks.clear()
