# Phase 6: Agent Team — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing 2-layer delegation system with a 3-layer Agent Team architecture: Lapwing → Team Lead → Researcher/Coder.

**Architecture:** Lapwing calls `delegate` tool → Team Lead agent runs its own tool loop with `delegate_to_agent` → concrete agents (Researcher, Coder) execute tasks. All agents share the same `BaseAgent` base class with independent tool loops driven by `LLMRouter.complete_with_tools()`. Events flow through `Dispatcher` to Desktop SSE.

**Tech Stack:** Python 3.11, asyncio, pytest, existing LLMRouter/ToolRegistry/Dispatcher infrastructure.

---

## What Gets Replaced

The following files will be **deleted** (their functionality is superseded):

| File | Reason |
|------|--------|
| `src/core/delegation.py` (429 lines) | Replaced by Team Lead + BaseAgent tool loop |
| `src/core/agent_dispatcher.py` (128 lines) | Replaced by `delegate` tool calling Team Lead directly |
| `src/core/agent_registry.py` (104 lines) | Replaced by new `src/agents/registry.py` |
| `src/core/agent_base.py` (149 lines) | Replaced by new `src/agents/base.py` |
| `src/core/agent_protocol.py` (119 lines) | Replaced by new `src/agents/types.py` |
| `src/tools/delegation_tool.py` (101 lines) | Replaced by new `src/tools/agent_tools.py` |
| `src/agents/researcher.py` (223 lines) | Replaced by new simpler version using BaseAgent tool loop |
| `src/agents/browser_agent.py` (143 lines) | Not re-implemented in Phase 6 (can be added later) |

The following test files will be **deleted** and replaced:

| File | Reason |
|------|--------|
| `tests/core/test_delegation.py` | Old DelegationManager tests |
| `tests/core/test_delegation_tool_agent.py` | Old delegate_task tool tests |
| `tests/core/test_agent_dispatcher.py` | Old AgentDispatcher tests |
| `tests/core/test_agent_registry.py` | Old AgentRegistry tests |
| `tests/core/test_agent_base.py` | Old BaseAgent tests |
| `tests/core/test_agent_protocol.py` | Old protocol tests |
| `tests/agents/test_researcher.py` | Old ResearcherAgent tests |
| `tests/agents/test_browser_agent.py` | Old BrowserAgent tests |
| `tests/agents/test_agent_registry.py` | Old agent definitions tests |

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/agents/types.py` | `AgentSpec`, `AgentMessage`, `AgentResult` data models |
| `src/agents/base.py` | `BaseAgent` with LLM-driven tool loop |
| `src/agents/team_lead.py` | `TeamLead` agent (orchestrator) |
| `src/agents/researcher.py` | `Researcher` agent (web search/fetch) |
| `src/agents/coder.py` | `Coder` agent (file/shell in workspace) |
| `src/agents/registry.py` | `AgentRegistry` (replaces existing) |
| `src/tools/agent_tools.py` | `delegate` + `delegate_to_agent` tool executors |
| `src/tools/workspace_tools.py` | Workspace-sandboxed file/shell tools for Coder |
| `tests/agents/test_types.py` | Data model tests |
| `tests/agents/test_base_agent.py` | BaseAgent tool loop tests |
| `tests/agents/test_team_lead.py` | TeamLead tests |
| `tests/agents/test_researcher.py` | Researcher tests |
| `tests/agents/test_coder.py` | Coder tests |
| `tests/agents/test_registry.py` | Registry tests |
| `tests/tools/test_agent_tools.py` | delegate/delegate_to_agent tool tests |
| `tests/tools/test_workspace_tools.py` | Workspace sandboxing tests |

### Modified Files

| File | Change |
|------|--------|
| `src/tools/personal_tools.py` | Remove `_delegate` placeholder + registration |
| `src/app/container.py:334-381` | Replace delegation/agent-team wiring with new system |
| `src/core/brain.py:258-263` | Replace `delegation_manager`/`agent_dispatcher` service injection |

---

## Task 1: Agent Data Models

**Files:**
- Create: `src/agents/types.py`
- Test: `tests/agents/test_types.py`

- [ ] **Step 1: Write tests for data models**

```python
# tests/agents/test_types.py
"""AgentSpec / AgentMessage / AgentResult 数据模型测试。"""

from datetime import datetime

from src.agents.types import AgentMessage, AgentResult, AgentSpec


class TestAgentSpec:
    def test_required_fields(self):
        spec = AgentSpec(
            name="test",
            description="A test agent",
            system_prompt="You are a test agent.",
            model_slot="agent_execution",
            tools=["web_search"],
        )
        assert spec.name == "test"
        assert spec.model_slot == "agent_execution"
        assert spec.tools == ["web_search"]

    def test_defaults(self):
        spec = AgentSpec(
            name="t", description="t", system_prompt="t",
            model_slot="agent_execution", tools=[],
        )
        assert spec.max_rounds == 15
        assert spec.max_tokens == 30000
        assert spec.timeout_seconds == 180


class TestAgentMessage:
    def test_fields(self):
        msg = AgentMessage(
            from_agent="lapwing",
            to_agent="team_lead",
            task_id="task_001",
            content="查一下天气",
            message_type="request",
        )
        assert msg.from_agent == "lapwing"
        assert msg.message_type == "request"
        assert isinstance(msg.timestamp, datetime)


class TestAgentResult:
    def test_done(self):
        r = AgentResult(task_id="t1", status="done", result="ok")
        assert r.status == "done"
        assert r.artifacts == []
        assert r.evidence == []
        assert r.attempted_actions == []

    def test_failed(self):
        r = AgentResult(
            task_id="t1", status="failed", result="",
            reason="timeout", attempted_actions=["search", "retry"],
        )
        assert r.reason == "timeout"
        assert len(r.attempted_actions) == 2
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

```bash
python -m pytest tests/agents/test_types.py -x -q
```

- [ ] **Step 3: Implement data models**

```python
# src/agents/types.py
"""Agent Team 数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentSpec:
    """Agent 的配置描述。"""
    name: str
    description: str
    system_prompt: str
    model_slot: str              # LLMRouter slot: "agent_execution" etc.
    tools: list[str]             # 可用工具名称列表
    max_rounds: int = 15
    max_tokens: int = 30000
    timeout_seconds: int = 180


@dataclass
class AgentMessage:
    """Agent 之间的一条消息。"""
    from_agent: str
    to_agent: str
    task_id: str
    content: str
    message_type: str            # "request" / "response" / "update"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AgentResult:
    """Agent 执行任务的结果。"""
    task_id: str
    status: str                  # "done" / "failed" / "blocked"
    result: str
    artifacts: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    reason: str = ""
    attempted_actions: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/agents/test_types.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agents/types.py tests/agents/test_types.py
git commit -m "feat(agents): add AgentSpec/AgentMessage/AgentResult data models"
```

---

## Task 2: AgentRegistry

**Files:**
- Create: `tests/agents/test_registry.py` (new test file, replaces old `tests/agents/test_agent_registry.py`)
- Rewrite: `src/agents/registry.py` (replace AgentDefinition with new AgentRegistry)

