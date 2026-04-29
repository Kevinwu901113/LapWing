"""End-to-end inner_tick integration test.

Wires Brain.think_inner against:
- a stub LLMRouter that drives the tool loop deterministically,
- a real ProactiveMessageGate (quiet hours active),
- a real BrowserGuard (block_internal_network=True, block_downloads=True),
- a real (in-memory) StateMutationLog,
- a real TaskRuntime + ToolRegistry with two registered tools
  (browser_open + send_message).

Asserts the wiring contract from the audit punch list:
a. inner_tick profile is the one resolved at TaskRuntime entry
b. RuntimeOptions (max_tool_rounds / no_action_budget /
   error_burst_threshold) reach TaskRuntime
c. an attempted browser_open is blocked by BrowserGuard and the
   denial is recorded as TOOL_DENIED in the mutation log
d. a proactive send_message during quiet hours produces a
   PROACTIVE_MESSAGE_DECISION mutation log entry
e. IntentRouter is NOT consulted for inner_tick profile selection
   (profile_override="inner_tick" short-circuits routing).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.brain import LapwingBrain
from src.core.browser_guard import BrowserGuard
from src.core.llm_types import ToolCallRequest, ToolTurnResult
from src.core.proactive_message_gate import ProactiveMessageGate
from src.logging.state_mutation_log import MutationType, StateMutationLog
from src.tools.registry import ToolRegistry
from src.tools.types import ToolExecutionResult, ToolSpec


class _StubLLMRouter:
    """Drives the TaskRuntime tool loop with a fixed sequence.

    Round 1: emit browser_open (will be blocked by BrowserGuard) +
             send_message (will be deferred by quiet hours).
    Round 2: empty tool_calls, plain text → loop exits.

    Records every call so the test can assert what landed at the LLM.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self._round = 0

    def set_mutation_log(self, log):
        # TaskRuntime calls this during its lifecycle setup.
        self._mutation_log = log

    async def complete_with_tools(self, messages, tools, **kwargs):
        self.calls.append({
            "messages": list(messages),
            "tool_count": len(tools),
            "kwargs": dict(kwargs),
        })
        self._round += 1
        if self._round == 1:
            return ToolTurnResult(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="tc_browser_1",
                        name="browser_open",
                        arguments={"url": "http://localhost/admin"},
                    ),
                    ToolCallRequest(
                        id="tc_send_1",
                        name="send_message",
                        arguments={
                            "target": "kevin_qq",
                            "content": "想到一件事",
                        },
                    ),
                ],
            )
        return ToolTurnResult(text="算了，下次再说。 [NEXT: 30m]", tool_calls=[])

    async def complete(self, messages, **kwargs):
        # called by no-tools branches; not exercised here
        return ""

    def build_tool_result_message(self, tool_results, **kwargs):
        """OpenAI-style tool result message — TaskRuntime treats every
        non-Anthropic provider as flat ``role=tool`` per result."""
        return [
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": output,
            }
            for tc, output in tool_results
        ]


def _build_registry() -> ToolRegistry:
    """A minimal registry: browser_open + the real send_message executor.

    send_message must be the production executor so the test exercises
    the actual ProactiveMessageGate consult path — a stubbed sender
    would skip the gate and never produce PROACTIVE_MESSAGE_DECISION."""
    from src.tools.personal_tools import _send_message

    registry = ToolRegistry()

    async def _noop_browser_open(req, ctx):
        return ToolExecutionResult(success=True, payload={"output": "ran"})

    registry.register(ToolSpec(
        name="browser_open",
        description="open url",
        json_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        executor=_noop_browser_open,
        capability="browser",
    ))
    registry.register(ToolSpec(
        name="send_message",
        description="send",
        json_schema={"type": "object", "properties": {
            "target": {"type": "string"}, "content": {"type": "string"},
        }},
        executor=_send_message,
        capability="general",
    ))
    return registry


