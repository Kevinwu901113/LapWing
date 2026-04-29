"""Audit logging for denials + proactive message decisions (commit 9).

Locks in two new MutationType members:

- TOOL_DENIED — fires when a guard refuses a tool call before it
                reaches the executor (AuthorityGate, VitalGuard,
                ShellPolicy, BrowserGuard, run_skill gate).
- PROACTIVE_MESSAGE_DECISION — fires for every ProactiveMessageGate
                outcome (allow / defer / deny).

Both go through StateMutationLog so the existing iteration / chat
indexing applies and external consumers can tail them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.logging.state_mutation_log import MutationType


class _FakeMutationLog:
    """Minimal MutationLog stand-in: collect every recorded event."""

    def __init__(self):
        self.records: list[tuple[str, dict, dict]] = []

    async def record(self, event_type, payload, *, iteration_id=None, chat_id=None):
        self.records.append(
            (event_type.value, dict(payload), {"iteration_id": iteration_id, "chat_id": chat_id})
        )


def _by_type(records, event_type):
    return [r for r in records if r[0] == event_type.value]


class TestMutationTypesExist:
    def test_tool_denied_present(self):
        assert MutationType.TOOL_DENIED.value == "tool.denied"

    def test_proactive_message_decision_present(self):
        assert MutationType.PROACTIVE_MESSAGE_DECISION.value == "proactive_message.decision"


class TestTaskRuntimeRecordToolDenied:
    @pytest.mark.asyncio
    async def test_authority_gate_denial_recorded(self):
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec

        async def _exec(req, ctx):
            return ToolExecutionResult(success=True, payload={})

        registry = ToolRegistry()
        # execute_shell needs OWNER per AuthorityGate. A GUEST adapter
        # call will be rejected.
        registry.register(ToolSpec(
            name="execute_shell",
            description="shell",
            json_schema={"type": "object", "properties": {}},
            executor=_exec,
            capability="shell",
        ))
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        log = _FakeMutationLog()

        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="execute_shell", arguments={"command": "ls"}
            ),
            profile="chat_shell",
            services={"mutation_log": log},
            adapter="qq",
            user_id="random_guest",
            chat_id="c1",
        )
        assert result.success is False
        denied = _by_type(log.records, MutationType.TOOL_DENIED)
        assert len(denied) == 1
        _, payload, meta = denied[0]
        assert payload["tool"] == "execute_shell"
        assert payload["guard"] == "authority_gate"
        assert payload["reason"]
        assert payload["auth_level"] in (0, 1, 2)
        assert meta["chat_id"] == "c1"

    @pytest.mark.asyncio
    async def test_vital_guard_block_recorded(self):
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import build_default_tool_registry
        from src.tools.types import ToolExecutionRequest

        runtime = TaskRuntime(router=MagicMock(), tool_registry=build_default_tool_registry())
        log = _FakeMutationLog()
        # rm -rf / is a classic VitalGuard block.
        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="execute_shell", arguments={"command": "rm -rf /"}
            ),
            profile="chat_shell",
            services={"mutation_log": log},
            chat_id="c1",
        )
        assert result.success is False
        denied = _by_type(log.records, MutationType.TOOL_DENIED)
        # Either VitalGuard or AuthorityGate — both are valid block paths.
        # The test asserts at least one denial record fired.
        assert denied, f"expected a TOOL_DENIED record, got {log.records!r}"
        guards = {r[1]["guard"] for r in denied}
        assert guards & {"vital_guard", "authority_gate", "shell_policy"}

    @pytest.mark.asyncio
    async def test_browser_guard_missing_recorded(self):
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec

        async def _exec(req, ctx):
            return ToolExecutionResult(success=True, payload={})

        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="browser_open",
            description="open",
            json_schema={"type": "object", "properties": {}},
            executor=_exec,
            capability="browser",
        ))
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        # Deliberately do not call set_browser_guard.
        log = _FakeMutationLog()
        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="browser_open", arguments={"url": "https://x.com/"}
            ),
            profile="task_execution",
            services={"mutation_log": log},
            chat_id="c1",
        )
        assert result.success is False
        denied = _by_type(log.records, MutationType.TOOL_DENIED)
        assert len(denied) == 1
        assert denied[0][1]["guard"] == "browser_guard_missing"

    @pytest.mark.asyncio
    async def test_unknown_tool_denial_recorded(self):
        """An LLM that hallucinates a tool name is a soft attack on the
        loop budget; record it so we can spot patterns."""
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest

        runtime = TaskRuntime(router=MagicMock(), tool_registry=ToolRegistry())
        log = _FakeMutationLog()
        result = await runtime.execute_tool(
            request=ToolExecutionRequest(name="hallucinated_tool", arguments={}),
            profile="chat_shell",
            services={"mutation_log": log},
            chat_id="c1",
        )
        assert result.success is False
        denied = _by_type(log.records, MutationType.TOOL_DENIED)
        assert len(denied) == 1
        _, payload, meta = denied[0]
        assert payload["tool"] == "hallucinated_tool"
        assert payload["guard"] == "unknown_tool"
        assert payload["profile"] == "chat_shell"
        assert meta["chat_id"] == "c1"

    @pytest.mark.asyncio
    async def test_profile_not_allowed_denial_recorded(self):
        """A real tool called from a profile that doesn't expose it is
        also a denial — log it the same way as guard rejections."""
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec

        async def _exec(req, ctx):
            return ToolExecutionResult(success=True, payload={})

        registry = ToolRegistry()
        # browser_open is registered globally but is not part of chat_shell
        # profile's allowed tool names — calling it from chat_shell denies.
        registry.register(ToolSpec(
            name="browser_open",
            description="open",
            json_schema={"type": "object", "properties": {}},
            executor=_exec,
            capability="browser",
        ))
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        log = _FakeMutationLog()
        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="browser_open", arguments={"url": "https://x.com/"}
            ),
            profile="zero_tools",  # does not expose browser_open
            services={"mutation_log": log},
            chat_id="c1",
        )
        assert result.success is False
        denied = _by_type(log.records, MutationType.TOOL_DENIED)
        assert len(denied) == 1
        _, payload, meta = denied[0]
        assert payload["tool"] == "browser_open"
        assert payload["guard"] == "profile_not_allowed"
        assert payload["profile"] == "zero_tools"
        assert meta["chat_id"] == "c1"

    @pytest.mark.asyncio
    async def test_browser_guard_url_block_recorded(self):
        from src.core.browser_guard import BrowserGuard
        from src.core.task_runtime import TaskRuntime
        from src.tools.registry import ToolRegistry
        from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec

        async def _exec(req, ctx):
            return ToolExecutionResult(success=True, payload={})

        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="browser_open",
            description="open",
            json_schema={"type": "object", "properties": {}},
            executor=_exec,
            capability="browser",
        ))
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        runtime.set_browser_guard(BrowserGuard())  # block_internal_network=True
        log = _FakeMutationLog()
        result = await runtime.execute_tool(
            request=ToolExecutionRequest(
                name="browser_open", arguments={"url": "http://localhost/admin"}
            ),
            profile="task_execution",
            services={"mutation_log": log},
            chat_id="c1",
        )
        assert result.success is False
        denied = _by_type(log.records, MutationType.TOOL_DENIED)
        assert len(denied) == 1
        assert denied[0][1]["guard"] == "browser_guard"
        assert "url" in denied[0][1]


class TestRunSkillGateRecordsDenial:
    @pytest.mark.asyncio
    async def test_chat_extended_draft_denial_recorded(self):
        from src.tools.skill_tools import run_skill_executor
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.shell_executor import ShellResult

        log = _FakeMutationLog()

        class _Store:
            def read(self, sid):
                return {"meta": {"maturity": "draft", "trust_required": "guest"}}

        class _Exec:
            async def execute(self, *a, **k):
                class _R:
                    success = True; output = "ok"; error = ""; exit_code = 0; timed_out = False
                return _R()

        async def _noop_shell(_):
            return ShellResult(stdout="", stderr="", return_code=0)

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd="/tmp",
            services={
                "skill_store": _Store(),
                "skill_executor": _Exec(),
                "mutation_log": log,
            },
            auth_level=2,
            chat_id="c1",
            runtime_profile="standard",
        )
        req = ToolExecutionRequest(name="run_skill", arguments={"skill_id": "s1"})
        result = await run_skill_executor(req, ctx)
        assert result.success is False

        denied = _by_type(log.records, MutationType.TOOL_DENIED)
        assert len(denied) == 1
        _, payload, _ = denied[0]
        assert payload["guard"] == "run_skill_gate"
        assert payload["profile"] == "standard"
        assert payload["skill_id"] == "s1"


class TestProactiveMessageDecisionRecorded:
    @pytest.mark.asyncio
    async def test_allow_decision_recorded(self):
        from datetime import datetime
        from src.core.proactive_message_gate import ProactiveMessageGate
        from src.tools.personal_tools import _send_message
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.shell_executor import ShellResult

        gate = ProactiveMessageGate(
            enabled=True, max_per_day=3, min_minutes_between=0,
            quiet_hours_start="00:00", quiet_hours_end="00:00",  # disabled
            clock=lambda: datetime(2026, 1, 1, 14, 0),
        )

        class _QQ:
            sent: list = []
            async def send_private_message(self, qq, content):
                self.sent.append(content)

        class _CM:
            def __init__(self):
                self.qq = _QQ()
            def get_adapter(self, name):
                return self.qq if name == "qq" else None

        log = _FakeMutationLog()

        async def _noop_shell(_):
            return ShellResult(stdout="", stderr="", return_code=0)

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd="/tmp",
            services={
                "channel_manager": _CM(),
                "owner_qq_id": "12345",
                "proactive_message_gate": gate,
                "mutation_log": log,
            },
            auth_level=2,
            chat_id="c-proactive",
            runtime_profile="inner_tick",
        )
        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "hi"},
        )
        result = await _send_message(req, ctx)
        assert result.success is True

        decisions = _by_type(log.records, MutationType.PROACTIVE_MESSAGE_DECISION)
        assert len(decisions) == 1
        _, payload, meta = decisions[0]
        assert payload["decision"] == "allow"
        assert payload["target"] == "kevin_qq"
        assert payload["urgent"] is False
        assert payload["bypassed"] is False
        assert payload["runtime_profile"] == "inner_tick"
        assert meta["chat_id"] == "c-proactive"

    @pytest.mark.asyncio
    async def test_defer_decision_recorded(self):
        from datetime import datetime
        from src.core.proactive_message_gate import ProactiveMessageGate
        from src.tools.personal_tools import _send_message
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.shell_executor import ShellResult

        gate = ProactiveMessageGate(
            enabled=True,
            quiet_hours_start="23:00",
            quiet_hours_end="08:00",
            allow_urgent_bypass=False,
            clock=lambda: datetime(2026, 1, 1, 23, 30),  # in quiet hours
        )
        log = _FakeMutationLog()

        async def _noop_shell(_):
            return ShellResult(stdout="", stderr="", return_code=0)

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd="/tmp",
            services={
                "channel_manager": object(),  # not consulted (gate denies first)
                "proactive_message_gate": gate,
                "mutation_log": log,
            },
            auth_level=2,
            chat_id="c1",
            runtime_profile="inner_tick",
        )
        req = ToolExecutionRequest(
            name="send_message",
            arguments={"target": "kevin_qq", "content": "hi"},
        )
        result = await _send_message(req, ctx)
        assert result.success is False
        assert result.payload["gate_decision"] == "defer"

        decisions = _by_type(log.records, MutationType.PROACTIVE_MESSAGE_DECISION)
        assert len(decisions) == 1
        _, payload, _ = decisions[0]
        assert payload["decision"] == "defer"
        assert "quiet_hours" in payload["reason"]

    @pytest.mark.asyncio
    async def test_urgent_bypass_decision_recorded_with_flag(self):
        from datetime import datetime
        from src.core.proactive_message_gate import ProactiveMessageGate
        from src.tools.personal_tools import _send_message
        from src.tools.types import ToolExecutionContext, ToolExecutionRequest
        from src.tools.shell_executor import ShellResult

        gate = ProactiveMessageGate(
            enabled=True,
            quiet_hours_start="23:00",
            quiet_hours_end="08:00",
            allow_urgent_bypass=True,
            urgent_bypass_categories=["reminder_due"],
            clock=lambda: datetime(2026, 1, 1, 23, 30),
        )
        log = _FakeMutationLog()

        class _QQ:
            sent: list = []
            async def send_private_message(self, qq, content):
                self.sent.append(content)

        class _CM:
            def __init__(self):
                self.qq = _QQ()
            def get_adapter(self, name):
                return self.qq if name == "qq" else None

        async def _noop_shell(_):
            return ShellResult(stdout="", stderr="", return_code=0)

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd="/tmp",
            services={
                "channel_manager": _CM(),
                "owner_qq_id": "12345",
                "proactive_message_gate": gate,
                "mutation_log": log,
            },
            auth_level=2,
            chat_id="c1",
            runtime_profile="inner_tick",
        )
        req = ToolExecutionRequest(
            name="send_message",
            arguments={
                "target": "kevin_qq",
                "content": "提醒",
                "category": "reminder_due",
            },
        )
        result = await _send_message(req, ctx)
        assert result.success is True

        decisions = _by_type(log.records, MutationType.PROACTIVE_MESSAGE_DECISION)
        assert len(decisions) == 1
        _, payload, _ = decisions[0]
        assert payload["decision"] == "allow"
        assert payload["bypassed"] is True
        assert payload["category"] == "reminder_due"