- [ ] **Step 1: Write tests**

```python
# tests/agents/test_registry.py
"""AgentRegistry 测试。"""

from unittest.mock import MagicMock

from src.agents.registry import AgentRegistry


def _make_agent(name="test"):
    agent = MagicMock()
    agent.spec = MagicMock()
    agent.spec.name = name
    agent.spec.description = f"{name} agent"
    return agent


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = _make_agent("researcher")
        reg.register("researcher", agent)
        assert reg.get("researcher") is agent

    def test_get_nonexistent(self):
        reg = AgentRegistry()
        assert reg.get("nope") is None

    def test_list_names_excludes_team_lead(self):
        reg = AgentRegistry()
        reg.register("team_lead", _make_agent("team_lead"))
        reg.register("researcher", _make_agent("researcher"))
        reg.register("coder", _make_agent("coder"))
        names = reg.list_names()
        assert "team_lead" not in names
        assert "researcher" in names
        assert "coder" in names

    def test_list_specs(self):
        reg = AgentRegistry()
        reg.register("team_lead", _make_agent("team_lead"))
        reg.register("researcher", _make_agent("researcher"))
        specs = reg.list_specs()
        assert len(specs) == 1
        assert specs[0]["name"] == "researcher"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/agents/test_registry.py -x -q
```

- [ ] **Step 3: Implement AgentRegistry**

Replace the contents of `src/agents/registry.py` entirely:

```python
# src/agents/registry.py
"""Agent 注册表。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseAgent

logger = logging.getLogger("lapwing.agents.registry")


class AgentRegistry:
    """Agent 注册表。"""

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}

    def register(self, name: str, agent: "BaseAgent"):
        self._agents[name] = agent
        logger.info("Agent '%s' 已注册", name)

    def get(self, name: str) -> "BaseAgent | None":
        return self._agents.get(name)

    def list_names(self) -> list[str]:
        """返回除 team_lead 外的 Agent 名称（供 Team Lead prompt 参考）。"""
        return [n for n in self._agents if n != "team_lead"]

    def list_specs(self) -> list[dict]:
        """返回除 team_lead 外的 Agent 描述。"""
        return [
            {"name": a.spec.name, "description": a.spec.description}
            for n, a in self._agents.items()
            if n != "team_lead"
        ]
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/agents/test_registry.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agents/registry.py tests/agents/test_registry.py
git commit -m "feat(agents): rewrite AgentRegistry for Phase 6 team architecture"
```

---

## Task 3: BaseAgent with Tool Loop

**Files:**
- Create: `src/agents/base.py`
- Create: `tests/agents/test_base_agent.py`

This is the core piece — a generic agent that runs an independent LLM tool loop using `LLMRouter.complete_with_tools()` and `ToolRegistry.execute()`.

- [ ] **Step 1: Write tests**

```python
# tests/agents/test_base_agent.py
"""BaseAgent tool loop 测试。"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import BaseAgent
from src.agents.types import AgentMessage, AgentResult, AgentSpec


def _make_spec(**overrides):
    defaults = dict(
        name="test_agent",
        description="test",
        system_prompt="You are a test agent.",
        model_slot="agent_execution",
        tools=["web_search"],
        max_rounds=5,
        max_tokens=10000,
        timeout_seconds=10,
    )
    defaults.update(overrides)
    return AgentSpec(**defaults)


def _make_message(content="do something", task_id="t1"):
    return AgentMessage(
        from_agent="team_lead",
        to_agent="test_agent",
        task_id=task_id,
        content=content,
        message_type="request",
    )


def _make_deps(tool_turn_result=None, tool_exec_result=None):
    """Create mock llm_router, tool_registry, dispatcher."""
    from src.core.llm_types import ToolCallRequest, ToolTurnResult

    router = MagicMock()
    # Default: LLM returns text with no tool calls (task done immediately)
    if tool_turn_result is None:
        tool_turn_result = ToolTurnResult(
            text="Done.", tool_calls=[], continuation_message=None,
        )
    router.complete_with_tools = AsyncMock(return_value=tool_turn_result)
    router.build_tool_result_message = MagicMock(return_value={"role": "user", "content": "tool result"})

    registry = MagicMock()
    # Default tool spec lookup
    tool_spec = MagicMock()
    tool_spec.name = "web_search"
    tool_spec.description = "Search the web"
    tool_spec.json_schema = {"type": "object", "properties": {}}
    registry.get = MagicMock(return_value=tool_spec)

    if tool_exec_result is None:
        from src.tools.types import ToolExecutionResult
        tool_exec_result = ToolExecutionResult(
            success=True, payload={"results": ["result1"]},
        )
    registry.execute = AsyncMock(return_value=tool_exec_result)

    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")

    return router, registry, dispatcher


class TestBaseAgentNoCalls:
    """LLM returns text immediately, no tool calls."""

    async def test_returns_done(self):
        spec = _make_spec()
        router, registry, dispatcher = _make_deps()
        agent = BaseAgent(spec, router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "done"
        assert result.result == "Done."

    async def test_publishes_start_event(self):
        spec = _make_spec()
        router, registry, dispatcher = _make_deps()
        agent = BaseAgent(spec, router, registry, dispatcher)
        await agent.execute(_make_message())
        dispatcher.submit.assert_any_call(
            event_type="agent.task_started",
            actor="test_agent",
            task_id="t1",
            payload=pytest.approx({"task_request": "", "message": "do something"}, rel=None, abs=None),
        )


class TestBaseAgentWithToolCalls:
    """LLM requests tool calls, then finishes."""

    async def test_executes_tool_and_returns(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        # Round 1: LLM calls web_search
        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={"query": "test"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        # Round 2: LLM returns final text
        round2 = ToolTurnResult(
            text="Found results.", tool_calls=[], continuation_message=None,
        )

        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])

        agent = BaseAgent(_make_spec(), router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "done"
        assert result.result == "Found results."
        assert registry.execute.await_count == 1

    async def test_publishes_tool_called_event(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={"query": "q"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        round2 = ToolTurnResult(text="ok", tool_calls=[], continuation_message=None)

        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=[round1, round2])

        agent = BaseAgent(_make_spec(), router, registry, dispatcher)
        await agent.execute(_make_message())

        tool_events = [
            c for c in dispatcher.submit.call_args_list
            if c.kwargs.get("event_type") == "agent.tool_called"
            or (c.args and c.args[0] == "agent.tool_called")
        ]
        assert len(tool_events) >= 1


class TestBaseAgentMaxRounds:
    """Exceeds max_rounds → failed result."""

    async def test_fails_on_max_rounds(self):
        from src.core.llm_types import ToolCallRequest, ToolTurnResult

        # Always return tool calls, never finish
        always_calls = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="tc1", name="web_search", arguments={})],
            continuation_message={"role": "assistant", "content": ""},
        )

        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(return_value=always_calls)

        spec = _make_spec(max_rounds=3)
        agent = BaseAgent(spec, router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "failed"
        assert "3" in result.reason


class TestBaseAgentTimeout:
    """LLM call times out → failed result."""

    async def test_timeout_returns_failed(self):
        router, registry, dispatcher = _make_deps()
        router.complete_with_tools = AsyncMock(side_effect=asyncio.TimeoutError)

        spec = _make_spec(timeout_seconds=1)
        agent = BaseAgent(spec, router, registry, dispatcher)
        result = await agent.execute(_make_message())
        assert result.status == "failed"
        assert "超时" in result.reason or "timeout" in result.reason.lower()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/agents/test_base_agent.py -x -q
```