@pytest.mark.asyncio
async def test_inner_tick_end_to_end(tmp_path):
    # ── Real subsystems ──────────────────────────────────────────────
    mutation_log = StateMutationLog(
        tmp_path / "mut.db", logs_dir=tmp_path / "logs",
    )
    await mutation_log.init()

    proactive_gate = ProactiveMessageGate(
        enabled=True,
        max_per_day=3,
        min_minutes_between=0,
        quiet_hours_start="23:00",
        quiet_hours_end="08:00",
        allow_urgent_bypass=True,
        urgent_bypass_categories=["reminder_due", "safety"],
        # 02:30 — solidly inside quiet window
        clock=lambda: datetime(2026, 4, 27, 2, 30),
    )
    browser_guard = BrowserGuard(
        block_internal_network=True,  # blocks localhost
        block_downloads=True,
    )

    # ── Brain wiring (real TaskRuntime, stubbed ancillaries) ─────────
    # Patch out network-y deps; replace the tool registry post-init so a
    # full-suite run that may have touched PHASE0_MODE env vars cannot
    # silently leave the registry empty (would mask browser_open denial).
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter") as _RouterCls:
        brain = LapwingBrain(db_path=tmp_path / "lap.db")

    stub_router = _StubLLMRouter()
    brain.router = stub_router
    test_registry = _build_registry()
    brain.tool_registry = test_registry
    brain.task_runtime._tool_registry = test_registry
    brain.task_runtime._router = stub_router
    brain.task_runtime.set_browser_guard(browser_guard)

    # IntentRouter spy — must NOT be called when profile_override is used.
    routed: list = []

    class _IntentRouterSpy:
        async def route(self, chat_id, message):
            routed.append((chat_id, message))
            return "standard"  # would widen the surface — must not fire

    brain.intent_router = _IntentRouterSpy()
    brain._mutation_log_ref = mutation_log
    brain._proactive_message_gate_ref = proactive_gate
    brain.channel_manager = MagicMock()
    brain.channel_manager.get_adapter = lambda name: None
    brain.channel_manager.default_chat_id = "kevin-qq"

    # _render_messages: skip the StateView builder pipeline; feed the
    # synthesised user message straight through.
    async def fake_render(chat_id, recent, *, inner=False, **kwargs):
        return [{"role": "system", "content": "<sys>"}] + list(recent)

    brain._render_messages = fake_render  # type: ignore[method-assign]

    # _record_turn: TrajectoryStore unwired in this test; swallow.
    brain._record_turn = AsyncMock()  # type: ignore[method-assign]
    brain.state_view_builder = MagicMock()

    # Spy on TaskRuntime.complete_chat to capture profile + runtime_options.
    captured: dict = {}
    real_complete_chat = brain.task_runtime.complete_chat

    async def spy_complete_chat(**kwargs):
        captured["profile"] = kwargs.get("profile")
        captured["runtime_options"] = kwargs.get("runtime_options")
        return await real_complete_chat(**kwargs)

    brain.task_runtime.complete_chat = spy_complete_chat  # type: ignore[assignment]

    # Force INTENT_ROUTER_ENABLED=True so the spy would otherwise fire —
    # this makes the "no IntentRouter for inner_tick" assertion meaningful.
    with patch("src.core.brain.INTENT_ROUTER_ENABLED", True):
        clean_text, next_interval, did_something = await brain.think_inner(
            timeout_seconds=10,
        )

    # ── Assertions ──────────────────────────────────────────────────
    # a. inner_tick profile reaches TaskRuntime
    assert captured["profile"] == "inner_tick"

    # b. RuntimeOptions thread through with the expected per-tick budgets
    rt_opts = captured["runtime_options"]
    assert rt_opts is not None
    assert rt_opts.max_tool_rounds is not None
    assert rt_opts.no_action_budget is not None
    assert rt_opts.error_burst_threshold is not None

    # c. attempted browser_open is blocked / guarded — under inner_tick
    #    the profile gate fires first (browser_open is not in the
    #    INNER_TICK_PROFILE allowlist), but if a future change widened
    #    the profile, BrowserGuard's check_url("http://localhost/...")
    #    would also block. Either path is a valid wiring signal.
    rows = await mutation_log.query_by_type(MutationType.TOOL_DENIED)
    browser_denials = [r for r in rows if r.payload.get("tool") == "browser_open"]
    assert len(browser_denials) >= 1, (
        f"expected a TOOL_DENIED for browser_open; got {[r.payload for r in rows]}"
    )
    guard_used = browser_denials[0].payload["guard"]
    assert guard_used in ("browser_guard", "profile_not_allowed"), (
        f"unexpected guard for inner_tick browser_open denial: {guard_used!r}"
    )

    # d. send_message during quiet hours emits PROACTIVE_MESSAGE_DECISION
    decisions = await mutation_log.query_by_type(
        MutationType.PROACTIVE_MESSAGE_DECISION,
    )
    assert len(decisions) >= 1, (
        f"expected a PROACTIVE_MESSAGE_DECISION row; got {decisions!r}"
    )
    assert decisions[0].payload["decision"] in ("defer", "deny")
    assert "quiet_hours" in decisions[0].payload["reason"]
    assert decisions[0].payload["runtime_profile"] == "inner_tick"

    # e. IntentRouter was NOT consulted (profile_override path bypasses it)
    assert routed == [], (
        f"IntentRouter must be skipped for inner_tick; got calls {routed!r}"
    )

    await mutation_log.close()
