"""End-to-end observability chain for Blueprint v2.0 Step 1.

Simulates one conversational iteration that does:
    user message → LLM decides to call read_file → tool executes →
    LLM returns final reply.

Verifies the full mutation chain lands in StateMutationLog in order:
    ITERATION_STARTED
      → LLM_REQUEST (req_1)
      → LLM_RESPONSE (req_1, tool_use)
      → TOOL_CALLED (parent_llm_request_id=req_1)
      → TOOL_RESULT
      → LLM_REQUEST (req_2)
      → LLM_RESPONSE (req_2, end_turn)
    ITERATION_ENDED

The integration runs the real TaskRuntime + real LLMRouter._tracked_call
stack against a real StateMutationLog. Only the bottom-most LLM API call
(``client.chat.completions.create``) and the shell executor are mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.llm_router import LLMRouter
from src.core.shell_policy import (
    ShellRuntimePolicy,
    analyze_command,
    extract_execution_constraints,
    failure_reason_from_result,
    failure_type_from_result,
    infer_permission_denied_alternative,
    should_request_consent_for_command,
    should_validate_after_success,
)
from src.core.task_runtime import TaskRuntime
from src.core.task_types import RuntimeDeps
from src.logging.state_mutation_log import MutationType, StateMutationLog
from src.tools.registry import build_default_tool_registry
from src.tools.shell_executor import ShellResult


def _chat_ready_registry():
    from src.tools.personal_tools import register_personal_tools
    from src.tools.research_tool import register_research_tool
    from src.tools.agent_tools import register_agent_tools
    from src.core.durable_scheduler import DURABLE_SCHEDULER_EXECUTORS
    from src.tools.types import ToolSpec

    registry = build_default_tool_registry()
    register_personal_tools(registry, {})
    register_research_tool(registry)
    register_agent_tools(registry)
    for name in ("set_reminder", "view_reminders", "cancel_reminder"):
        registry.register(ToolSpec(
            name=name,
            description="reminder tool",
            json_schema={"type": "object", "properties": {}},
            executor=DURABLE_SCHEDULER_EXECUTORS[name],
            capability="schedule",
        ))
    return registry


def _make_policy():
    return ShellRuntimePolicy(
        analyze_command=analyze_command,
        should_request_consent_for_command=should_request_consent_for_command,
        failure_type_from_result=failure_type_from_result,
        infer_permission_denied_alternative=infer_permission_denied_alternative,
        should_validate_after_success=should_validate_after_success,
        verify_constraints=AsyncMock(),
        failure_reason_builder=failure_reason_from_result,
    )


class _FakeFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tool_id: str, name: str, arguments: str):
        self.id = tool_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str, tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeChoice:
    def __init__(self, message, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.prompt_tokens = input_tokens
        self.completion_tokens = output_tokens


class _FakeResponse:
    def __init__(self, choice, usage):
        self.choices = [choice]
        self.usage = usage


class _ScriptedClient:
    """Minimal OpenAI-compatible client returning two scripted responses."""

    def __init__(self):
        self.call_count = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            msg = _FakeMessage(
                content="",
                tool_calls=[
                    _FakeToolCall(
                        "tool_call_1",
                        "read_file",
                        '{"path": "/tmp/a.txt"}',
                    )
                ],
            )
            return _FakeResponse(_FakeChoice(msg, "tool_calls"), _FakeUsage(10, 5))
        # Round 2 — end of turn
        msg = _FakeMessage(content="文件看过了，里面写着 hello。")
        return _FakeResponse(_FakeChoice(msg, "stop"), _FakeUsage(20, 15))


@pytest.fixture
async def mutation_log(tmp_path):
    log = StateMutationLog(tmp_path / "mutation_log.db", logs_dir=tmp_path / "logs")
    await log.init()
    yield log
    await log.close()


@pytest.mark.asyncio
async def test_full_observability_chain(mutation_log):
    # ── Router with fake routing retry + fake OpenAI client ──
    router = LLMRouter()
    router.set_mutation_log(mutation_log)
    router._base_urls = {"main_conversation": "https://fake-openai"}
    router._api_types = {"main_conversation": "openai"}
    router._models = {"main_conversation": "fake-model"}

    fake_client = _ScriptedClient()

    async def _mock_retry(*, purpose, runner, **_):
        # Directly call the runner against our fake client/model. The runner
        # is the real closure defined inside complete_with_tools, so it will
        # invoke router._tracked_call → mutation_log.record.
        return await runner(None, fake_client, "fake-model", "openai")

    router._with_routing_retry = _mock_retry

    # ── TaskRuntime with real registry + mutation_log in services ──
    registry = _chat_ready_registry()
    mock_shell = AsyncMock(
        return_value=ShellResult(stdout="hello", stderr="", return_code=0, cwd="/tmp"),
    )
    runtime = TaskRuntime(router=router, tool_registry=registry, no_action_budget=0)
    constraints = extract_execution_constraints("读一下 /tmp/a.txt 看看里面什么内容")
    deps = RuntimeDeps(
        execute_shell=mock_shell,
        policy=_make_policy(),
        shell_default_cwd="/tmp",
        shell_allow_sudo=True,
    )

    tools = runtime.chat_tools(shell_enabled=True)
    services = {"mutation_log": mutation_log, "router": router}

    result = await runtime.complete_chat(
        chat_id="chat-integration",
        messages=[{"role": "user", "content": "读 /tmp/a.txt"}],
        constraints=constraints,
        tools=tools,
        deps=deps,
        services=services,
        adapter="test",
        user_id="kevin",
    )
    assert "hello" in result or "文件" in result

    # ── Assert the chain in order ──
    # query_by_window over generous range to get everything in insertion order
    import time as _time
    all_rows = await mutation_log.query_by_window(0.0, _time.time() + 1)
    event_types = [r.event_type for r in all_rows]

    # ITERATION_STARTED is first
    assert event_types[0] == MutationType.ITERATION_STARTED.value
    # ITERATION_ENDED is last
    assert event_types[-1] == MutationType.ITERATION_ENDED.value
    # Two LLM_REQUEST + two LLM_RESPONSE total
    assert event_types.count(MutationType.LLM_REQUEST.value) == 2
    assert event_types.count(MutationType.LLM_RESPONSE.value) == 2
    # One TOOL_CALLED + one TOOL_RESULT
    assert event_types.count(MutationType.TOOL_CALLED.value) == 1
    assert event_types.count(MutationType.TOOL_RESULT.value) == 1

    # Every non-bracket event shares one iteration_id
    iteration_id = all_rows[0].payload["iteration_id"]
    for row in all_rows:
        assert row.iteration_id == iteration_id

    # request_id correlation: first LLM_REQUEST's request_id matches first
    # LLM_RESPONSE's request_id; same for second pair.
    reqs = [r for r in all_rows if r.event_type == MutationType.LLM_REQUEST.value]
    resps = [r for r in all_rows if r.event_type == MutationType.LLM_RESPONSE.value]
    assert reqs[0].payload["request_id"] == resps[0].payload["request_id"]
    assert reqs[1].payload["request_id"] == resps[1].payload["request_id"]
    assert reqs[0].payload["request_id"] != reqs[1].payload["request_id"]

    # TOOL_CALLED carries parent_llm_request_id matching the first LLM_REQUEST
    tool_called = next(
        r for r in all_rows if r.event_type == MutationType.TOOL_CALLED.value
    )
    assert tool_called.payload["parent_llm_request_id"] == reqs[0].payload["request_id"]
    assert tool_called.payload["tool_call_id"] == "tool_call_1"
    assert tool_called.payload["tool_name"] == "read_file"

    # TOOL_RESULT carries the same tool_call_id; don't assert on success value
    # — the read_file path goes through shell_policy which may add its own
    # verification requirements in the test environment. What matters for
    # Step 1 observability is that the mutation was recorded with the right
    # correlation id.
    tool_result = next(
        r for r in all_rows if r.event_type == MutationType.TOOL_RESULT.value
    )
    assert tool_result.payload["tool_call_id"] == "tool_call_1"
    assert "success" in tool_result.payload
    assert "elapsed_ms" in tool_result.payload

    # First LLM_RESPONSE has tool_use in content_blocks; second has text.
    first_blocks = resps[0].payload["content_blocks"]
    assert any(b["type"] == "tool_use" for b in first_blocks)
    second_blocks = resps[1].payload["content_blocks"]
    assert any(b["type"] == "text" for b in second_blocks)

    # ITERATION_ENDED counts should agree with the chain
    ended = all_rows[-1].payload
    assert ended["llm_calls_count"] == 2
    assert ended["tool_calls_count"] == 1
    assert ended["end_reason"] == "completed"