- [ ] **Step 3: Implement BaseAgent**

```python
# src/agents/base.py
"""Agent 基类：所有 Agent 的 tool loop 实现。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from .types import AgentMessage, AgentResult, AgentSpec

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry
    from src.tools.types import ToolExecutionRequest

logger = logging.getLogger("lapwing.agents.base")


class BaseAgent:
    """通用 Agent：接收 AgentMessage，跑独立 tool loop，返回 AgentResult。"""

    def __init__(
        self,
        spec: AgentSpec,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        dispatcher: "Dispatcher",
    ):
        self.spec = spec
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.dispatcher = dispatcher

    async def execute(self, message: AgentMessage) -> AgentResult:
        """执行任务：独立 tool loop。"""

        await self.dispatcher.submit(
            event_type="agent.task_started",
            actor=self.spec.name,
            task_id=message.task_id,
            payload={"task_request": "", "message": message.content},
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt(message)},
            {"role": "user", "content": message.content},
        ]

        available_tools = self._get_tools()

        for round_num in range(self.spec.max_rounds):
            try:
                response = await asyncio.wait_for(
                    self.llm_router.complete_with_tools(
                        messages=messages,
                        tools=available_tools,
                        slot=self.spec.model_slot,
                        max_tokens=min(self.spec.max_tokens // 2, 4096),
                        origin=f"agent:{self.spec.name}",
                    ),
                    timeout=self.spec.timeout_seconds,
                )
            except asyncio.TimeoutError:
                return AgentResult(
                    task_id=message.task_id,
                    status="failed",
                    result="",
                    reason="LLM 调用超时",
                )
            except Exception as exc:
                logger.exception("Agent '%s' LLM 调用失败", self.spec.name)
                return AgentResult(
                    task_id=message.task_id,
                    status="failed",
                    result="",
                    reason=f"LLM error: {exc}",
                )

            # 无 tool_calls → 任务完成
            if not response.tool_calls:
                return AgentResult(
                    task_id=message.task_id,
                    status="done",
                    result=response.text,
                    evidence=self._extract_evidence(messages),
                )

            # 追加 assistant continuation
            if response.continuation_message:
                messages.append(response.continuation_message)

            # 执行工具
            tool_results: list[tuple] = []
            for tc in response.tool_calls:
                output = await self._execute_tool(tc, message)
                tool_results.append((tc, output))

                await self.dispatcher.submit(
                    event_type="agent.tool_called",
                    actor=self.spec.name,
                    task_id=message.task_id,
                    payload={
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "success": True,
                    },
                )

            # 追加 tool results — build_tool_result_message expects list[tuple[ToolCallRequest, str]]
            result_msg = self.llm_router.build_tool_result_message(
                tool_results, slot=self.spec.model_slot,
            )
            if isinstance(result_msg, list):
                messages.extend(result_msg)
            elif result_msg:
                messages.append(result_msg)

        # 超出 max_rounds
        return AgentResult(
            task_id=message.task_id,
            status="failed",
            result="",
            reason=f"超过最大轮数 {self.spec.max_rounds}",
        )

    def _build_system_prompt(self, message: AgentMessage) -> str:
        return f"""{self.spec.system_prompt}

## 当前任务

Task ID: {message.task_id}
来源: {message.from_agent}

请完成任务后直接返回结果文本。不需要再调用工具时，输出最终结果即可。"""

    def _get_tools(self) -> list[dict]:
        tools = []
        for tool_name in self.spec.tools:
            spec = self.tool_registry.get(tool_name)
            if spec:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.json_schema,
                    },
                })
        return tools

    async def _execute_tool(self, tool_call, message: AgentMessage) -> str:
        """执行工具并返回 JSON 字符串结果。"""
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd=".",
            adapter="agent",
            user_id=f"agent:{self.spec.name}",
            auth_level=1,  # TRUSTED
            chat_id=f"agent-{message.task_id}",
            services={},
        )

        req = ToolExecutionRequest(name=tool_call.name, arguments=tool_call.arguments)
        try:
            result = await self.tool_registry.execute(req, ctx)
            return json.dumps(result.payload, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.exception("Agent '%s' tool '%s' failed", self.spec.name, tool_call.name)
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    def _extract_evidence(self, messages: list[dict]) -> list[dict]:
        evidence = []
        for msg in messages:
            if msg.get("role") == "tool":
                try:
                    content = json.loads(msg.get("content", "{}"))
                    if isinstance(content, dict):
                        if "url" in content:
                            evidence.append({"type": "url", "value": content["url"]})
                        if "file_path" in content:
                            evidence.append({"type": "file", "value": content["file_path"]})
                except Exception:
                    pass
        return evidence


async def _noop_shell(cmd: str):
    """Agent 不允许直接执行 shell。"""
    from src.tools.shell_executor import ShellResult
    return ShellResult(stdout="", stderr="Shell disabled for agents", return_code=1)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/agents/test_base_agent.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agents/base.py tests/agents/test_base_agent.py
git commit -m "feat(agents): add BaseAgent with LLM-driven tool loop"
```

---

## Task 4: Team Lead Agent

**Files:**
- Create: `src/agents/team_lead.py`
- Create: `tests/agents/test_team_lead.py`

Team Lead is an agent whose only tool is `delegate_to_agent`. It receives Lapwing's request, decides which agent(s) to use, and coordinates results.

- [ ] **Step 1: Write tests**

```python
# tests/agents/test_team_lead.py
"""TeamLead Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.team_lead import TeamLead
from src.agents.types import AgentMessage


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")
    return router, registry, dispatcher


class TestTeamLeadCreate:
    def test_creates_with_correct_spec(self):
        router, registry, dispatcher = _make_deps()
        tl = TeamLead.create(router, registry, dispatcher)
        assert tl.spec.name == "team_lead"
        assert "delegate_to_agent" in tl.spec.tools
        assert tl.spec.model_slot == "agent_execution"

    def test_system_prompt_mentions_agents(self):
        router, registry, dispatcher = _make_deps()
        tl = TeamLead.create(router, registry, dispatcher)
        assert "researcher" in tl.spec.system_prompt.lower()
        assert "coder" in tl.spec.system_prompt.lower()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/agents/test_team_lead.py -x -q
```

- [ ] **Step 3: Implement TeamLead**

