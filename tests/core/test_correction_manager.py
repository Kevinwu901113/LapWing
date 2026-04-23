"""CorrectionManager 单元测试。"""

import pytest
import time
from unittest.mock import MagicMock

from src.core.correction_manager import CorrectionManager


class TestAddCorrection:
    """add_correction 方法基础行为测试。"""

    def test_increments_count(self):
        """每次调用应累加计数。"""
        mgr = CorrectionManager()
        assert mgr.add_correction("test_rule") == 1
        assert mgr.add_correction("test_rule") == 2
        assert mgr.add_correction("test_rule") == 3

    def test_different_rules_are_independent(self):
        """不同规则的计数相互独立。"""
        mgr = CorrectionManager()
        mgr.add_correction("rule_a")
        mgr.add_correction("rule_a")
        mgr.add_correction("rule_b")
        violations = mgr.get_violations()
        assert violations["rule_a"] == 2
        assert violations["rule_b"] == 1

    def test_threshold_fires_callback_at_exactly_threshold(self):
        """恰好达到阈值时回调一次，不提前也不多次。"""
        callback = MagicMock()
        mgr = CorrectionManager(threshold=3, on_threshold=callback)

        mgr.add_correction("rule_x", "第1次")
        callback.assert_not_called()

        mgr.add_correction("rule_x", "第2次")
        callback.assert_not_called()

        mgr.add_correction("rule_x", "第3次")
        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == "rule_x"   # rule_key
        assert args[1] == 3           # count
        assert "第1次" in args[2]      # all_details
        assert "第2次" in args[2]
        assert "第3次" in args[2]

    def test_threshold_fires_again_on_subsequent_calls(self):
        """超过阈值后，后续每次调用都会再次触发回调。"""
        callback = MagicMock()
        mgr = CorrectionManager(threshold=3, on_threshold=callback)
        for _ in range(5):
            mgr.add_correction("rule_x")
        # 第3、4、5次都应触发
        assert callback.call_count == 3

    def test_no_callback_when_none(self):
        """未设置回调时不报错。"""
        mgr = CorrectionManager(threshold=2)
        mgr.add_correction("rule_y")
        mgr.add_correction("rule_y")  # 达到阈值，但无回调

    def test_details_accumulate(self):
        """空 details 不加入列表，非空 details 正确拼接。"""
        callback = MagicMock()
        mgr = CorrectionManager(threshold=2, on_threshold=callback)
        mgr.add_correction("rule_z")           # 无 details
        mgr.add_correction("rule_z", "abc")    # 有 details
        args = callback.call_args[0]
        assert args[2] == "abc"                 # 只有非空的那条

    def test_returns_current_count(self):
        """返回值是当前累计次数。"""
        mgr = CorrectionManager()
        assert mgr.add_correction("r") == 1
        assert mgr.add_correction("r") == 2


class TestGetViolationsAndReset:
    """get_violations 和 reset 方法测试。"""

    def test_get_violations_returns_snapshot(self):
        """get_violations 返回当前状态快照（副本）。"""
        mgr = CorrectionManager()
        mgr.add_correction("a")
        mgr.add_correction("b")
        mgr.add_correction("b")
        snap = mgr.get_violations()
        assert snap == {"a": 1, "b": 2}
        # 修改快照不影响内部状态
        snap["a"] = 999
        assert mgr.get_violations()["a"] == 1

    def test_reset_clears_rule(self):
        """reset 清除指定规则的记录。"""
        mgr = CorrectionManager()
        mgr.add_correction("a")
        mgr.add_correction("b")
        mgr.reset("a")
        assert "a" not in mgr.get_violations()
        assert mgr.get_violations()["b"] == 1

    def test_reset_nonexistent_rule_is_noop(self):
        """reset 一个不存在的规则不报错。"""
        mgr = CorrectionManager()
        mgr.reset("nonexistent")  # 不应抛异常


class TestOnCircuitBreak:
    """on_circuit_break 方法防抖测试。"""

    def test_fires_callback_on_first_call(self):
        """第一次触发应调用 on_circuit_break 回调。"""
        callback = MagicMock()
        mgr = CorrectionManager(on_circuit_break=callback, circuit_break_cooldown_seconds=600)
        mgr.on_circuit_break("my_tool", 5)
        callback.assert_called_once_with("my_tool", 5)

    def test_suppressed_within_cooldown(self):
        """冷却期内再次触发应被抑制。"""
        callback = MagicMock()
        mgr = CorrectionManager(on_circuit_break=callback, circuit_break_cooldown_seconds=600)
        mgr.on_circuit_break("my_tool", 5)
        mgr.on_circuit_break("my_tool", 6)  # 立即再触发 → 应被抑制
        callback.assert_called_once()

    def test_different_tools_have_independent_cooldowns(self):
        """不同工具的冷却时间相互独立。"""
        callback = MagicMock()
        mgr = CorrectionManager(on_circuit_break=callback, circuit_break_cooldown_seconds=600)
        mgr.on_circuit_break("tool_a", 3)
        mgr.on_circuit_break("tool_b", 3)  # 不同工具 → 不被抑制
        assert callback.call_count == 2

    def test_fires_again_after_cooldown(self):
        """冷却期过后应再次触发（直接操控内部时间戳模拟冷却过期）。"""
        callback = MagicMock()
        mgr = CorrectionManager(on_circuit_break=callback, circuit_break_cooldown_seconds=600)

        # 第一次触发
        mgr.on_circuit_break("tool_x", 3)
        assert callback.call_count == 1

        # 立即再触发 → 被抑制
        mgr.on_circuit_break("tool_x", 3)
        assert callback.call_count == 1

        # 手动把上次触发时间戳向前推到冷却期之前
        mgr._circuit_last_fire["tool_x"] = time.monotonic() - 601.0

        # 现在应再次触发
        mgr.on_circuit_break("tool_x", 3)
        assert callback.call_count == 2

    def test_no_callback_when_none(self):
        """未设置 on_circuit_break 回调时不报错。"""
        mgr = CorrectionManager(circuit_break_cooldown_seconds=0)
        mgr.on_circuit_break("any_tool", 1)  # 不应抛异常
