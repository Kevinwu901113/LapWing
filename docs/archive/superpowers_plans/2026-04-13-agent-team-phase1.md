# Agent Team Phase 1: Protocol + Registry + BaseAgent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Agent Team infrastructure: communication protocol, agent registry, base agent class, dispatcher, and Brain integration. After this, Lapwing can dispatch tasks to registered sub-agents.

**Architecture:** New agent system sits alongside existing `DelegationManager` (`src/core/delegation.py`). Gated by `AGENT_TEAM_ENABLED` flag. When enabled, `delegate_task` tool routes through `AgentDispatcher` → `AgentRegistry` → `BaseAgent` subclasses. Existing delegation path preserved as fallback.

**Tech Stack:** Python 3.12, dataclasses, asyncio, pytest + pytest-asyncio (asyncio_mode=auto)

---

### Task 1: Agent Communication Protocol

**Files:**
- Create: `src/core/agent_protocol.py`
- Create: `tests/core/test_agent_protocol.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_agent_protocol.py
"""Agent communication protocol unit tests."""

from __future__ import annotations

from src.core.agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentCommandPriority,
    AgentEmit,
    AgentEmitState,
    AgentGuidance,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
    GuidanceOption,
)


class TestAgentNotify:
    def test_creation_with_defaults(self):
        n = AgentNotify(
            agent_name="researcher",
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.SOON,
            headline="Done",
        )
        assert n.agent_name == "researcher"
        assert n.kind == AgentNotifyKind.RESULT
        assert n.id  # auto-generated
        assert n.detail is None
        assert n.payload is None
        assert n.ref_command_id is None
        assert n.created_at > 0

    def test_creation_with_all_fields(self):
        n = AgentNotify(
            agent_name="browser",
            kind=AgentNotifyKind.ERROR,
            urgency=AgentUrgency.IMMEDIATE,
            headline="Failed",
            detail="Connection refused",
            payload={"url": "https://example.com"},
            ref_command_id="cmd-1",
        )
        assert n.detail == "Connection refused"
        assert n.payload["url"] == "https://example.com"
        assert n.ref_command_id == "cmd-1"


class TestAgentCommand:
    def test_defaults(self):
        c = AgentCommand(
            target_agent="researcher",
            intent=AgentCommandIntent.EXECUTE,
            task_description="Search for papers",
        )
        assert c.priority == AgentCommandPriority.NORMAL
        assert c.interrupt is False
        assert c.guidance is None
        assert c.context is None
        assert c.max_steps == 20
        assert c.timeout_seconds == 300
        assert c.id  # auto-generated
        assert c.created_at > 0

    def test_with_guidance(self):
        g = AgentGuidance(
            options=[
                GuidanceOption(label="Option A", steps=["step1"], rationale="fast", risk="low"),
                GuidanceOption(label="Option B", steps=["step1", "step2"], risk="high"),
            ],
            persona_hints={"thoroughness": "high"},
        )
        c = AgentCommand(
            target_agent="coder",
            intent=AgentCommandIntent.EXECUTE,
            task_description="Fix bug",
            guidance=g,
        )
        assert len(c.guidance.options) == 2
        assert c.guidance.options[0].rationale == "fast"
        assert c.guidance.options[1].rationale is None
        assert c.guidance.persona_hints["thoroughness"] == "high"


class TestAgentEmit:
    def test_creation(self):
        e = AgentEmit(
            agent_name="coder",
            ref_id="cmd-1",
            state=AgentEmitState.WORKING,
        )
        assert e.progress is None
        assert e.note is None
        assert e.id
        assert e.created_at > 0

    def test_with_progress(self):
        e = AgentEmit(
            agent_name="coder",
            ref_id="cmd-1",
            state=AgentEmitState.WORKING,
            progress=0.5,
            note="Halfway done",
        )
        assert e.progress == 0.5
        assert e.note == "Halfway done"


class TestEnumValues:
    def test_urgency_values(self):
        assert AgentUrgency.IMMEDIATE == "immediate"
        assert AgentUrgency.SOON == "soon"
        assert AgentUrgency.LATER == "later"

    def test_emit_state_values(self):
        assert AgentEmitState.QUEUED == "queued"
        assert AgentEmitState.WORKING == "working"
        assert AgentEmitState.DONE == "done"
        assert AgentEmitState.FAILED == "failed"
        assert AgentEmitState.BLOCKED == "blocked"
        assert AgentEmitState.CANCELLED == "cancelled"

    def test_command_intent_values(self):
        assert AgentCommandIntent.EXECUTE == "execute"
        assert AgentCommandIntent.CANCEL == "cancel"

    def test_notify_kind_values(self):
        assert AgentNotifyKind.RESULT == "result"
        assert AgentNotifyKind.ERROR == "error"
        assert AgentNotifyKind.QUESTION == "question"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_agent_protocol.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.agent_protocol'`

- [ ] **Step 3: Write the implementation**