```python
# src/agents/team_lead.py
"""Team Lead Agent — 任务管理者。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseAgent
from .types import AgentSpec

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry

TEAM_LEAD_SYSTEM_PROMPT = """你是 Lapwing 的工作团队 Team Lead。你是一个任务管理者，不是聊天的人——不要闲聊，不要表达感情。

## 你的职责

1. 理解 Lapwing 的需求
2. 判断任务类型，分配给合适的 Agent：
   - Researcher: 搜索、调研、信息整理
   - Coder: 写代码、调试、跑脚本、文件操作
3. 监控任务进度
4. 整合 Agent 返回的结果
5. 把结果汇报给 Lapwing

## 可用的 Agent

- researcher: 擅长网络搜索、信息整理、写摘要
- coder: 擅长写代码、跑脚本、文件读写

## 工作流程

1. 收到 Lapwing 的请求后，先分析任务
2. 如果需要多步协作（如"查资料然后写代码"），拆分成多个子任务按顺序派
3. 用 delegate_to_agent 工具把任务派给 Agent
4. 收到 Agent 的结果后，决定：
   - 结果满足需求 → 汇总输出给 Lapwing
   - 结果不够好 → 重新派（最多 2 次）
   - 失败 → 告诉 Lapwing 原因
5. 最后的回复要简洁，聚焦结果本身

## 输出格式

当你完成任务时，直接输出要返回给 Lapwing 的内容。不需要客套话。"""


class TeamLead(BaseAgent):
    """团队管理者。"""

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        dispatcher: "Dispatcher",
    ) -> "TeamLead":
        spec = AgentSpec(
            name="team_lead",
            description="团队管理者",
            system_prompt=TEAM_LEAD_SYSTEM_PROMPT,
            model_slot="agent_execution",
            tools=["delegate_to_agent"],
            max_rounds=10,
            max_tokens=20000,
            timeout_seconds=300,
        )
        return cls(spec, llm_router, tool_registry, dispatcher)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/agents/test_team_lead.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agents/team_lead.py tests/agents/test_team_lead.py
git commit -m "feat(agents): add TeamLead agent with orchestration prompt"
```

---

## Task 5: Researcher Agent

**Files:**
- Rewrite: `src/agents/researcher.py`
- Create: `tests/agents/test_researcher.py`

New Researcher is much simpler — it relies on the BaseAgent tool loop instead of a hand-coded multi-step pipeline.

- [ ] **Step 1: Write tests**

```python
# tests/agents/test_researcher.py
"""Researcher Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.researcher import Researcher


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")
    return router, registry, dispatcher


class TestResearcherCreate:
    def test_creates_with_correct_spec(self):
        router, registry, dispatcher = _make_deps()
        r = Researcher.create(router, registry, dispatcher)
        assert r.spec.name == "researcher"
        assert "web_search" in r.spec.tools
        assert "web_fetch" in r.spec.tools
        assert r.spec.model_slot == "agent_execution"

    def test_system_prompt_mentions_sources(self):
        router, registry, dispatcher = _make_deps()
        r = Researcher.create(router, registry, dispatcher)
        assert "来源" in r.spec.system_prompt or "URL" in r.spec.system_prompt
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/agents/test_researcher.py -x -q
```

- [ ] **Step 3: Implement Researcher**

```python
# src/agents/researcher.py
"""Researcher Agent — 搜索和调研。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseAgent
from .types import AgentSpec

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry

RESEARCHER_SYSTEM_PROMPT = """你是 Lapwing 团队的 Researcher。你擅长搜索、调研、整理信息。

## 你的职责

1. 根据任务需求，用搜索工具查找信息
2. 必要时抓取网页内容深入阅读
3. 整理成简洁的摘要
4. 在结果中标注信息来源（URL）

## 你的边界

- 你是执行者，不闲聊
- 不做主观判断，只整理事实
- 每个结论都要有来源支持
- 找不到的信息直接说"没找到"

## 输出格式

完成任务后，输出简洁的摘要，每条要点后附上 [来源: URL]。"""


class Researcher(BaseAgent):
    """搜索和调研 Agent。"""

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        dispatcher: "Dispatcher",
    ) -> "Researcher":
        spec = AgentSpec(
            name="researcher",
            description="搜索和调研",
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            model_slot="agent_execution",
            tools=["web_search", "web_fetch"],
            max_rounds=15,
            max_tokens=40000,
            timeout_seconds=300,
        )
        return cls(spec, llm_router, tool_registry, dispatcher)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/agents/test_researcher.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agents/researcher.py tests/agents/test_researcher.py
git commit -m "feat(agents): add Researcher agent (tool-loop based)"
```

---

## Task 6: Coder Agent + Workspace Tools

**Files:**
- Create: `src/agents/coder.py`
- Create: `src/tools/workspace_tools.py`
- Create: `tests/agents/test_coder.py`
- Create: `tests/tools/test_workspace_tools.py`

Coder operates in `data/agent_workspace/` only. Workspace tools enforce path sandboxing.

- [ ] **Step 1: Write workspace tools tests**

```python
# tests/tools/test_workspace_tools.py
"""Workspace 工具沙箱测试。"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult
from src.tools.workspace_tools import (
    AGENT_WORKSPACE,
    ws_file_list_executor,
    ws_file_read_executor,
    ws_file_write_executor,
)


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Patch AGENT_WORKSPACE to a temp dir."""
    import src.tools.workspace_tools as mod
    monkeypatch.setattr(mod, "AGENT_WORKSPACE", tmp_path)
    return tmp_path


def _make_ctx():
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        adapter="agent",
        user_id="agent:coder",
        auth_level=1,
    )


class TestWsFileWrite:
    async def test_write_in_workspace(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_write",
            arguments={"path": "hello.py", "content": "print('hi')"},
        )
        result = await ws_file_write_executor(req, _make_ctx())
        assert result.success
        assert (tmp_workspace / "hello.py").read_text() == "print('hi')"

    async def test_write_nested_path(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_write",
            arguments={"path": "sub/dir/file.txt", "content": "nested"},
        )
        result = await ws_file_write_executor(req, _make_ctx())
        assert result.success
        assert (tmp_workspace / "sub" / "dir" / "file.txt").read_text() == "nested"

    async def test_blocks_path_traversal(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_write",
            arguments={"path": "../../etc/passwd", "content": "hacked"},
        )
        result = await ws_file_write_executor(req, _make_ctx())
        assert not result.success
        assert "agent_workspace" in result.reason


class TestWsFileRead:
    async def test_read_existing(self, tmp_workspace):
        (tmp_workspace / "test.txt").write_text("hello")
        req = ToolExecutionRequest(
            name="ws_file_read",
            arguments={"path": "test.txt"},
        )
        result = await ws_file_read_executor(req, _make_ctx())
        assert result.success
        assert result.payload["content"] == "hello"

    async def test_read_nonexistent(self, tmp_workspace):
        req = ToolExecutionRequest(
            name="ws_file_read",
            arguments={"path": "nope.txt"},
        )
        result = await ws_file_read_executor(req, _make_ctx())
        assert not result.success


class TestWsFileList:
    async def test_list_files(self, tmp_workspace):
        (tmp_workspace / "a.py").write_text("")
        (tmp_workspace / "b.py").write_text("")
        req = ToolExecutionRequest(
            name="ws_file_list",
            arguments={"path": "."},
        )
        result = await ws_file_list_executor(req, _make_ctx())
        assert result.success
        assert "a.py" in result.payload["files"]
        assert "b.py" in result.payload["files"]
```

