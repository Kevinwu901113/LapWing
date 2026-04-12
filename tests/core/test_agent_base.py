"""BaseAgent 单元测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.core.agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentEmit,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
)
from src.core.agent_base import BaseAgent


# ---------- 测试辅助 Agent ----------

class StubAgent(BaseAgent):
    """返回固定结果的 Agent。"""

    def __init__(self, name: str = "stub", result_headline: str = "Done"):
        super().__init__(name, "A stub agent for testing")
        self._result_headline = result_headline

    @property
    def capabilities(self) -> list[str]:
        return ["testing"]

    async def _execute_task(self, command, task_runtime, emit):
        emit(AgentEmitState.DONE, note="Task completed")
        return AgentNotify(
            agent_name=self.name,
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.LATER,
            headline=self._result_headline,
            ref_command_id=command.id,
        )


class SlowAgent(BaseAgent):
    """休眠时间超过 timeout 的 Agent。"""

    def __init__(self):
        super().__init__("slow", "A slow agent for testing")

    @property
    def capabilities(self) -> list[str]:
        return ["slow"]

    async def _execute_task(self, command, task_runtime, emit):
        await asyncio.sleep(10)
        return AgentNotify(
            agent_name=self.name,
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.LATER,
            headline="unreachable",
        )


class ErrorAgent(BaseAgent):
    """抛出 RuntimeError 的 Agent。"""

    def __init__(self):
        super().__init__("error", "An error agent for testing")

    @property
    def capabilities(self) -> list[str]:
        return ["error"]

    async def _execute_task(self, command, task_runtime, emit):
        raise RuntimeError("Something broke")


# ---------- 测试辅助函数 ----------

def make_command(timeout_seconds: float = 300.0) -> AgentCommand:
    return AgentCommand(
        target_agent="stub",
        intent=AgentCommandIntent.EXECUTE,
        task_description="Test task description",
        timeout_seconds=timeout_seconds,
    )


async def collect(agent: BaseAgent, command: AgentCommand) -> list[AgentEmit | AgentNotify]:
    """收集 execute() 生成的所有事件。"""
    task_runtime = MagicMock()
    results = []
    async for item in agent.execute(command, task_runtime):
        results.append(item)
    return results


# ---------- 测试用例 ----------

async def test_yields_queued_then_working():
    """前两个事件必须是 QUEUED 和 WORKING 的 AgentEmit。"""
    agent = StubAgent()
    command = make_command()
    events = await collect(agent, command)

    assert len(events) >= 2
    assert isinstance(events[0], AgentEmit)
    assert events[0].state == AgentEmitState.QUEUED
    assert isinstance(events[1], AgentEmit)
    assert events[1].state == AgentEmitState.WORKING


async def test_yields_intermediate_emits_and_final_notify():
    """DONE emit（来自回调）和最终的 RESULT notify 都应该出现。"""
    agent = StubAgent(result_headline="All done")
    command = make_command()
    events = await collect(agent, command)

    emit_states = [e.state for e in events if isinstance(e, AgentEmit)]
    assert AgentEmitState.DONE in emit_states

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    notify = notifies[0]
    assert notify.kind == AgentNotifyKind.RESULT
    assert notify.headline == "All done"
    assert notify.ref_command_id == command.id


async def test_timeout_yields_failed_and_error_notify():
    """SlowAgent 超时后应产生 FAILED emit 和 ERROR notify。"""
    agent = SlowAgent()
    command = make_command(timeout_seconds=0.1)
    events = await collect(agent, command)

    emit_states = [e.state for e in events if isinstance(e, AgentEmit)]
    assert AgentEmitState.FAILED in emit_states

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    assert notifies[0].kind == AgentNotifyKind.ERROR
    assert notifies[0].ref_command_id == command.id


async def test_exception_yields_failed_and_error_notify():
    """ErrorAgent 抛出异常后应产生 FAILED emit 和 ERROR notify。"""
    agent = ErrorAgent()
    command = make_command()
    events = await collect(agent, command)

    emit_states = [e.state for e in events if isinstance(e, AgentEmit)]
    assert AgentEmitState.FAILED in emit_states

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    assert notifies[0].kind == AgentNotifyKind.ERROR
    assert "Something broke" in (notifies[0].detail or "")
    assert notifies[0].ref_command_id == command.id


async def test_cancel_flag():
    """调用 cancel() 后 is_cancel_requested 应为 True。"""
    agent = StubAgent()
    assert agent.is_cancel_requested is False
    await agent.cancel()
    assert agent.is_cancel_requested is True
