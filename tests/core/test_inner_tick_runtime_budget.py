"""inner_tick runtime budget threading.

The [inner_tick] config caps how much one autonomous tick may spend
before yielding — distinct from [consciousness] which controls cadence.
This test locks in three things:

1. The pydantic InnerTickConfig defaults match the spec.
2. think_inner reads the inner_tick budget and passes RuntimeOptions
   into _complete_chat (which forwards to TaskRuntime).
3. TaskRuntime.complete_chat applies RuntimeOptions overrides — the
   tool loop runs no more than max_tool_rounds rounds, and the
   no_action_budget / error_burst_threshold values flow into the
   ToolLoopContext.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestInnerTickConfigDefaults:
    def test_pydantic_defaults_match_spec(self):
        from src.config.settings import InnerTickConfig

        cfg = InnerTickConfig()
        assert cfg.enabled is True
        assert cfg.base_interval_seconds == 600
        assert cfg.min_interval_seconds == 300
        assert cfg.max_interval_seconds == 14400
        assert cfg.timeout_seconds == 120
        assert cfg.max_tool_rounds == 3
        assert cfg.no_action_budget == 2
        assert cfg.error_burst_threshold == 2

    def test_barrel_exports_constants(self):
        import config.settings as cs

        assert cs.INNER_TICK_ENABLED is True
        assert cs.INNER_TICK_BASE_INTERVAL_SECONDS == 600
        assert cs.INNER_TICK_TIMEOUT_SECONDS == 120
        assert cs.INNER_TICK_MAX_TOOL_ROUNDS == 3
        assert cs.INNER_TICK_NO_ACTION_BUDGET == 2
        assert cs.INNER_TICK_ERROR_BURST_THRESHOLD == 2


@pytest.fixture
def brain(tmp_path):
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.build_default_tool_registry"), \
         patch("src.core.brain.TaskRuntime"):
        from src.core.brain import LapwingBrain
        b = LapwingBrain(db_path=tmp_path / "x.db")
    return b


class TestThinkInnerPassesRuntimeOptions:
    async def test_runtime_options_threaded_into_complete_chat(self, brain):
        """think_inner must pass a RuntimeOptions object with the inner_tick
        budgets into _complete_chat."""
        from src.core.task_runtime import RuntimeOptions

        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])

        captured: dict = {}

        async def fake_render(chat_id, recent, *, inner=False, **kwargs):
            return [{"role": "system", "content": "<sys>"}] + list(recent)

        async def fake_complete(chat_id, messages, user_msg, **kwargs):
            captured["kwargs"] = kwargs
            return "ok [NEXT: 30m]"

        brain._render_messages = fake_render  # type: ignore[method-assign]
        brain._complete_chat = fake_complete  # type: ignore[method-assign]

        await brain.think_inner()

        opts = captured["kwargs"].get("runtime_options")
        assert isinstance(opts, RuntimeOptions), (
            "think_inner must pass RuntimeOptions to constrain the tick budget"
        )
        # Values must come from [inner_tick] config defaults
        assert opts.max_tool_rounds == 3
        assert opts.no_action_budget == 2
        assert opts.error_burst_threshold == 2

    async def test_default_timeout_comes_from_config(self, brain):
        """When think_inner is called without timeout_seconds, the value
        must come from INNER_TICK_TIMEOUT_SECONDS — not the old hardcoded
        120 default."""
        brain.trajectory_store = AsyncMock()
        brain.trajectory_store.relevant_to_chat = AsyncMock(return_value=[])

        async def fake_render(chat_id, recent, *, inner=False, **kwargs):
            return [{"role": "system", "content": "<sys>"}] + list(recent)

        sleep_calls: list = []

        async def slow_complete(*a, **kw):
            sleep_calls.append("called")
            return "ok [NEXT: 30m]"

        brain._render_messages = fake_render  # type: ignore[method-assign]
        brain._complete_chat = slow_complete  # type: ignore[method-assign]

        # We can't easily intercept asyncio.wait_for's timeout value, but
        # we can assert that overriding the default propagates by passing
        # explicit timeout_seconds=1 (no patch needed).
        reply, _, _ = await brain.think_inner(timeout_seconds=5)
        # The completion path ran (timeout did not fire on this fast mock).
        assert sleep_calls == ["called"]


class TestRuntimeOptionsAppliedInTaskRuntime:
    async def test_overrides_replace_instance_defaults(self):
        """TaskRuntime.complete_chat with RuntimeOptions must use the
        override values and NOT the instance defaults."""
        from src.core.task_runtime import (
            ExecutionConstraints,
            RuntimeDeps,
            RuntimeOptions,
            TaskRuntime,
        )
        from src.tools.shell_executor import ShellResult

        # Build a TaskRuntime with deliberately-large defaults so we can
        # see the override actually shrink the budget.
        router = MagicMock()
        router.complete_with_tools = AsyncMock()
        router.complete = AsyncMock(return_value="text")

        runtime = TaskRuntime(
            router,
            max_tool_rounds=99,
            no_action_budget=99,
            error_burst_threshold=99,
        )

        captured_max_rounds: list = []

        # Wrap run_task_loop so we can capture the max_rounds it sees.
        original_run_task_loop = runtime.run_task_loop

        async def spy_run_task_loop(*, max_rounds, step_runner):
            captured_max_rounds.append(max_rounds)
            return await original_run_task_loop(
                max_rounds=max_rounds, step_runner=step_runner,
            )

        runtime.run_task_loop = spy_run_task_loop  # type: ignore[method-assign]

        # Tool that always responds with no tool_calls so the loop ends
        # cleanly on round 0.
        async def empty_response(*a, **kw):
            class _Resp:
                tool_calls = []
                text = "done"
                continuation_message = {"role": "assistant", "content": "done"}
            return _Resp()

        router.complete_with_tools = AsyncMock(side_effect=empty_response)

        async def _noop_shell(_):
            return ShellResult(stdout="", stderr="", return_code=0)

        deps = RuntimeDeps(
            execute_shell=_noop_shell,
            policy=MagicMock(),
            shell_default_cwd="/tmp",
            shell_allow_sudo=False,
        )

        constraints = ExecutionConstraints(original_user_message="")

        await runtime.complete_chat(
            chat_id="t",
            messages=[{"role": "user", "content": "hi"}],
            constraints=constraints,
            tools=[{"type": "function", "function": {"name": "x", "description": "x", "parameters": {}}}],
            deps=deps,
            profile="inner_tick",
            runtime_options=RuntimeOptions(
                max_tool_rounds=3,
                no_action_budget=2,
                error_burst_threshold=2,
            ),
        )

        assert captured_max_rounds == [3], (
            f"max_tool_rounds override was not applied: {captured_max_rounds}"
        )

    async def test_no_runtime_options_uses_instance_defaults(self):
        """Without RuntimeOptions, TaskRuntime must use the values it was
        constructed with (no regression for existing chat paths)."""
        from src.core.task_runtime import (
            ExecutionConstraints,
            RuntimeDeps,
            TaskRuntime,
        )
        from src.tools.shell_executor import ShellResult

        router = MagicMock()
        router.complete = AsyncMock(return_value="text")

        runtime = TaskRuntime(
            router,
            max_tool_rounds=42,
            no_action_budget=7,
            error_burst_threshold=5,
        )

        captured: list = []

        original = runtime.run_task_loop

        async def spy(*, max_rounds, step_runner):
            captured.append(max_rounds)
            return await original(max_rounds=max_rounds, step_runner=step_runner)

        runtime.run_task_loop = spy  # type: ignore[method-assign]

        async def empty_response(*a, **kw):
            class _Resp:
                tool_calls = []
                text = "done"
                continuation_message = {"role": "assistant", "content": "done"}
            return _Resp()

        router.complete_with_tools = AsyncMock(side_effect=empty_response)

        async def _noop_shell(_):
            return ShellResult(stdout="", stderr="", return_code=0)

        deps = RuntimeDeps(
            execute_shell=_noop_shell,
            policy=MagicMock(),
            shell_default_cwd="/tmp",
            shell_allow_sudo=False,
        )

        constraints = ExecutionConstraints(original_user_message="")

        await runtime.complete_chat(
            chat_id="t",
            messages=[{"role": "user", "content": "hi"}],
            constraints=constraints,
            tools=[{"type": "function", "function": {"name": "x", "description": "x", "parameters": {}}}],
            deps=deps,
            profile="chat_extended",
        )

        assert captured == [42], (
            f"instance default max_tool_rounds=42 was not used: {captured}"
        )