- [ ] **Step 2: Write Coder agent tests**

```python
# tests/agents/test_coder.py
"""Coder Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.coder import Coder


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")
    return router, registry, dispatcher


class TestCoderCreate:
    def test_creates_with_correct_spec(self):
        router, registry, dispatcher = _make_deps()
        c = Coder.create(router, registry, dispatcher)
        assert c.spec.name == "coder"
        assert "ws_file_write" in c.spec.tools
        assert "ws_file_read" in c.spec.tools
        assert "ws_file_list" in c.spec.tools
        assert "execute_shell" in c.spec.tools

    def test_system_prompt_mentions_workspace(self):
        router, registry, dispatcher = _make_deps()
        c = Coder.create(router, registry, dispatcher)
        assert "agent_workspace" in c.spec.system_prompt
```

- [ ] **Step 3: Run tests — expect FAIL**

```bash
python -m pytest tests/tools/test_workspace_tools.py tests/agents/test_coder.py -x -q
```

- [ ] **Step 4: Implement workspace tools**

```python
# src/tools/workspace_tools.py
"""Coder Agent 工作区工具 — 限制在 data/agent_workspace/ 下。"""

from __future__ import annotations

import logging
from pathlib import Path

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.workspace")

AGENT_WORKSPACE = Path("data/agent_workspace")


def _resolve_safe(path_str: str) -> Path | None:
    """解析路径并确保在 workspace 内。返回 None 表示越界。"""
    resolved = (AGENT_WORKSPACE / path_str).resolve()
    try:
        resolved.relative_to(AGENT_WORKSPACE.resolve())
        return resolved
    except ValueError:
        return None


async def ws_file_write_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """写入文件到 agent_workspace。"""
    path_str = req.arguments.get("path", "")
    content = req.arguments.get("content", "")

    resolved = _resolve_safe(path_str)
    if resolved is None:
        return ToolExecutionResult(
            success=False, payload={},
            reason="只能写入 data/agent_workspace/ 下的文件。",
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")

    rel = resolved.relative_to(Path.cwd()) if resolved.is_relative_to(Path.cwd()) else resolved
    return ToolExecutionResult(
        success=True, payload={"path": str(rel)}, reason="已写入",
    )


async def ws_file_read_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """读取 agent_workspace 中的文件。"""
    path_str = req.arguments.get("path", "")

    resolved = _resolve_safe(path_str)
    if resolved is None:
        return ToolExecutionResult(
            success=False, payload={},
            reason="只能读取 data/agent_workspace/ 下的文件。",
        )

    if not resolved.exists():
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"文件不存在: {path_str}",
        )

    content = resolved.read_text(encoding="utf-8")
    return ToolExecutionResult(
        success=True, payload={"content": content, "path": path_str},
    )


async def ws_file_list_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """列出 agent_workspace 中的文件。"""
    path_str = req.arguments.get("path", ".")

    resolved = _resolve_safe(path_str)
    if resolved is None:
        return ToolExecutionResult(
            success=False, payload={},
            reason="只能列出 data/agent_workspace/ 下的内容。",
        )

    if not resolved.exists():
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"目录不存在: {path_str}",
        )

    files = sorted(p.name for p in resolved.iterdir())
    return ToolExecutionResult(
        success=True, payload={"files": files, "path": path_str},
    )
```

- [ ] **Step 5: Implement Coder agent**

```python
# src/agents/coder.py
"""Coder Agent — 写代码和执行。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseAgent
from .types import AgentSpec

if TYPE_CHECKING:
    from src.core.dispatcher import Dispatcher
    from src.core.llm_router import LLMRouter
    from src.tools.registry import ToolRegistry

CODER_SYSTEM_PROMPT = """你是 Lapwing 团队的 Coder。你擅长写代码、调试、跑脚本。

## 你的职责

1. 根据需求写代码或修改代码
2. 执行代码或 shell 命令验证
3. 返回代码结果或执行输出

## 你的工作区

你的所有文件操作都在 data/agent_workspace/ 目录下。你不能直接修改 src/ 下的生产代码。

如果任务涉及修改系统代码，产出 patch 文件到 patches/ 目录，由 Kevin 审核后合入。

## 你的边界

- 你是执行者，不闲聊
- 不做需求评判，按指令完成
- 代码要简洁、可读
- 失败时报告错误信息和尝试过的方案

## 输出格式

完成任务后，输出简洁的总结：做了什么、结果如何、产出文件在哪。"""


class Coder(BaseAgent):
    """代码和执行 Agent。"""

    @classmethod
    def create(
        cls,
        llm_router: "LLMRouter",
        tool_registry: "ToolRegistry",
        dispatcher: "Dispatcher",
    ) -> "Coder":
        spec = AgentSpec(
            name="coder",
            description="写代码和执行",
            system_prompt=CODER_SYSTEM_PROMPT,
            model_slot="agent_execution",
            tools=["ws_file_read", "ws_file_write", "ws_file_list", "execute_shell", "run_python_code"],
            max_rounds=20,
            max_tokens=50000,
            timeout_seconds=600,
        )
        return cls(spec, llm_router, tool_registry, dispatcher)
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
python -m pytest tests/tools/test_workspace_tools.py tests/agents/test_coder.py -x -q
```

- [ ] **Step 7: Commit**

```bash
git add src/agents/coder.py src/tools/workspace_tools.py tests/agents/test_coder.py tests/tools/test_workspace_tools.py
git commit -m "feat(agents): add Coder agent with workspace-sandboxed tools"
```

---

## Task 7: Agent Tools (delegate + delegate_to_agent)

**Files:**
- Create: `src/tools/agent_tools.py`
- Create: `tests/tools/test_agent_tools.py`

Two tools:
1. `delegate` — Lapwing calls this. Creates task, executes Team Lead, returns result.
2. `delegate_to_agent` — Team Lead calls this. Dispatches to a named agent.

- [ ] **Step 1: Write tests**