```python
# src/core/agent_protocol.py
"""Agent 间通信协议数据类型。

三种消息语义：
- AgentNotify: 子 Agent → Brain（报告结果/进度/错误）
- AgentCommand: Brain → 子 Agent（下达指令）
- AgentEmit: 双向状态更新
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AgentUrgency(str, Enum):
    IMMEDIATE = "immediate"
    SOON = "soon"
    LATER = "later"


class AgentNotifyKind(str, Enum):
    RESULT = "result"
    PROGRESS = "progress"
    ERROR = "error"
    QUESTION = "question"


class AgentCommandIntent(str, Enum):
    EXECUTE = "execute"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    CONTEXT = "context"


class AgentCommandPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class AgentEmitState(str, Enum):
    QUEUED = "queued"
    WORKING = "working"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class GuidanceOption:
    """单个方案选项。"""
    label: str
    steps: list[str]
    rationale: str | None = None
    risk: str = "low"


@dataclass
class AgentGuidance:
    """指令附带的多方案指引。"""
    options: list[GuidanceOption]
    persona_hints: dict[str, str] | None = None


@dataclass
class AgentNotify:
    """子 Agent -> Brain：报告事件。"""
    agent_name: str
    kind: AgentNotifyKind
    urgency: AgentUrgency
    headline: str
    detail: str | None = None
    payload: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    ref_command_id: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class AgentCommand:
    """Brain -> 子 Agent：下达指令。"""
    target_agent: str
    intent: AgentCommandIntent
    task_description: str
    priority: AgentCommandPriority = AgentCommandPriority.NORMAL
    interrupt: bool = False
    guidance: AgentGuidance | None = None
    context: dict[str, Any] | None = None
    max_steps: int = 20
    timeout_seconds: float = 300
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)


@dataclass
class AgentEmit:
    """双向状态更新。"""
    agent_name: str
    ref_id: str
    state: AgentEmitState
    progress: float | None = None
    note: str | None = None
    payload: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)


# 类型别名
EmitCallback = Callable[[AgentEmitState, str | None, float | None, dict | None], None]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_agent_protocol.py -x -q`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/agent_protocol.py tests/core/test_agent_protocol.py
git commit -m "feat: add agent communication protocol data types"
```

---

### Task 2: Agent Registry

**Files:**
- Create: `src/core/agent_registry.py`
- Create: `tests/core/test_agent_registry.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_agent_registry.py
"""AgentRegistry unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.agent_registry import AgentCapability, AgentRegistration, AgentRegistry


def _mock_agent(name: str, capabilities: list[str] | None = None) -> MagicMock:
    """Create a mock BaseAgent."""
    agent = MagicMock()
    agent.name = name
    agent.capabilities = capabilities or []
    return agent


class TestAgentRegistryRegister:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = _mock_agent("researcher")
        caps = [AgentCapability(name="web_search", description="Search", tools_required=["web_search"])]
        reg.register(agent, caps)

        result = reg.get("researcher")
        assert result is not None
        assert result.agent.name == "researcher"
        assert result.status == "idle"

    def test_register_replaces_existing(self):
        reg = AgentRegistry()
        agent1 = _mock_agent("researcher")
        agent2 = _mock_agent("researcher")
        caps = [AgentCapability(name="search", description="S", tools_required=[])]
        reg.register(agent1, caps)
        reg.register(agent2, caps)
        assert reg.get("researcher").agent is agent2

    def test_get_nonexistent_returns_none(self):
        reg = AgentRegistry()
        assert reg.get("ghost") is None


class TestAgentRegistryUnregister:
    def test_unregister_existing(self):
        reg = AgentRegistry()
        agent = _mock_agent("coder")
        reg.register(agent, [])
        reg.unregister("coder")
        assert reg.get("coder") is None

    def test_unregister_nonexistent_no_error(self):
        reg = AgentRegistry()
        reg.unregister("ghost")  # should not raise


class TestAgentRegistryFind:
    def test_find_by_capability(self):
        reg = AgentRegistry()
        a1 = _mock_agent("researcher")
        a2 = _mock_agent("coder")
        reg.register(a1, [AgentCapability("web_search", "Search", ["web_search"])])
        reg.register(a2, [AgentCapability("code_execution", "Code", ["execute_shell"])])

        results = reg.find_by_capability("web_search")
        assert len(results) == 1
        assert results[0].agent.name == "researcher"

    def test_find_by_capability_skips_disabled(self):
        reg = AgentRegistry()
        agent = _mock_agent("researcher")
        reg.register(agent, [AgentCapability("web_search", "Search", ["web_search"])])
        reg.set_status("researcher", "disabled")

        results = reg.find_by_capability("web_search")
        assert len(results) == 0

    def test_find_best_prefers_idle(self):
        reg = AgentRegistry()
        a1 = _mock_agent("agent1")
        a2 = _mock_agent("agent2")
        caps = [AgentCapability("general", "G", [])]
        reg.register(a1, caps)
        reg.register(a2, caps)
        reg.set_status("agent1", "busy", "cmd-1")

        best = reg.find_best_for_task("some task")
        assert best is not None
        assert best.agent.name == "agent2"

    def test_find_best_skips_disabled_and_error(self):
        reg = AgentRegistry()
        a1 = _mock_agent("agent1")
        a2 = _mock_agent("agent2")
        reg.register(a1, [])
        reg.register(a2, [])
        reg.set_status("agent1", "disabled")
        reg.set_status("agent2", "error")

        assert reg.find_best_for_task("task") is None

    def test_find_best_filters_by_required_tools(self):
        reg = AgentRegistry()
        a1 = _mock_agent("researcher")
        a2 = _mock_agent("coder")
        reg.register(a1, [AgentCapability("search", "S", ["web_search"])])
        reg.register(a2, [AgentCapability("code", "C", ["execute_shell", "write_file"])])

        best = reg.find_best_for_task("write code", required_tools=["execute_shell"])
        assert best is not None
        assert best.agent.name == "coder"

    def test_find_best_returns_none_when_no_match(self):
        reg = AgentRegistry()
        a1 = _mock_agent("researcher")
        reg.register(a1, [AgentCapability("search", "S", ["web_search"])])

        assert reg.find_best_for_task("task", required_tools=["execute_shell"]) is None


class TestAgentRegistryStatus:
    def test_set_status(self):
        reg = AgentRegistry()
        agent = _mock_agent("coder")
        reg.register(agent, [])
        reg.set_status("coder", "busy", "cmd-1")

        result = reg.get("coder")
        assert result.status == "busy"
        assert result.current_command_id == "cmd-1"

    def test_set_status_nonexistent_no_error(self):
        reg = AgentRegistry()
        reg.set_status("ghost", "busy")  # should not raise


class TestAgentRegistryList:
    def test_list_agents(self):
        reg = AgentRegistry()
        a1 = _mock_agent("researcher")
        a2 = _mock_agent("coder")
        reg.register(a1, [AgentCapability("search", "S", ["web_search"])])
        reg.register(a2, [AgentCapability("code", "C", ["shell"])])

        agents = reg.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"researcher", "coder"}

    def test_available_count(self):
        reg = AgentRegistry()
        a1 = _mock_agent("a1")
        a2 = _mock_agent("a2")
        a3 = _mock_agent("a3")
        reg.register(a1, [])
        reg.register(a2, [])
        reg.register(a3, [])
        reg.set_status("a2", "disabled")
        reg.set_status("a3", "error")

        assert reg.available_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_agent_registry.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.agent_registry'`

- [ ] **Step 3: Write the implementation**

```python
# src/core/agent_registry.py
"""Agent 注册表：管理可用 Agent 及其能力。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_base import BaseAgent

