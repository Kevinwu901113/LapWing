from __future__ import annotations

import pytest

from src.core.infra_breaker import InfraCircuitBreaker, InfraBreakerState


def test_infra_breaker_open_half_open_and_close_after_three_successes():
    now = [0.0]
    breaker = InfraCircuitBreaker(
        cooldown_schedule_seconds=(5.0, 10.0, 30.0),
        close_success_threshold=3,
        now_fn=lambda: now[0],
    )

    breaker.record_failure("tool_dispatcher")

    allowed, reason = breaker.should_allow("tool_dispatcher")
    assert allowed is False
    assert reason == "infra_breaker_open"

    now[0] = 5.0
    allowed, reason = breaker.should_allow("tool_dispatcher")
    assert allowed is True
    assert reason == "half_open_probe"

    allowed, reason = breaker.should_allow("tool_dispatcher")
    assert allowed is False
    assert reason == "infra_breaker_half_open_probe_in_flight"

    breaker.record_success("tool_dispatcher")
    assert breaker.snapshot("tool_dispatcher")["state"] == InfraBreakerState.HALF_OPEN.value
    breaker.record_success("tool_dispatcher")
    breaker.record_success("tool_dispatcher")
    assert breaker.snapshot("tool_dispatcher")["state"] == InfraBreakerState.CLOSED.value


def test_infra_breaker_failure_in_half_open_reopens_with_backoff():
    now = [0.0]
    breaker = InfraCircuitBreaker(
        cooldown_schedule_seconds=(5.0, 10.0, 30.0),
        close_success_threshold=3,
        now_fn=lambda: now[0],
    )

    breaker.record_failure("tool_registry")
    first_cooldown = breaker.snapshot("tool_registry")["cooldown_until"]
    now[0] = 5.0
    assert breaker.should_allow("tool_registry")[0] is True

    breaker.record_failure("tool_registry")

    snap = breaker.snapshot("tool_registry")
    assert snap["state"] == InfraBreakerState.OPEN.value
    assert snap["cooldown_until"] == now[0] + 10.0
    assert snap["cooldown_until"] > first_cooldown


def test_infra_breaker_disabled_flag_allows_legacy_flow():
    breaker = InfraCircuitBreaker(enabled=False)

    breaker.record_failure("tool_dispatcher")

    allowed, reason = breaker.should_allow("tool_dispatcher")
    assert allowed is True
    assert reason == "disabled"


def test_infra_breaker_env_var_disabled_allows_legacy_flow(monkeypatch):
    from src.config.settings import get_settings

    monkeypatch.setenv("INFRA_BREAKER_ENABLED", "false")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.infra_breaker.enabled is False

        breaker = InfraCircuitBreaker(enabled=settings.infra_breaker.enabled)
        breaker.record_failure("tool_dispatcher")

        allowed, reason = breaker.should_allow("tool_dispatcher")
        assert allowed is True
        assert reason == "disabled"
    finally:
        monkeypatch.delenv("INFRA_BREAKER_ENABLED", raising=False)
        get_settings.cache_clear()