```python
# tests/tools/test_agent_tools.py
"""delegate / delegate_to_agent 工具测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.types import AgentResult
from src.tools.agent_tools import delegate_executor, delegate_to_agent_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_ctx(**services_override):
    services = {
        "agent_registry": MagicMock(),
        "dispatcher": AsyncMock(),
    }
    services.update(services_override)
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd=".",
        services=services,
    )


class TestDelegateExecutor:
    async def test_empty_request_fails(self):
        req = ToolExecutionRequest(name="delegate", arguments={"request": ""})
        result = await delegate_executor(req, _make_ctx())
        assert not result.success

    async def test_no_registry_fails(self):
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(), shell_default_cwd=".",
            services={},
        )
        req = ToolExecutionRequest(name="delegate", arguments={"request": "test"})
        result = await delegate_executor(req, ctx)
        assert not result.success

    async def test_no_team_lead_fails(self):
        registry = MagicMock()
        registry.get.return_value = None
        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(name="delegate", arguments={"request": "test"})
        result = await delegate_executor(req, ctx)
        assert not result.success

    async def test_success_flow(self):
        team_lead = MagicMock()
        team_lead.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="done", result="Here are the results.",
        ))
        registry = MagicMock()
        registry.get.return_value = team_lead

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate",
            arguments={"request": "查一下天气"},
        )
        result = await delegate_executor(req, ctx)
        assert result.success
        assert "results" in result.payload.get("result", "").lower() or result.payload.get("result")

    async def test_failed_delegation(self):
        team_lead = MagicMock()
        team_lead.execute = AsyncMock(return_value=AgentResult(
            task_id="t1", status="failed", result="", reason="timeout",
        ))
        registry = MagicMock()
        registry.get.return_value = team_lead

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(name="delegate", arguments={"request": "do stuff"})
        result = await delegate_executor(req, ctx)
        assert not result.success


class TestDelegateToAgentExecutor:
    async def test_missing_params_fails(self):
        req = ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent": "", "instruction": ""},
        )
        result = await delegate_to_agent_executor(req, _make_ctx())
        assert not result.success

    async def test_unknown_agent_fails(self):
        registry = MagicMock()
        registry.get.return_value = None
        registry.list_names.return_value = ["researcher", "coder"]
        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent": "nonexistent", "instruction": "do stuff"},
        )
        result = await delegate_to_agent_executor(req, ctx)
        assert not result.success
        assert "researcher" in result.reason

    async def test_success_flow(self):
        agent = MagicMock()
        agent.execute = AsyncMock(return_value=AgentResult(
            task_id="sub1", status="done", result="Found info.",
            evidence=[{"type": "url", "value": "https://example.com"}],
        ))
        registry = MagicMock()
        registry.get.return_value = agent

        ctx = _make_ctx(agent_registry=registry)
        req = ToolExecutionRequest(
            name="delegate_to_agent",
            arguments={"agent": "researcher", "instruction": "search for X"},
        )
        result = await delegate_to_agent_executor(req, ctx)
        assert result.success
        assert "Found info." in result.payload.get("result", "")
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/tools/test_agent_tools.py -x -q
```

- [ ] **Step 3: Implement agent tools**

```python
# src/tools/agent_tools.py
"""Agent Team 工具：delegate + delegate_to_agent。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from src.agents.types import AgentMessage, AgentResult
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult, ToolSpec

logger = logging.getLogger("lapwing.tools.agent_tools")


def _generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


async def delegate_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """Lapwing 调用的 delegate 工具。把任务交给 Team Lead。"""
    request = req.arguments.get("request", "").strip()
    context_str = req.arguments.get("context", "")

    if not request:
        return ToolExecutionResult(success=False, payload={}, reason="请求不能为空")

    agent_registry = ctx.services.get("agent_registry")
    dispatcher = ctx.services.get("dispatcher")

    if not agent_registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    team_lead = agent_registry.get("team_lead")
    if not team_lead:
        return ToolExecutionResult(success=False, payload={}, reason="Team Lead 不可用")

    task_id = _generate_task_id()

    if dispatcher:
        await dispatcher.submit(
            event_type="agent.task_created",
            actor="lapwing",
            task_id=task_id,
            payload={"request": request, "assigned_to": "team_lead"},
        )

    message = AgentMessage(
        from_agent="lapwing",
        to_agent="team_lead",
        task_id=task_id,
        content=f"{request}\n\n上下文: {context_str}" if context_str else request,
        message_type="request",
    )

    result = await team_lead.execute(message)

    if dispatcher:
        await dispatcher.submit(
            event_type=f"agent.task_{result.status}",
            actor="team_lead",
            task_id=task_id,
            payload={"result": result.result[:500] if result.result else ""},
        )

    if result.status == "done":
        return ToolExecutionResult(
            success=True,
            payload={
                "task_id": task_id,
                "result": result.result,
                "artifacts": result.artifacts,
            },
            reason="任务完成",
        )
    else:
        return ToolExecutionResult(
            success=False,
            payload={"task_id": task_id, "status": result.status},
            reason=result.reason or "任务失败",
        )


async def delegate_to_agent_executor(
    req: ToolExecutionRequest, ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """Team Lead 调用的工具。把子任务派给具体 Agent。"""
    agent_name = req.arguments.get("agent", "").strip()
    instruction = req.arguments.get("instruction", "").strip()

    if not agent_name or not instruction:
        return ToolExecutionResult(
            success=False, payload={},
            reason="agent 和 instruction 不能为空",
        )

    agent_registry = ctx.services.get("agent_registry")
    dispatcher = ctx.services.get("dispatcher")

    if not agent_registry:
        return ToolExecutionResult(success=False, payload={}, reason="Agent Team 未就绪")

    agent = agent_registry.get(agent_name)
    if not agent:
        available = agent_registry.list_names()
        return ToolExecutionResult(
            success=False, payload={},
            reason=f"Agent '{agent_name}' 不存在。可用: {', '.join(available)}",
        )

    subtask_id = _generate_task_id()

    if dispatcher:
        await dispatcher.submit(
            event_type="agent.task_assigned",
            actor="team_lead",
            task_id=subtask_id,
            payload={"agent": agent_name, "instruction": instruction},
        )

    message = AgentMessage(
        from_agent="team_lead",
        to_agent=agent_name,
        task_id=subtask_id,
        content=instruction,
        message_type="request",
    )

    result = await agent.execute(message)

    if dispatcher:
        await dispatcher.submit(
            event_type=f"agent.task_{result.status}",
            actor=agent_name,
            task_id=subtask_id,
            payload={
                "result": result.result[:500] if result.result else "",
                "evidence": result.evidence,
            },
        )

    if result.status == "done":
        return ToolExecutionResult(
            success=True,
            payload={
                "result": result.result,
                "evidence": result.evidence,
                "artifacts": result.artifacts,
            },
            reason="ok",
        )
    else:
        return ToolExecutionResult(
            success=False,
            payload={"status": result.status},
            reason=result.reason or "失败",
        )


def register_agent_tools(registry) -> None:
    """注册 Agent Team 工具到 ToolRegistry。"""

    registry.register(ToolSpec(
        name="delegate",
        description="把任务交给你的工作团队。告诉 Team Lead 你需要什么。",
        json_schema={
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "你的需求"},
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "紧急程度",
                    "default": "normal",
                },
                "context": {"type": "string", "description": "相关上下文（可选）"},
            },
            "required": ["request"],
        },
        executor=delegate_executor,
        capability="general",
        risk_level="low",
        max_result_tokens=3000,
    ))

    registry.register(ToolSpec(
        name="delegate_to_agent",
        description="把子任务派给一个具体的 Agent。",
        json_schema={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent 名称 (researcher / coder)",
                },
                "instruction": {
                    "type": "string",
                    "description": "给 Agent 的指令",
                },
            },
            "required": ["agent", "instruction"],
        },
        executor=delegate_to_agent_executor,
        capability="agent",
        risk_level="low",
    ))

    logger.info("[agent_tools] 已注册 delegate + delegate_to_agent")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/tools/test_agent_tools.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/agent_tools.py tests/tools/test_agent_tools.py
git commit -m "feat(tools): add delegate and delegate_to_agent executors"
```

