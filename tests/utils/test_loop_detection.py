"""Tests for src.utils.loop_detection — shared loop detector."""

import pytest

from src.utils.loop_detection import (
    LoopCheckResult,
    LoopDetector,
    LoopDetectorConfig,
    LoopDetectorState,
    LoopVerdict,
    tool_args_hash,
    _generic_repeat_count,
    _ping_pong_count,
)


class TestToolArgsHash:
    def test_deterministic(self):
        h1 = tool_args_hash({"a": 1, "b": "hello"})
        h2 = tool_args_hash({"b": "hello", "a": 1})
        assert h1 == h2

    def test_different_args_different_hash(self):
        h1 = tool_args_hash({"a": 1})
        h2 = tool_args_hash({"a": 2})
        assert h1 != h2

    def test_empty_dict(self):
        h = tool_args_hash({})
        assert isinstance(h, str) and len(h) == 64


class TestGenericRepeatCount:
    def test_no_history(self):
        from collections import deque
        history: deque[tuple[str, str]] = deque()
        assert _generic_repeat_count(history, ("tool", "hash")) == 1

    def test_consecutive_matches(self):
        from collections import deque
        sig = ("tool_a", "hash_x")
        history: deque[tuple[str, str]] = deque([sig, sig, sig])
        assert _generic_repeat_count(history, sig) == 4

    def test_broken_by_different(self):
        from collections import deque
        sig = ("tool_a", "hash_x")
        other = ("tool_b", "hash_y")
        history: deque[tuple[str, str]] = deque([sig, sig, other, sig, sig])
        assert _generic_repeat_count(history, sig) == 3


class TestPingPongCount:
    def test_too_short_history(self):
        from collections import deque
        history: deque[tuple[str, str]] = deque([("a", "1"), ("b", "2")])
        assert _ping_pong_count(history, ("a", "1")) == 0

    def test_alternating_pattern(self):
        from collections import deque
        a = ("tool_a", "h1")
        b = ("tool_b", "h2")
        history: deque[tuple[str, str]] = deque([a, b, a, b, a])
        assert _ping_pong_count(history, b) >= 2

    def test_consecutive_same_not_ping_pong(self):
        from collections import deque
        a = ("tool_a", "h1")
        history: deque[tuple[str, str]] = deque([a, a, a])
        assert _ping_pong_count(history, a) == 0


class TestLoopDetector:
    def _make_detector(self, **overrides) -> LoopDetector:
        defaults = dict(
            enabled=True,
            warning_threshold=3,
            global_circuit_breaker_threshold=5,
        )
        defaults.update(overrides)
        return LoopDetector(LoopDetectorConfig(**defaults))

    def test_no_loop_on_varied_calls(self):
        det = self._make_detector()
        state = det.new_state()
        for i in range(10):
            result = det.check(state, "tool", {"arg": i})
            assert not result.should_block
            det.record(state, "tool", {"arg": i})

    def test_generic_repeat_warning(self):
        det = self._make_detector(warning_threshold=3)
        state = det.new_state()
        args = {"q": "same"}
        for _ in range(2):
            det.record(state, "search", args)
        result = det.check(state, "search", args)
        assert result.generic_repeat is LoopVerdict.WARNING
        assert result.generic_repeat_count == 3

    def test_generic_repeat_block(self):
        det = self._make_detector(
            warning_threshold=2,
            global_circuit_breaker_threshold=4,
        )
        state = det.new_state()
        args = {"q": "same"}
        for _ in range(3):
            det.record(state, "search", args)
        result = det.check(state, "search", args)
        assert result.should_block
        assert "重复循环" in result.block_reason

    def test_ping_pong_block(self):
        det = self._make_detector(
            warning_threshold=2,
            global_circuit_breaker_threshold=3,
        )
        state = det.new_state()
        a_args = {"x": 1}
        b_args = {"y": 2}
        for _ in range(4):
            det.record(state, "tool_a", a_args)
            det.record(state, "tool_b", b_args)
        result = det.check(state, "tool_a", a_args)
        assert result.ping_pong is LoopVerdict.BLOCK

    def test_disabled_detector(self):
        det = self._make_detector(enabled=False)
        state = det.new_state()
        args = {"q": "same"}
        for _ in range(50):
            det.record(state, "tool", args)
        result = det.check(state, "tool", args)
        assert not result.should_block
        assert not result.has_warning

    def test_block_reason_empty_when_ok(self):
        result = LoopCheckResult()
        assert result.block_reason == ""
