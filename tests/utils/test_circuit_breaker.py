"""CircuitBreaker 单元测试。"""
import time
from unittest.mock import patch

import pytest

from src.utils.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_allows_first_call(self):
        cb = CircuitBreaker()
        allowed, reason = cb.should_allow("test:key")
        assert allowed is True
        assert reason == ""

    def test_blocks_after_failure(self):
        cb = CircuitBreaker(cooldown_sequence=(10, 30, 60))
        cb.record_failure("test:key")
        allowed, reason = cb.should_allow("test:key")
        assert allowed is False
        assert "circuit_breaker" in reason

    def test_allows_after_cooldown(self):
        cb = CircuitBreaker(cooldown_sequence=(1,))
        cb.record_failure("test:key")
        with patch("src.utils.circuit_breaker.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            allowed, _ = cb.should_allow("test:key")
            assert allowed is True

    def test_escalating_cooldown(self):
        cb = CircuitBreaker(cooldown_sequence=(10, 60, 300))
        now = time.time()

        cb._failures["test:key"] = [now]
        allowed, reason = cb.should_allow("test:key")
        assert allowed is False
        assert "1 failures" in reason

        cb._failures["test:key"] = [now, now]
        allowed, reason = cb.should_allow("test:key")
        assert allowed is False

    def test_success_clears_failures(self):
        cb = CircuitBreaker()
        cb.record_failure("test:key")
        cb.record_success("test:key")
        allowed, _ = cb.should_allow("test:key")
        assert allowed is True

    def test_reset(self):
        cb = CircuitBreaker()
        cb.record_failure("test:key")
        cb.reset("test:key")
        allowed, _ = cb.should_allow("test:key")
        assert allowed is True

    def test_reset_all(self):
        cb = CircuitBreaker()
        cb.record_failure("a")
        cb.record_failure("b")
        cb.reset_all()
        assert cb.open_circuits == {}

    def test_open_circuits(self):
        cb = CircuitBreaker()
        cb.record_failure("a")
        cb.record_failure("a")
        cb.record_failure("b")
        assert cb.open_circuits == {"a": 2, "b": 1}

    def test_independent_keys(self):
        cb = CircuitBreaker(cooldown_sequence=(10,))
        cb.record_failure("a")
        allowed_a, _ = cb.should_allow("a")
        allowed_b, _ = cb.should_allow("b")
        assert allowed_a is False
        assert allowed_b is True