---

## Task 8: Remove Old System + Wire New System

**Files:**
- Delete: `src/core/delegation.py`, `src/core/agent_dispatcher.py`, `src/core/agent_registry.py`, `src/core/agent_base.py`, `src/core/agent_protocol.py`, `src/tools/delegation_tool.py`, `src/agents/browser_agent.py`
- Delete: `tests/core/test_delegation.py`, `tests/core/test_delegation_tool_agent.py`, `tests/core/test_agent_dispatcher.py`, `tests/core/test_agent_registry.py`, `tests/core/test_agent_base.py`, `tests/core/test_agent_protocol.py`, `tests/agents/test_browser_agent.py`, `tests/agents/test_agent_registry.py`
- Modify: `src/tools/personal_tools.py` — remove delegate placeholder
- Modify: `src/app/container.py:334-419` — replace delegation/agent wiring
- Modify: `src/core/brain.py:258-263` — replace service injection
- Modify: `src/agents/__init__.py` — update docstring

This task has no new tests — it's wiring existing tested components together. Verification is the full test suite.

- [ ] **Step 1: Delete old files**

```bash
# Old implementation files
rm src/core/delegation.py
rm src/core/agent_dispatcher.py
rm src/core/agent_registry.py
rm src/core/agent_base.py
rm src/core/agent_protocol.py
rm src/tools/delegation_tool.py
rm src/agents/browser_agent.py

# Old test files
rm tests/core/test_delegation.py
rm tests/core/test_delegation_tool_agent.py
rm tests/core/test_agent_dispatcher.py
rm tests/core/test_agent_registry.py
rm tests/core/test_agent_base.py
rm tests/core/test_agent_protocol.py
rm tests/agents/test_browser_agent.py
rm tests/agents/test_agent_registry.py
```

- [ ] **Step 2: Remove delegate placeholder from personal_tools.py**

In `src/tools/personal_tools.py`:
- Delete the `_delegate` function (lines 557-572)
- Delete the delegate ToolSpec registration (lines 748-777)
- Update the tool count in the log message (line 779): `8 个` → `7 个`

- [ ] **Step 3: Rewire container.py**

Replace the delegation and agent team blocks in `src/app/container.py` (lines 334-381) with:

```python
        # ── Agent Team 系统（Phase 6） ──────────────────────────────────
        from config.settings import AGENT_TEAM_ENABLED
        if AGENT_TEAM_ENABLED:
            from src.agents.registry import AgentRegistry
            from src.agents.team_lead import TeamLead
            from src.agents.researcher import Researcher
            from src.agents.coder import Coder
            from src.tools.agent_tools import register_agent_tools
            from src.tools.workspace_tools import (
                ws_file_read_executor,
                ws_file_write_executor,
                ws_file_list_executor,
            )

            agent_registry = AgentRegistry()

            # 注册具体 Agent
            agent_registry.register(
                "team_lead",
                TeamLead.create(
                    self.brain.router,
                    self.brain.tool_registry,
                    self.dispatcher,
                ),
            )
            agent_registry.register(
                "researcher",
                Researcher.create(
                    self.brain.router,
                    self.brain.tool_registry,
                    self.dispatcher,
                ),
            )
            agent_registry.register(
                "coder",
                Coder.create(
                    self.brain.router,
                    self.brain.tool_registry,
                    self.dispatcher,
                ),
            )

            # 注册 Agent 工具（delegate + delegate_to_agent）
            register_agent_tools(self.brain.tool_registry)

            # 注册 workspace 工具（供 Coder 使用）
            from src.tools.types import ToolSpec as _TS
            self.brain.tool_registry.register(_TS(
                name="ws_file_read",
                description="读取工作区文件",
                json_schema={"type": "object", "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                }, "required": ["path"]},
                executor=ws_file_read_executor,
                capability="agent",
                visibility="internal",
            ))
            self.brain.tool_registry.register(_TS(
                name="ws_file_write",
                description="写入工作区文件",
                json_schema={"type": "object", "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                    "content": {"type": "string", "description": "文件内容"},
                }, "required": ["path", "content"]},
                executor=ws_file_write_executor,
                capability="agent",
                visibility="internal",
            ))
            self.brain.tool_registry.register(_TS(
                name="ws_file_list",
                description="列出工作区文件",
                json_schema={"type": "object", "properties": {
                    "path": {"type": "string", "description": "相对路径", "default": "."},
                }, "required": []},
                executor=ws_file_list_executor,
                capability="agent",
                visibility="internal",
            ))

            # 注入 services
            self.brain._agent_registry = agent_registry

            # 创建工作区目录
            from pathlib import Path
            Path("data/agent_workspace").mkdir(parents=True, exist_ok=True)
            Path("data/agent_workspace/patches").mkdir(parents=True, exist_ok=True)

            logger.info("Agent Team 系统已就绪（%d agents）", len(agent_registry.list_names()))
```

Also remove the old `DELEGATION_ENABLED` block (lines 334-343) entirely.

- [ ] **Step 4: Rewire brain.py service injection**

In `src/core/brain.py`, replace lines 258-263:

```python
        delegation_manager = getattr(self, "delegation_manager", None)
        if delegation_manager is not None:
            services["delegation_manager"] = delegation_manager
        agent_dispatcher = getattr(self, "agent_dispatcher", None)
        if agent_dispatcher is not None:
            services["agent_dispatcher"] = agent_dispatcher
```

With:

```python
        agent_registry = getattr(self, "_agent_registry", None)
        if agent_registry is not None:
            services["agent_registry"] = agent_registry
```

Also inject dispatcher:

```python
        # dispatcher is needed by agent tools
        dispatcher = getattr(self, "_dispatcher_ref", None)
        if dispatcher is not None:
            services["dispatcher"] = dispatcher
```

And in `container.py`, after the agent team block, add: `self.brain._dispatcher_ref = self.dispatcher`

- [ ] **Step 5: Update src/agents/__init__.py**

```python
"""Lapwing Agent Team 实现（Phase 6）。"""
```

- [ ] **Step 6: Fix any remaining imports**

Search the codebase for remaining imports of deleted modules and update them:

```bash
grep -rn "from src.core.delegation import" src/ --include="*.py"
grep -rn "from src.core.agent_dispatcher import" src/ --include="*.py"
grep -rn "from src.core.agent_registry import" src/ --include="*.py"
grep -rn "from src.core.agent_base import" src/ --include="*.py"
grep -rn "from src.core.agent_protocol import" src/ --include="*.py"
grep -rn "from src.tools.delegation_tool import" src/ --include="*.py"
grep -rn "from src.agents.browser_agent import" src/ --include="*.py"
```

