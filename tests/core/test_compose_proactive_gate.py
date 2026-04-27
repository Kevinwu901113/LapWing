"""compose_proactive must engage the ProactiveMessageGate.

Until this commit, ``Brain.compose_proactive`` called
``TaskRuntime.complete_chat`` with no services dict and no proactive
flag, so the send_message executor saw the call as direct-reply
territory and let it through unbounded. That defeats the rate limit
and quiet-hours contract from the gate.

The fix: compose_proactive must
1. build the same services dict ``_complete_chat`` builds (so the gate
   reference reaches send_message), and
2. set ``services["proactive_send_active"] = True`` for the duration of
   the call so the executor knows the context is autonomous.

Tests:
- compose_proactive passes ``proactive_send_active`` through TaskRuntime
- 4th proactive send within 24h is denied when max_per_day=3
- quiet hours defer a non-urgent send invoked under proactive context
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def brain(tmp_path):
    with patch("src.core.brain.AuthManager"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.build_default_tool_registry"), \
         patch("src.core.brain.TaskRuntime"):
        from src.core.brain import LapwingBrain
        b = LapwingBrain(db_path=tmp_path / "x.db")
    return b


async def test_compose_proactive_marks_services_proactive(brain):
    """compose_proactive must stamp services['proactive_send_active'] so
    the send_message executor consults the ProactiveMessageGate even
    when the runtime profile is not 'inner_tick'."""
    from src.core.proactive_message_gate import ProactiveMessageGate

    gate = ProactiveMessageGate(enabled=True)
    brain._proactive_message_gate_ref = gate
    brain.channel_manager = MagicMock()
    brain.channel_manager.default_chat_id = "kevin-qq"

    captured: dict = {}

    async def spy_complete_chat(**kwargs):
        captured["services"] = kwargs.get("services") or {}
        return "msg"

    async def fake_render(chat_id, recent, *, inner=False, **kwargs):
        return list(recent)

    brain.task_runtime.chat_tools = lambda **kwargs: [
        {"function": {"name": "send_message"}}
    ]
    brain.task_runtime.complete_chat = spy_complete_chat
    brain._render_messages = fake_render  # type: ignore[method-assign]

    await brain.compose_proactive(
        purpose="兴趣分享",
        context_prompt="主动发个消息",
        tools=["send_message"],
        chat_id="kevin-qq",
    )

    services = captured["services"]
    assert services.get("proactive_send_active") is True, (
        "compose_proactive must flag services['proactive_send_active']=True "
        "so send_message engages the proactive gate"
    )
    assert services.get("proactive_message_gate") is gate, (
        "compose_proactive must propagate the proactive_message_gate ref "
        "(via _build_services) so the executor can call evaluate()"
    )


@pytest.mark.asyncio
async def test_compose_proactive_fourth_send_denied_at_max_per_day_3():
    """End-to-end: under proactive_send_active, the 4th send within a
    rolling 24h window is denied when max_per_day=3.

    This simulates the compose_proactive call path: a send_message tool
    invocation with services flagged as proactive."""
    from src.core.proactive_message_gate import ProactiveMessageGate
    from src.tools.personal_tools import _send_message
    from src.tools.shell_executor import ShellResult
    from src.tools.types import ToolExecutionContext, ToolExecutionRequest

    gate = ProactiveMessageGate(
        enabled=True,
        max_per_day=3,
        min_minutes_between=0,
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",  # disabled
        clock=lambda: datetime(2026, 4, 27, 14, 0),
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

    cm = _CM()

    async def _noop_shell(_):
        return ShellResult(stdout="", stderr="", return_code=0)

    def _ctx() -> ToolExecutionContext:
        return ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd="/tmp",
            services={
                "channel_manager": cm,
                "owner_qq_id": "12345",
                "proactive_message_gate": gate,
                "proactive_send_active": True,  # compose_proactive sets this
            },
            auth_level=2,
            chat_id="kevin-qq",
            runtime_profile="compose_proactive",
        )

    req = ToolExecutionRequest(
        name="send_message",
        arguments={"target": "kevin_qq", "content": "ping"},
    )
    # First 3 sends succeed
    for _ in range(3):
        result = await _send_message(req, _ctx())
        assert result.success is True

    # 4th send — daily cap reached
    result = await _send_message(req, _ctx())
    assert result.success is False
    assert result.payload["gate_decision"] == "deny"
    assert "daily_cap_reached" in result.payload["gate_reason"]


@pytest.mark.asyncio
async def test_compose_proactive_quiet_hours_blocks_non_urgent():
    """Quiet hours must defer a non-urgent compose_proactive send."""
    from src.core.proactive_message_gate import ProactiveMessageGate
    from src.tools.personal_tools import _send_message
    from src.tools.shell_executor import ShellResult
    from src.tools.types import ToolExecutionContext, ToolExecutionRequest

    gate = ProactiveMessageGate(
        enabled=True,
        quiet_hours_start="23:00",
        quiet_hours_end="08:00",
        allow_urgent_bypass=True,
        urgent_bypass_categories=["reminder_due", "safety"],
        clock=lambda: datetime(2026, 4, 27, 23, 30),  # inside quiet window
    )

    sent: list = []

    class _QQ:
        async def send_private_message(self, qq, content):
            sent.append(content)

    class _CM:
        def get_adapter(self, name):
            return _QQ() if name == "qq" else None

    async def _noop_shell(_):
        return ShellResult(stdout="", stderr="", return_code=0)

    ctx = ToolExecutionContext(
        execute_shell=_noop_shell,
        shell_default_cwd="/tmp",
        services={
            "channel_manager": _CM(),
            "owner_qq_id": "12345",
            "proactive_message_gate": gate,
            "proactive_send_active": True,
        },
        auth_level=2,
        chat_id="kevin-qq",
        runtime_profile="compose_proactive",
    )
    req = ToolExecutionRequest(
        name="send_message",
        arguments={"target": "kevin_qq", "content": "夜里的随口想到"},
    )
    result = await _send_message(req, ctx)
    assert result.success is False
    assert result.payload["gate_decision"] == "defer"
    assert "quiet_hours" in result.payload["gate_reason"]
    assert sent == [], "no message should leave the channel during quiet hours"
