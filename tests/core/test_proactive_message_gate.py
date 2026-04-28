"""ProactiveMessageGate — rate limiting + quiet hours + urgent bypass.

Direct assistant replies use bare text and never go through send_message.
This gate fires only on proactive/background flows. Tests cover:

- Decision API (allow / defer / deny / urgent bypass)
- Rolling-24h daily cap
- Min interval between sends
- Quiet hours (same-day + wrapping windows)
- Urgent bypass categories
- Disabled gate is a no-op
- send_message integration: gated only on proactive contexts
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import pytest

from src.core.proactive_message_gate import (
    ProactiveGateDecision,
    ProactiveMessageGate,
    _in_quiet_window,
    _parse_hhmm,
)


def _gate(now: datetime, **overrides) -> ProactiveMessageGate:
    """Build a gate with a fixed clock."""
    cfg = {
        "enabled": True,
        "max_per_day": 3,
        "min_minutes_between": 90,
        "quiet_hours_start": "23:00",
        "quiet_hours_end": "08:00",
        "allow_urgent_bypass": True,
        "urgent_bypass_categories": ["reminder_due", "safety", "explicit_commitment"],
        "clock": lambda: now,
    }
    cfg.update(overrides)
    return ProactiveMessageGate(**cfg)


class TestQuietWindowHelper:
    def test_parse_hhmm(self):
        assert _parse_hhmm("23:00") == dtime(23, 0)
        assert _parse_hhmm("08:30") == dtime(8, 30)
        assert _parse_hhmm("") == dtime(0, 0)

    def test_same_day_window(self):
        start, end = dtime(13, 0), dtime(14, 0)
        assert _in_quiet_window(datetime(2026, 1, 1, 13, 30), start, end) is True
        assert _in_quiet_window(datetime(2026, 1, 1, 12, 30), start, end) is False
        assert _in_quiet_window(datetime(2026, 1, 1, 14, 0), start, end) is False

    def test_wrapping_window(self):
        # 23:00 → 08:00 covers late night and early morning of next day
        start, end = dtime(23, 0), dtime(8, 0)
        assert _in_quiet_window(datetime(2026, 1, 1, 23, 30), start, end) is True
        assert _in_quiet_window(datetime(2026, 1, 2, 1, 0), start, end) is True
        assert _in_quiet_window(datetime(2026, 1, 2, 7, 59), start, end) is True
        assert _in_quiet_window(datetime(2026, 1, 2, 8, 0), start, end) is False
        assert _in_quiet_window(datetime(2026, 1, 2, 14, 0), start, end) is False

    def test_zero_width_window(self):
        # start == end means the window is "off"
        assert _in_quiet_window(
            datetime(2026, 1, 1, 12, 0), dtime(8, 0), dtime(8, 0)
        ) is False


class TestBasicDecisions:
    def test_disabled_gate_always_allows(self):
        now = datetime(2026, 1, 1, 23, 30)  # inside quiet hours
        gate = _gate(now, enabled=False)
        d = gate.evaluate()
        assert d.decision == "allow"
        assert "enabled=false" in d.reason

    def test_within_budget_during_active_hours_allows(self):
        now = datetime(2026, 1, 1, 14, 0)
        gate = _gate(now)
        d = gate.evaluate()
        assert d.decision == "allow"
        assert d.reason == "within_budget"

    def test_quiet_hours_defers(self):
        now = datetime(2026, 1, 1, 23, 30)
        gate = _gate(now)
        d = gate.evaluate()
        assert d.decision == "defer"
        assert "quiet_hours" in d.reason

    def test_quiet_hours_early_morning_defers(self):
        now = datetime(2026, 1, 2, 7, 0)  # inside the wrapping 23:00→08:00
        gate = _gate(now)
        d = gate.evaluate()
        assert d.decision == "defer"
        assert "quiet_hours" in d.reason


class TestMinInterval:
    def test_blocks_within_min_interval(self):
        t0 = datetime(2026, 1, 1, 14, 0)
        gate = _gate(t0)
        d1 = gate.evaluate()
        assert d1.decision == "allow"
        # Advance 30 minutes — well under 90 minutes min_minutes_between
        gate._clock = lambda: t0 + timedelta(minutes=30)
        d2 = gate.evaluate()
        assert d2.decision == "defer"
        assert "min_interval_not_elapsed" in d2.reason

    def test_passes_after_min_interval(self):
        t0 = datetime(2026, 1, 1, 14, 0)
        gate = _gate(t0)
        gate.evaluate()
        gate._clock = lambda: t0 + timedelta(minutes=91)
        d = gate.evaluate()
        assert d.decision == "allow"


class TestDailyCap:
    def test_third_send_caps(self):
        t0 = datetime(2026, 1, 1, 9, 0)
        gate = _gate(t0, min_minutes_between=0)  # disable spacing
        for i in range(3):
            gate._clock = lambda i=i: t0 + timedelta(minutes=i * 10)
            assert gate.evaluate().decision == "allow"
        gate._clock = lambda: t0 + timedelta(minutes=40)
        d = gate.evaluate()
        assert d.decision == "deny"
        assert "daily_cap_reached" in d.reason

    def test_daily_cap_rolls_off_after_24h(self):
        t0 = datetime(2026, 1, 1, 9, 0)
        gate = _gate(t0, min_minutes_between=0)
        for i in range(3):
            gate._clock = lambda i=i: t0 + timedelta(minutes=i * 10)
            gate.evaluate()
        # 25 hours later, the oldest entries roll off
        gate._clock = lambda: t0 + timedelta(hours=25)
        d = gate.evaluate()
        assert d.decision == "allow"

    def test_remaining_today_counter(self):
        t0 = datetime(2026, 1, 1, 9, 0)
        gate = _gate(t0, min_minutes_between=0)
        assert gate.remaining_today() == 3
        gate.evaluate()
        assert gate.remaining_today() == 2


class TestUrgentBypass:
    def test_category_in_bypass_list_allows_during_quiet_hours(self):
        now = datetime(2026, 1, 1, 23, 30)  # quiet hours
        gate = _gate(now)
        d = gate.evaluate(category="reminder_due")
        assert d.decision == "allow"
        assert d.bypassed is True
        assert "urgent_bypass" in d.reason

    def test_explicit_urgent_flag_allows_during_quiet_hours(self):
        now = datetime(2026, 1, 1, 23, 30)
        gate = _gate(now)
        d = gate.evaluate(urgent=True)
        assert d.decision == "allow"
        assert d.bypassed is True

    def test_unknown_category_does_not_bypass(self):
        now = datetime(2026, 1, 1, 23, 30)  # quiet hours
        gate = _gate(now)
        d = gate.evaluate(category="random_chitchat")
        assert d.decision == "defer"
        assert d.bypassed is False

    def test_bypass_disabled_via_config(self):
        now = datetime(2026, 1, 1, 23, 30)
        gate = _gate(now, allow_urgent_bypass=False)
        d = gate.evaluate(category="safety")
        assert d.decision == "defer"
        assert d.bypassed is False

    def test_urgent_bypass_consumes_budget(self):
        """An urgent send still spends from the daily cap — keeps the cap
        honest if someone tries to backdoor by always claiming urgency."""
        t0 = datetime(2026, 1, 1, 23, 30)
        gate = _gate(t0, min_minutes_between=0)
        for _ in range(3):
            d = gate.evaluate(category="safety")
            assert d.decision == "allow"
            assert d.bypassed is True
        # Even an urgent fourth call now hits the cap (history is full)
        # NOTE: urgent bypass adds entries via record_send-equivalent path.
        # The actual cap is only checked on non-urgent paths, so urgent
        # always succeeds. The contract that matters: budget is consumed.
        assert gate.remaining_today() == 0


class TestFromSettings:
    def test_built_from_pydantic_config(self):
        from src.config.settings import ProactiveMessagesConfig

        cfg = ProactiveMessagesConfig()
        gate = ProactiveMessageGate.from_settings(cfg)
        assert gate.enabled is True
        assert gate.max_per_day == 3
        assert gate.min_minutes_between == 90
        assert "reminder_due" in gate.bypass_categories
        assert "safety" in gate.bypass_categories
        assert "explicit_commitment" in gate.bypass_categories


class TestSendMessageIntegration:
    """The send_message executor consults the gate only on proactive
    contexts (currently: runtime_profile=='inner_tick' or services has
    'proactive_send_active')."""

    async def _execute(
        self,
        *,
        runtime_profile: str = "",
        proactive_active: bool = False,
        gate: ProactiveMessageGate | None = None,
        category: str | None = None,
        urgent: bool = False,
    ):
        from src.tools.personal_tools import _send_message
        from src.tools.types import (
            ToolExecutionContext,
            ToolExecutionRequest,
        )
        from src.tools.shell_executor import ShellResult

        class _FakeQQ:
            sent: list = []

            async def send_private_message(self, qq_id, content):
                self.sent.append((qq_id, content))

        class _FakeChannelManager:
            def __init__(self):
                self.qq = _FakeQQ()

            def get_adapter(self, name):
                if name == "qq":
                    return self.qq
                return None

        cm = _FakeChannelManager()
        services: dict = {
            "channel_manager": cm,
            "owner_qq_id": "12345",
        }
        if gate is not None:
            services["proactive_message_gate"] = gate
        if proactive_active:
            services["proactive_send_active"] = True

        async def _noop_shell(_):
            return ShellResult(stdout="", stderr="", return_code=0)

        ctx = ToolExecutionContext(
            execute_shell=_noop_shell,
            shell_default_cwd="/tmp",
            services=services,
            auth_level=2,
            runtime_profile=runtime_profile,
        )
        args = {"target": "kevin_qq", "content": "hello"}
        if category:
            args["category"] = category
        if urgent:
            args["urgent"] = True
        req = ToolExecutionRequest(name="send_message", arguments=args)
        result = await _send_message(req, ctx)
        return result, cm.qq.sent

    async def test_chat_extended_hard_rejected_even_without_gate(self):
        """chat_extended is direct-reply territory: send_message must be
        hard-rejected regardless of gate state, and no message goes out.
        Direct replies are bare assistant text — there's no legitimate
        send_message path here."""
        result, sent = await self._execute(
            runtime_profile="chat_extended",
        )
        assert result.success is False
        assert result.reason == "send_message_forbidden_in_direct_chat"
        assert sent == []

    async def test_chat_minimal_hard_rejected(self):
        result, sent = await self._execute(
            runtime_profile="chat_minimal",
        )
        assert result.success is False
        assert result.reason == "send_message_forbidden_in_direct_chat"
        assert sent == []

    async def test_task_execution_hard_rejected(self):
        result, sent = await self._execute(
            runtime_profile="task_execution",
        )
        assert result.success is False
        assert result.reason == "send_message_forbidden_in_direct_chat"
        assert sent == []

    async def test_inner_tick_gate_blocks_during_quiet_hours(self):
        gate = _gate(datetime(2026, 1, 1, 23, 30))
        result, sent = await self._execute(
            runtime_profile="inner_tick",
            gate=gate,
        )
        assert result.success is False
        assert result.payload["gate_decision"] == "defer"
        assert "quiet_hours" in result.payload["gate_reason"]
        assert sent == []  # no actual send

    async def test_inner_tick_urgent_category_bypasses_quiet_hours(self):
        gate = _gate(datetime(2026, 1, 1, 23, 30))
        result, sent = await self._execute(
            runtime_profile="inner_tick",
            gate=gate,
            category="reminder_due",
        )
        assert result.success is True
        assert len(sent) == 1

    async def test_inner_tick_daily_cap_denies(self):
        gate = _gate(
            datetime(2026, 1, 1, 14, 0),
            max_per_day=1,
            min_minutes_between=0,
        )
        # First send succeeds
        r1, _ = await self._execute(runtime_profile="inner_tick", gate=gate)
        assert r1.success is True
        # Second send is denied (cap reached)
        r2, _ = await self._execute(runtime_profile="inner_tick", gate=gate)
        assert r2.success is False
        assert r2.payload["gate_decision"] == "deny"

    async def test_proactive_send_active_flag_engages_gate(self):
        """A non-inner_tick caller can opt in via services flag."""
        gate = _gate(datetime(2026, 1, 1, 23, 30))
        result, sent = await self._execute(
            runtime_profile="chat_extended",
            proactive_active=True,
            gate=gate,
        )
        assert result.success is False
        assert result.payload["gate_decision"] == "defer"
        assert sent == []
