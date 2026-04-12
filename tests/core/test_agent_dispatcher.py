"""AgentDispatcher 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.agent_dispatcher import AgentDispatcher
from src.core.agent_protocol import (
    AgentEmit,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
)
from src.core.agent_registry import AgentCapability, AgentRegistry
from tests.core.test_agent_base import StubAgent


# ---------- 辅助函数 ----------

def _make_registry_with_agent(agent_name: str = "researcher", headline: str = "Found results") -> AgentRegistry:
    registry = AgentRegistry()
    agent = StubAgent(agent_name, headline)
    registry.register(agent, [AgentCapability(name="research", description="Search tasks", tools_required=[])])
    return registry


def _make_dispatcher(**overrides) -> AgentDispatcher:
    registry = overrides.get("registry", AgentRegistry())
    task_runtime = overrides.get("task_runtime", MagicMock())
    return AgentDispatcher(
        registry=registry,
        task_runtime=task_runtime,
        on_progress=overrides.get("on_progress"),
        on_result=overrides.get("on_result"),
    )


# ---------- 测试用例 ----------

async def test_dispatch_to_named_agent():
    """通过 target_agent 指定 Agent，应返回 RESULT notify。"""
    registry = _make_registry_with_agent("researcher", "Found 3 papers")
    dispatcher = _make_dispatcher(registry=registry)

    result = await dispatcher.dispatch(
        task_description="Find recent papers",
        target_agent="researcher",
    )

    assert result is not None
    assert isinstance(result, AgentNotify)
    assert result.kind == AgentNotifyKind.RESULT
    assert result.headline == "Found 3 papers"


async def test_dispatch_auto_selects_agent():
    """不指定 target_agent，应自动选择合适的 Agent，返回 RESULT notify。"""
    registry = _make_registry_with_agent("researcher", "Auto result")
    dispatcher = _make_dispatcher(registry=registry)

    result = await dispatcher.dispatch(task_description="Some task")

    assert result is not None
    assert isinstance(result, AgentNotify)
    assert result.kind == AgentNotifyKind.RESULT


async def test_dispatch_unknown_agent_returns_error():
    """指定不存在的 Agent，应返回 ERROR notify 并包含 'not found'。"""
    dispatcher = _make_dispatcher()

    result = await dispatcher.dispatch(
        task_description="Something",
        target_agent="nonexistent",
    )

    assert result is not None
    assert result.kind == AgentNotifyKind.ERROR
    assert "not found" in result.headline.lower()


async def test_dispatch_no_available_agent_returns_error():
    """空注册表中 dispatch，应返回 ERROR notify。"""
    dispatcher = _make_dispatcher(registry=AgentRegistry())

    result = await dispatcher.dispatch(task_description="Something")

    assert result is not None
    assert result.kind == AgentNotifyKind.ERROR


async def test_dispatch_resets_status_to_idle():
    """dispatch 完成后，Agent 状态应恢复为 'idle'。"""
    registry = _make_registry_with_agent("researcher")
    dispatcher = _make_dispatcher(registry=registry)

    await dispatcher.dispatch(task_description="Task", target_agent="researcher")

    reg = registry.get("researcher")
    assert reg is not None
    assert reg.status == "idle"


async def test_dispatch_calls_progress_callback():
    """提供 on_progress 回调和 chat_id 时，AgentEmit 事件应触发回调。"""
    registry = _make_registry_with_agent("researcher")
    received_emits: list[AgentEmit] = []

    async def on_progress(chat_id: str, emit: AgentEmit) -> None:
        received_emits.append(emit)

    dispatcher = _make_dispatcher(registry=registry, on_progress=on_progress)

    await dispatcher.dispatch(
        task_description="Task",
        target_agent="researcher",
        chat_id="chat_001",
    )

    assert len(received_emits) > 0
    assert all(isinstance(e, AgentEmit) for e in received_emits)


async def test_dispatch_calls_result_callback():
    """提供 on_result 回调和 chat_id 时，AgentNotify 应触发回调。"""
    registry = _make_registry_with_agent("researcher", "Final answer")
    received_notifies: list[AgentNotify] = []

    async def on_result(chat_id: str, notify: AgentNotify) -> None:
        received_notifies.append(notify)

    dispatcher = _make_dispatcher(registry=registry, on_result=on_result)

    await dispatcher.dispatch(
        task_description="Task",
        target_agent="researcher",
        chat_id="chat_001",
    )

    assert len(received_notifies) == 1
    assert received_notifies[0].kind == AgentNotifyKind.RESULT
    assert received_notifies[0].headline == "Final answer"


async def test_cancel_nonexistent_returns_false():
    """取消不存在的 Agent 应返回 False。"""
    dispatcher = _make_dispatcher()
    result = await dispatcher.cancel_agent("nonexistent")
    assert result is False


async def test_cancel_existing_agent():
    """注册并设置 busy 后 cancel，状态应回到 idle，返回 True。"""
    registry = _make_registry_with_agent("researcher")
    registry.set_status("researcher", "busy", "cmd_123")
    dispatcher = _make_dispatcher(registry=registry)

    result = await dispatcher.cancel_agent("researcher")

    assert result is True
    reg = registry.get("researcher")
    assert reg is not None
    assert reg.status == "idle"


async def test_get_active_tasks_empty():
    """空注册表的 get_active_tasks 应返回空列表。"""
    dispatcher = _make_dispatcher()
    assert dispatcher.get_active_tasks() == []


async def test_get_active_tasks_with_busy_agents():
    """有 busy Agent 时，get_active_tasks 应返回其信息。"""
    registry = _make_registry_with_agent("researcher")
    registry.set_status("researcher", "busy", "cmd_abc")
    dispatcher = _make_dispatcher(registry=registry)

    tasks = dispatcher.get_active_tasks()

    assert len(tasks) == 1
    assert tasks[0]["agent_name"] == "researcher"
    assert tasks[0]["command_id"] == "cmd_abc"
    assert tasks[0]["status"] == "busy"