logger = logging.getLogger("lapwing.agent_registry")


@dataclass
class AgentCapability:
    """Agent 能力声明。"""
    name: str
    description: str
    tools_required: list[str]


@dataclass
class AgentRegistration:
    """Agent 注册信息。"""
    agent: BaseAgent
    capabilities: list[AgentCapability]
    status: str = "idle"
    current_command_id: str | None = None
    error_count: int = 0
    max_consecutive_errors: int = 3


class AgentRegistry:
    """Agent 注册表。"""

    def __init__(self):
        self._agents: dict[str, AgentRegistration] = {}

    def register(self, agent: BaseAgent, capabilities: list[AgentCapability]) -> None:
        if agent.name in self._agents:
            logger.warning("Agent '%s' already registered, replacing", agent.name)
        self._agents[agent.name] = AgentRegistration(agent=agent, capabilities=capabilities)
        logger.info("Registered agent '%s' with %d capabilities", agent.name, len(capabilities))

    def unregister(self, name: str) -> None:
        if name in self._agents:
            del self._agents[name]
            logger.info("Unregistered agent '%s'", name)

    def get(self, name: str) -> AgentRegistration | None:
        return self._agents.get(name)

    def find_by_capability(self, capability_name: str) -> list[AgentRegistration]:
        results = []
        for reg in self._agents.values():
            if reg.status == "disabled":
                continue
            for cap in reg.capabilities:
                if cap.name == capability_name:
                    results.append(reg)
                    break
        return results

    def find_best_for_task(
        self, task_description: str, required_tools: list[str] | None = None,
    ) -> AgentRegistration | None:
        candidates = []
        for reg in self._agents.values():
            if reg.status in ("disabled", "error"):
                continue
            if required_tools:
                agent_tools = set()
                for cap in reg.capabilities:
                    agent_tools.update(cap.tools_required)
                if not set(required_tools).issubset(agent_tools):
                    continue
            candidates.append(reg)

        if not candidates:
            return None

        idle = [c for c in candidates if c.status == "idle"]
        return idle[0] if idle else candidates[0]

    def set_status(self, name: str, status: str, command_id: str | None = None) -> None:
        reg = self._agents.get(name)
        if reg:
            reg.status = status
            reg.current_command_id = command_id

    def list_agents(self) -> list[dict]:
        result = []
        for name, reg in self._agents.items():
            result.append({
                "name": name,
                "status": reg.status,
                "capabilities": [c.name for c in reg.capabilities],
                "current_command_id": reg.current_command_id,
            })
        return result

    @property
    def available_count(self) -> int:
        return sum(1 for r in self._agents.values() if r.status not in ("disabled", "error"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_agent_registry.py -x -q`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/agent_registry.py tests/core/test_agent_registry.py
git commit -m "feat: add agent registry with capability-based lookup"
```

---

### Task 3: Base Agent Class

**Files:**
- Create: `src/core/agent_base.py`
- Create: `tests/core/test_agent_base.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_agent_base.py
"""BaseAgent unit tests."""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import MagicMock

from src.core.agent_base import BaseAgent
from src.core.agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentEmit,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
    EmitCallback,
)


class StubAgent(BaseAgent):
    """Test agent that returns a fixed result."""

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
    """Agent that sleeps longer than timeout."""

    @property
    def capabilities(self) -> list[str]:
        return ["slow"]

    async def _execute_task(self, command, task_runtime, emit):
        await asyncio.sleep(10)  # will be timed out
        return AgentNotify(
            agent_name=self.name,
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.LATER,
            headline="Should not reach here",
        )


class ErrorAgent(BaseAgent):
    """Agent that raises an exception."""

    @property
    def capabilities(self) -> list[str]:
        return ["error"]

    async def _execute_task(self, command, task_runtime, emit):
        raise RuntimeError("Something broke")


class TestBaseAgentExecute:
    @pytest.mark.asyncio
    async def test_yields_queued_then_working(self):
        agent = StubAgent()
        command = AgentCommand(
            target_agent="stub",
            intent=AgentCommandIntent.EXECUTE,
            task_description="Test task",
        )
        events = []
        async for event in agent.execute(command, MagicMock()):
            events.append(event)

        # First two events should be QUEUED and WORKING emits
        assert isinstance(events[0], AgentEmit)
        assert events[0].state == AgentEmitState.QUEUED
        assert isinstance(events[1], AgentEmit)
        assert events[1].state == AgentEmitState.WORKING

    @pytest.mark.asyncio
    async def test_yields_intermediate_emits_and_final_notify(self):
        agent = StubAgent(result_headline="Search complete")
        command = AgentCommand(
            target_agent="stub",
            intent=AgentCommandIntent.EXECUTE,
            task_description="Search papers",
        )
        events = []
        async for event in agent.execute(command, MagicMock()):
            events.append(event)

        # Should have: QUEUED, WORKING, DONE emit (from callback), final NOTIFY
        emits = [e for e in events if isinstance(e, AgentEmit)]
        notifies = [e for e in events if isinstance(e, AgentNotify)]

        assert any(e.state == AgentEmitState.DONE for e in emits)
        assert len(notifies) == 1
        assert notifies[0].headline == "Search complete"
        assert notifies[0].kind == AgentNotifyKind.RESULT

    @pytest.mark.asyncio
    async def test_timeout_yields_failed_and_error_notify(self):
        agent = SlowAgent("slow", "A slow agent")
        command = AgentCommand(
            target_agent="slow",
            intent=AgentCommandIntent.EXECUTE,
            task_description="Slow task",
            timeout_seconds=0.1,  # very short timeout
        )
        events = []
        async for event in agent.execute(command, MagicMock()):
            events.append(event)

        emits = [e for e in events if isinstance(e, AgentEmit)]
        notifies = [e for e in events if isinstance(e, AgentNotify)]

        assert any(e.state == AgentEmitState.FAILED for e in emits)
        assert len(notifies) == 1
        assert notifies[0].kind == AgentNotifyKind.ERROR
        assert "超时" in notifies[0].headline

    @pytest.mark.asyncio
    async def test_exception_yields_failed_and_error_notify(self):
        agent = ErrorAgent("error", "An error agent")
        command = AgentCommand(
            target_agent="error",
            intent=AgentCommandIntent.EXECUTE,
            task_description="Fail task",
        )
        events = []
        async for event in agent.execute(command, MagicMock()):
            events.append(event)

        emits = [e for e in events if isinstance(e, AgentEmit)]
        notifies = [e for e in events if isinstance(e, AgentNotify)]

        assert any(e.state == AgentEmitState.FAILED for e in emits)
        assert len(notifies) == 1
        assert notifies[0].kind == AgentNotifyKind.ERROR


class TestBaseAgentCancel:
    @pytest.mark.asyncio
    async def test_cancel_flag(self):
        agent = StubAgent()
        assert not agent.is_cancel_requested
        await agent.cancel()
        assert agent.is_cancel_requested
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_agent_base.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.agent_base'`

- [ ] **Step 3: Write the implementation**

```python
# src/core/agent_base.py
"""Agent 基类：所有子 Agent 继承此类。

每个 Agent 是 Brain 内的协程（不是独立进程），
通过 AgentCommand 接收指令，通过 yield AgentEmit 报告状态，
完成后发送 AgentNotify。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncGenerator

from .agent_protocol import (
    AgentCommand,
    AgentEmit,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
    EmitCallback,
)

if TYPE_CHECKING:
    from .task_runtime import TaskRuntime

logger = logging.getLogger("lapwing.agent_base")


class BaseAgent(ABC):
    """子 Agent 基类。"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._cancel_requested = False

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        ...

    @abstractmethod
    async def _execute_task(
        self,
        command: AgentCommand,
        task_runtime: TaskRuntime,
        emit: EmitCallback,
    ) -> AgentNotify:
        ...

    async def execute(
        self,
        command: AgentCommand,
        task_runtime: TaskRuntime,
    ) -> AsyncGenerator[AgentEmit | AgentNotify, None]:
        """执行指令，yield 状态更新和最终结果。"""
        self._cancel_requested = False
        emits: list[AgentEmit] = []

        def emit_callback(
            state: AgentEmitState,
            note: str | None = None,
            progress: float | None = None,
            payload: dict | None = None,
        ):
            emits.append(AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=state,
                progress=progress,
                note=note,
                payload=payload,
            ))

        yield AgentEmit(
            agent_name=self.name,
            ref_id=command.id,
            state=AgentEmitState.QUEUED,
            note=f"Task received: {command.task_description[:100]}",
        )

        yield AgentEmit(
            agent_name=self.name,
            ref_id=command.id,
            state=AgentEmitState.WORKING,
        )

        try:
            notify = await asyncio.wait_for(
                self._execute_task(command, task_runtime, emit_callback),
                timeout=command.timeout_seconds,
            )

            for e in emits:
                yield e

            yield notify

        except asyncio.TimeoutError:
            yield AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=AgentEmitState.FAILED,
                note=f"Task timed out after {command.timeout_seconds}s",
            )
            yield AgentNotify(
                agent_name=self.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline=f"任务超时：{command.task_description[:50]}",
                detail=f"超过 {command.timeout_seconds} 秒未完成",
                ref_command_id=command.id,
            )

        except asyncio.CancelledError:
            yield AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=AgentEmitState.CANCELLED,
                note="Task cancelled",
            )

        except Exception as e:
            logger.exception("Agent '%s' task failed", self.name)
            yield AgentEmit(
                agent_name=self.name,
                ref_id=command.id,
                state=AgentEmitState.FAILED,
                note=str(e),
            )
            yield AgentNotify(
                agent_name=self.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline=f"任务失败：{command.task_description[:50]}",
                detail=str(e),
                ref_command_id=command.id,
            )

        finally:
            pass

    async def cancel(self) -> None:
        """请求取消。设置标志供 _execute_task 轮询检查。"""
        self._cancel_requested = True

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel_requested
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_agent_base.py -x -q`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/agent_base.py tests/core/test_agent_base.py
git commit -m "feat: add BaseAgent abstract class with async generator execution"
```

---

### Task 4: Agent Dispatcher

**Files:**
- Create: `src/core/agent_dispatcher.py`
- Create: `tests/core/test_agent_dispatcher.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_agent_dispatcher.py
"""AgentDispatcher unit tests."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.agent_dispatcher import AgentDispatcher
from src.core.agent_protocol import (
    AgentCommandPriority,
    AgentEmit,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
)
from src.core.agent_registry import AgentCapability, AgentRegistry


# Reuse StubAgent from test_agent_base
from tests.core.test_agent_base import StubAgent


def _make_dispatcher(**overrides) -> AgentDispatcher:
    registry = overrides.get("registry", AgentRegistry())
    task_runtime = overrides.get("task_runtime", MagicMock())
    return AgentDispatcher(
        registry=registry,
        task_runtime=task_runtime,
        on_progress=overrides.get("on_progress"),
        on_result=overrides.get("on_result"),
    )


class TestDispatcherDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_to_named_agent(self):
        registry = AgentRegistry()
        agent = StubAgent(name="researcher", result_headline="Found 3 papers")
        registry.register(agent, [AgentCapability("search", "S", ["web_search"])])

        dispatcher = _make_dispatcher(registry=registry)
        notify = await dispatcher.dispatch(
            task_description="Search for papers",
            target_agent="researcher",
        )

        assert notify is not None
        assert notify.kind == AgentNotifyKind.RESULT
        assert notify.headline == "Found 3 papers"

    @pytest.mark.asyncio
    async def test_dispatch_auto_selects_agent(self):
        registry = AgentRegistry()
        agent = StubAgent(name="general", result_headline="Done")
        registry.register(agent, [AgentCapability("general", "G", [])])

        dispatcher = _make_dispatcher(registry=registry)
        notify = await dispatcher.dispatch(task_description="Do something")

        assert notify is not None
        assert notify.kind == AgentNotifyKind.RESULT

    @pytest.mark.asyncio
    async def test_dispatch_unknown_agent_returns_error(self):
        dispatcher = _make_dispatcher()
        notify = await dispatcher.dispatch(
            task_description="Task",
            target_agent="nonexistent",
        )

        assert notify is not None
        assert notify.kind == AgentNotifyKind.ERROR
        assert "not found" in notify.headline

    @pytest.mark.asyncio
    async def test_dispatch_no_available_agent_returns_error(self):
        dispatcher = _make_dispatcher()
        notify = await dispatcher.dispatch(task_description="Task")

        assert notify is not None
        assert notify.kind == AgentNotifyKind.ERROR

    @pytest.mark.asyncio
    async def test_dispatch_resets_status_to_idle(self):
        registry = AgentRegistry()
        agent = StubAgent(name="worker", result_headline="Ok")
        registry.register(agent, [])

        dispatcher = _make_dispatcher(registry=registry)
        await dispatcher.dispatch(task_description="Task", target_agent="worker")

        assert registry.get("worker").status == "idle"

    @pytest.mark.asyncio
    async def test_dispatch_calls_progress_callback(self):
        registry = AgentRegistry()
        agent = StubAgent(name="worker", result_headline="Ok")
        registry.register(agent, [])

        progress_events = []

        async def on_progress(chat_id, emit):
            progress_events.append(emit)

        dispatcher = _make_dispatcher(registry=registry, on_progress=on_progress)
        await dispatcher.dispatch(
            task_description="Task",
            target_agent="worker",
            chat_id="chat-1",
        )

        assert len(progress_events) > 0
        assert all(isinstance(e, AgentEmit) for e in progress_events)

    @pytest.mark.asyncio
    async def test_dispatch_calls_result_callback(self):
        registry = AgentRegistry()
        agent = StubAgent(name="worker", result_headline="Ok")
        registry.register(agent, [])

        result_events = []

        async def on_result(chat_id, notify):
            result_events.append(notify)

        dispatcher = _make_dispatcher(registry=registry, on_result=on_result)
        await dispatcher.dispatch(
            task_description="Task",
            target_agent="worker",
            chat_id="chat-1",
        )

        assert len(result_events) == 1
        assert result_events[0].kind == AgentNotifyKind.RESULT


class TestDispatcherCancel:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self):
        dispatcher = _make_dispatcher()
        assert await dispatcher.cancel_agent("ghost") is False

    @pytest.mark.asyncio
    async def test_cancel_existing_agent(self):
        registry = AgentRegistry()
        agent = StubAgent(name="worker")
        registry.register(agent, [])
        registry.set_status("worker", "busy", "cmd-1")

        dispatcher = _make_dispatcher(registry=registry)
        result = await dispatcher.cancel_agent("worker")

        assert result is True
        assert registry.get("worker").status == "idle"


class TestDispatcherActiveTasks:
    def test_get_active_tasks_empty(self):
        dispatcher = _make_dispatcher()
        assert dispatcher.get_active_tasks() == []

    def test_get_active_tasks_with_busy_agents(self):
        registry = AgentRegistry()
        agent = StubAgent(name="worker")
        registry.register(agent, [])
        registry.set_status("worker", "busy", "cmd-1")

        dispatcher = _make_dispatcher(registry=registry)
        tasks = dispatcher.get_active_tasks()
        assert len(tasks) == 1
        assert tasks[0]["agent_name"] == "worker"
        assert tasks[0]["command_id"] == "cmd-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_agent_dispatcher.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.agent_dispatcher'`

- [ ] **Step 3: Write the implementation**

```python
# src/core/agent_dispatcher.py
"""Agent 调度器：Brain 和 Agent 之间的桥梁。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from .agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentCommandPriority,
    AgentEmit,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
)

if TYPE_CHECKING:
    from .agent_registry import AgentRegistry
    from .task_runtime import TaskRuntime

logger = logging.getLogger("lapwing.agent_dispatcher")


class AgentDispatcher:
    """Agent 调度器。

    职责：接收 Brain 的委派请求，选择 Agent，执行，收集结果。
    """

    def __init__(
        self,
        registry: AgentRegistry,
        task_runtime: TaskRuntime,
        on_progress: Callable[[str, AgentEmit], Awaitable[None]] | None = None,
        on_result: Callable[[str, AgentNotify], Awaitable[None]] | None = None,
    ):
        self.registry = registry
        self.task_runtime = task_runtime
        self._on_progress = on_progress
        self._on_result = on_result

    async def dispatch(
        self,
        task_description: str,
        target_agent: str | None = None,
        priority: AgentCommandPriority = AgentCommandPriority.NORMAL,
        context: dict | None = None,
        chat_id: str | None = None,
        max_steps: int = 20,
        timeout: float = 300,
    ) -> AgentNotify | None:
        # 选择 Agent
        if target_agent:
            reg = self.registry.get(target_agent)
            if not reg:
                return AgentNotify(
                    agent_name=target_agent,
                    kind=AgentNotifyKind.ERROR,
                    urgency=AgentUrgency.SOON,
                    headline=f"Agent '{target_agent}' not found",
                )
        else:
            reg = self.registry.find_best_for_task(task_description)
            if not reg:
                return AgentNotify(
                    agent_name="dispatcher",
                    kind=AgentNotifyKind.ERROR,
                    urgency=AgentUrgency.SOON,
                    headline="没有找到合适的 Agent 来执行这个任务",
                    detail=f"任务：{task_description}",
                )

        agent = reg.agent

        command = AgentCommand(
            target_agent=agent.name,
            intent=AgentCommandIntent.EXECUTE,
            task_description=task_description,
            priority=priority,
            context=context,
            max_steps=max_steps,
            timeout_seconds=timeout,
        )

        self.registry.set_status(agent.name, "busy", command.id)

        final_notify = None
        try:
            async for event in agent.execute(command, self.task_runtime):
                if isinstance(event, AgentEmit):
                    logger.debug("Agent '%s' emit: %s - %s", agent.name, event.state, event.note)
                    if self._on_progress and chat_id:
                        await self._on_progress(chat_id, event)
                elif isinstance(event, AgentNotify):
                    final_notify = event
                    if self._on_result and chat_id:
                        await self._on_result(chat_id, event)
        except Exception as e:
            logger.exception("Dispatcher error for agent '%s'", agent.name)
            final_notify = AgentNotify(
                agent_name=agent.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline=f"调度失败：{str(e)[:100]}",
                ref_command_id=command.id,
            )
        finally:
            self.registry.set_status(agent.name, "idle")

        return final_notify

    async def cancel_agent(self, agent_name: str) -> bool:
        reg = self.registry.get(agent_name)
        if reg and reg.agent:
            await reg.agent.cancel()
            self.registry.set_status(agent_name, "idle")
            return True
        return False

    def get_active_tasks(self) -> list[dict]:
        result = []
        for name, reg in self.registry._agents.items():
            if reg.status == "busy" and reg.current_command_id:
                result.append({
                    "agent_name": name,
                    "command_id": reg.current_command_id,
                    "status": reg.status,
                })
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_agent_dispatcher.py -x -q`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/agent_dispatcher.py tests/core/test_agent_dispatcher.py
git commit -m "feat: add AgentDispatcher bridging Brain and Agent execution"
```

---

### Task 5: Feature Flag + Brain Attributes

**Files:**
- Modify: `config/settings.py:154` (add flag after DELEGATION_ENABLED)
- Modify: `src/core/brain.py:144` (add agent_registry, agent_dispatcher attributes)

- [ ] **Step 1: Add `AGENT_TEAM_ENABLED` feature flag**

In `config/settings.py`, after line 154 (`DELEGATION_ENABLED`), add:
```python
AGENT_TEAM_ENABLED: bool = os.getenv("AGENT_TEAM_ENABLED", "true").lower() in ("true", "1", "yes")
```

- [ ] **Step 2: Add Brain attributes**

In `src/core/brain.py`, after line 144 (`self.delegation_manager = None`), add:
```python
self.agent_registry = None  # Set externally (AgentRegistry | None)
self.agent_dispatcher = None  # Set externally (AgentDispatcher | None)
```

- [ ] **Step 3: Add agent_dispatcher to services dict**

In `src/core/brain.py`, in `_complete_chat()`, after the `delegation_manager` injection block (line 267), add:
```python
agent_dispatcher = getattr(self, "agent_dispatcher", None)
if agent_dispatcher is not None:
    services["agent_dispatcher"] = agent_dispatcher
```

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `python -m pytest tests/core/test_brain.py -x -q`
Expected: All PASS (no change in behavior)

- [ ] **Step 5: Commit**

```bash
git add config/settings.py src/core/brain.py
git commit -m "feat: add AGENT_TEAM_ENABLED flag and Brain agent attributes"
```

---

### Task 6: Container Wiring + Prompt Builder

**Files:**
- Modify: `src/app/container.py:304-313` (add agent team setup after delegation block)
- Modify: `src/core/prompt_builder.py:232-234` (add agent overview layer)

- [ ] **Step 1: Wire agent team in container**

In `src/app/container.py`, after the delegation block (line 313, after `logger.info("子 Agent 委托系统已就绪")`), add:

```python
# Agent Team 系统（可选，新架构）
from config.settings import AGENT_TEAM_ENABLED
if AGENT_TEAM_ENABLED:
    from src.core.agent_registry import AgentRegistry
    from src.core.agent_dispatcher import AgentDispatcher
    agent_registry = AgentRegistry()
    self.brain.agent_registry = agent_registry
    self.brain.agent_dispatcher = AgentDispatcher(
        registry=agent_registry,
        task_runtime=self.brain.task_runtime,
    )
    logger.info("Agent Team 系统已就绪")
```

- [ ] **Step 2: Add `agent_registry` kwarg to `build_system_prompt`**

The function `build_system_prompt()` at `prompt_builder.py:66` does NOT accept a `brain` parameter. It takes individual kwargs: `system_prompt`, `chat_id`, `user_message`, `memory`, `vector_store`, `knowledge_manager`, `skill_manager`, `memory_index`.

Add `agent_registry` as a new optional kwarg:

In `src/core/prompt_builder.py:75` (after `memory_index`), add parameter:
```python
    agent_registry: "Any | None" = None,
```

Then after the Layer 6.5 SOP block (line 231) and before the Layer 7 capabilities line (line 233), add:

```python
    # Layer 6.8: Agent 团队概览
    if agent_registry is not None:
        agents_info = agent_registry.list_agents()
        if agents_info:
            agent_lines = ["## 你的团队（可用 Agent）", "你可以用 delegate_task 工具将任务委派给它们："]
            for a in agents_info:
                status_icon = "[idle]" if a["status"] == "idle" else "[busy]"
                caps = ", ".join(a["capabilities"]) if a["capabilities"] else "general"
                agent_lines.append(f"- {status_icon} **{a['name']}**: {caps}")
            sections.append("\n".join(agent_lines))
```

- [ ] **Step 2b: Pass `agent_registry` from Brain._build_system_prompt**

In `src/core/brain.py:300-309`, add the new kwarg to the `build_system_prompt()` call:

```python
        return await build_system_prompt(
            system_prompt=self.system_prompt,
            chat_id=chat_id,
            user_message=user_message,
            memory=self.memory,
            vector_store=self.vector_store,
            knowledge_manager=self.knowledge_manager,
            skill_manager=self.skill_manager,
            memory_index=self.memory_index,
            agent_registry=self.agent_registry,
        )
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `python -m pytest tests/ -x -q --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/app/container.py src/core/prompt_builder.py
git commit -m "feat: wire agent team in container and inject agent overview in prompt"
```

---

### Task 7: Refactor delegation_tool.py

**Files:**
- Modify: `src/tools/delegation_tool.py` (add agent team path)
- Create: `tests/core/test_delegation_tool_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_delegation_tool_agent.py
"""Test delegation_tool routing through AgentDispatcher."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

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
        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_old_delegation_when_no_dispatcher(self):
        """When agent_dispatcher is not in services, falls back to delegation_manager."""
        mock_dm = MagicMock()
        mock_dm.delegate = AsyncMock(return_value=[])

        ctx = _make_context(services={"delegation_manager": mock_dm})
        req = ToolExecutionRequest(name="delegate_task", arguments={
            "tasks": [{"goal": "Test", "context": "Ctx"}],
        })

        # Should go through old path (delegation_manager)
        result = await delegate_task_executor(req, ctx)
        # Old path was called (even if no results)
        mock_dm.delegate.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_delegation_tool_agent.py -x -q`
Expected: FAIL (dispatcher path not implemented yet)

- [ ] **Step 3: Update delegation_tool.py**

Modify `src/tools/delegation_tool.py` to check for `agent_dispatcher` in services first. If present and the request has a single task (or an `agent` field), route through the new dispatcher. Otherwise fall back to old `delegation_manager` path.

Restructure the function body as a clear if/else:

```python
async def delegate_task_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """执行委托任务。优先走 AgentDispatcher，回退到 DelegationManager。"""
    raw_tasks = request.arguments.get("tasks", [])
    if not raw_tasks:
        return ToolExecutionResult(
            success=False, payload={"error": "缺少 tasks 参数"}, reason="缺少 tasks 参数",
        )

    # 新 Agent Team 路径（优先）
    agent_dispatcher = context.services.get("agent_dispatcher")
    if agent_dispatcher is not None:
        first_task = raw_tasks[0] if raw_tasks else {}
        goal = str(first_task.get("goal", "")).strip()
        if not goal:
            return ToolExecutionResult(
                success=False, payload={"error": "缺少任务目标"}, reason="缺少任务目标",
            )
        ctx_text = str(first_task.get("context", "")).strip()
        target = first_task.get("agent") or request.arguments.get("agent")
        full_description = f"{goal}\n\n背景：{ctx_text}" if ctx_text else goal

        from src.core.agent_protocol import AgentNotifyKind
        notify = await agent_dispatcher.dispatch(
            task_description=full_description,
            target_agent=target,
            chat_id=context.chat_id,
        )
        if notify and notify.kind == AgentNotifyKind.RESULT:
            return ToolExecutionResult(
                success=True,
                payload={"result": notify.headline, "detail": notify.detail, "data": notify.payload},
            )
        return ToolExecutionResult(
            success=False,
            payload={"error": notify.headline if notify else "Unknown error"},
            reason=notify.detail if notify else "Agent failed",
        )

    # 旧 DelegationManager 路径（回退）
    delegation_manager: DelegationManager | None = context.services.get("delegation_manager")
    if delegation_manager is None:
        return ToolExecutionResult(
            success=False, payload={"error": "委托系统未初始化"}, reason="委托系统未初始化",
        )

    # ... rest of existing delegation_manager code unchanged ...
```

Key point: The `delegation_manager is None` guard is now inside the `else` branch (after the `agent_dispatcher` check returns). This ensures that when `agent_dispatcher` is available, the function returns early through that path and never hits the `delegation_manager` guard.

- [ ] **Step 4: Run all delegation tests**

Run: `python -m pytest tests/core/test_delegation_tool_agent.py tests/core/test_delegation.py -x -q`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/delegation_tool.py tests/core/test_delegation_tool_agent.py
git commit -m "feat: route delegate_task through AgentDispatcher when available"
```

---

### Task 8: Full Regression Test

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: All tests PASS, no regressions

- [ ] **Step 2: Verify feature flag gating**

Verify that setting `AGENT_TEAM_ENABLED=false` does not break anything:
Run: `AGENT_TEAM_ENABLED=false python -m pytest tests/ -x -q`
Expected: All tests PASS (agent team code not loaded when disabled)

- [ ] **Step 3: Final commit if any cleanup needed**

Stage only the specific files that changed, then commit.