Fix any stale imports found (likely in `container.py` and `chat_ws.py`).

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/ -x -q
```

All tests must pass. If some fail due to missing imports/modules, fix them before proceeding.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(agents): replace delegation system with Phase 6 Agent Team

- Remove: DelegationManager, AgentDispatcher, old AgentRegistry, old BaseAgent, agent_protocol
- Remove: delegation_tool, browser_agent, delegate placeholder
- Wire: new AgentRegistry, TeamLead, Researcher, Coder in container
- Wire: delegate + delegate_to_agent tools via agent_tools.py
- Wire: workspace tools (ws_file_read/write/list) for Coder sandbox
- Inject: agent_registry + dispatcher into brain services"
```

---

## Task 9: Integration Test (E2E Delegation Flow)

**Files:**
- Create: `tests/agents/test_e2e_delegation.py`

This tests the full delegation chain: delegate tool → Team Lead → delegate_to_agent → Researcher → result bubbles back up.

- [ ] **Step 1: Write E2E test**

```python
# tests/agents/test_e2e_delegation.py
"""端到端 delegation 测试：Lapwing → Team Lead → Agent → 结果。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.coder import Coder
from src.agents.registry import AgentRegistry
from src.agents.researcher import Researcher
from src.agents.team_lead import TeamLead
from src.agents.types import AgentResult
from src.core.llm_types import ToolCallRequest, ToolTurnResult
from src.tools.agent_tools import delegate_executor, register_agent_tools
from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolSpec,
)


def _make_dispatcher():
    d = AsyncMock()
    d.submit = AsyncMock(return_value="evt_001")
    return d


def _make_registry_with_agents(router, tool_registry, dispatcher):
    """Build a full agent registry with all three agents."""
    reg = AgentRegistry()
    reg.register("team_lead", TeamLead.create(router, tool_registry, dispatcher))
    reg.register("researcher", Researcher.create(router, tool_registry, dispatcher))
    reg.register("coder", Coder.create(router, tool_registry, dispatcher))
    return reg


class TestE2EDelegateToResearcher:
    """Lapwing delegates → Team Lead → Researcher → result."""

    async def test_full_chain(self):
        dispatcher = _make_dispatcher()

        # We mock the LLM at the router level.
        # Call sequence:
        #   1. Team Lead calls LLM → gets delegate_to_agent(researcher, "search for X")
        #   2. Team Lead calls LLM again with tool result → returns final text
        #   3. Researcher calls LLM → gets web_search tool call
        #   4. Researcher calls LLM again with tool result → returns "Found: ..."

        # Researcher LLM responses
        researcher_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(id="r1", name="web_search", arguments={"query": "X"})],
            continuation_message={"role": "assistant", "content": ""},
        )
        researcher_round2 = ToolTurnResult(
            text="Found: X is interesting. [来源: https://example.com]",
            tool_calls=[],
            continuation_message=None,
        )

        # Team Lead LLM responses
        tl_round1 = ToolTurnResult(
            text="",
            tool_calls=[ToolCallRequest(
                id="tl1", name="delegate_to_agent",
                arguments={"agent": "researcher", "instruction": "search for X"},
            )],
            continuation_message={"role": "assistant", "content": ""},
        )
        tl_round2 = ToolTurnResult(
            text="调研结果：X is interesting。",
            tool_calls=[],
            continuation_message=None,
        )

        # Router returns different responses per call.
        # Order: TL round1, Researcher round1, Researcher round2, TL round2
        router = MagicMock()
        router.complete_with_tools = AsyncMock(
            side_effect=[tl_round1, researcher_round1, researcher_round2, tl_round2],
        )
        router.build_tool_result_message = MagicMock(
            return_value={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        )

        # Tool registry: needs delegate_to_agent and web_search specs
        tool_registry = MagicMock()

        def _get_tool(name):
            spec = MagicMock()
            spec.name = name
            spec.description = f"{name} tool"
            spec.json_schema = {"type": "object", "properties": {}}
            return spec

        tool_registry.get = MagicMock(side_effect=_get_tool)
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(
            success=True, payload={"results": ["X info"]},
        ))

        # Build registry
        agent_registry = _make_registry_with_agents(router, tool_registry, dispatcher)

        # Execute delegate
        ctx = ToolExecutionContext(
            execute_shell=AsyncMock(),
            shell_default_cwd=".",
            services={
                "agent_registry": agent_registry,
                "dispatcher": dispatcher,
            },
        )
        req = ToolExecutionRequest(
            name="delegate",
            arguments={"request": "帮我查一下 X 是什么"},
        )

        result = await delegate_executor(req, ctx)
        assert result.success
        assert "X" in result.payload.get("result", "")
```

- [ ] **Step 2: Run E2E test**

```bash
python -m pytest tests/agents/test_e2e_delegation.py -x -q
```

- [ ] **Step 3: Run full test suite to verify no regressions**

```bash
python -m pytest tests/ -x -q
```

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_e2e_delegation.py
git commit -m "test(agents): add E2E delegation chain test"
```

---

## Task 10: Register Workspace Tools in build_default_tool_registry

**Files:**
- Modify: `src/tools/registry.py` — add comment noting workspace tools are registered by container

This is a minor documentation step. Workspace tools (`ws_file_*`) are registered by the container when `AGENT_TEAM_ENABLED=true`, not in `build_default_tool_registry()`. Add a comment for clarity.

- [ ] **Step 1: Add comment in registry.py**

Near line 633 (where the old delegate_task comment is), replace:

```python
    # delegate_task 已由 personal_tools.py 的 delegate 替代（Phase 4）
```

With:

```python
    # Agent Team 工具（delegate, delegate_to_agent, ws_file_*）由 container.py 注册（Phase 6）
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -x -q
```

- [ ] **Step 3: Commit**

```bash
git add src/tools/registry.py
git commit -m "docs: update registry comment for Phase 6 agent tools"
```

---

## Summary

| Task | What | New/Modified Files | Tests |
|------|------|-------------------|-------|
| 1 | Data models | `src/agents/types.py` | `tests/agents/test_types.py` |
| 2 | AgentRegistry | `src/agents/registry.py` | `tests/agents/test_registry.py` |
| 3 | BaseAgent tool loop | `src/agents/base.py` | `tests/agents/test_base_agent.py` |
| 4 | TeamLead | `src/agents/team_lead.py` | `tests/agents/test_team_lead.py` |
| 5 | Researcher | `src/agents/researcher.py` | `tests/agents/test_researcher.py` |
| 6 | Coder + workspace | `src/agents/coder.py`, `src/tools/workspace_tools.py` | `tests/agents/test_coder.py`, `tests/tools/test_workspace_tools.py` |
| 7 | Agent tools | `src/tools/agent_tools.py` | `tests/tools/test_agent_tools.py` |
| 8 | Delete old + wire new | Multiple deletes + container/brain changes | Full suite regression |
| 9 | E2E test | `tests/agents/test_e2e_delegation.py` | E2E chain test |
| 10 | Registry comment | `src/tools/registry.py` | Full suite regression |
