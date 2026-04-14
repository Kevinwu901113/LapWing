"""模拟工具调用检测测试。"""

import pytest
from unittest.mock import MagicMock

from src.core.task_runtime import TaskRuntime, _contains_simulated_tool_call, _strip_simulated_tool_calls


def _make_runtime():
    return TaskRuntime(router=MagicMock())


class TestDetectSimulatedToolCall:
    def test_detects_chinese_intent_pattern(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("好的，我来用 web_search 帮你查一下", ["web_search", "web_fetch"]) is True

    def test_detects_english_intent_pattern(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("Let me use web_fetch to get the page", ["web_search", "web_fetch"]) is True

    def test_detects_call_pattern(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("我调用 execute_shell 来执行这个命令", ["execute_shell"]) is True

    def test_detects_json_tool_structure(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call('我会执行这个：{"tool": "web_search", "query": "test"}', ["web_search"]) is True

    def test_detects_function_json_structure(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call('{"function": "search", "args": {}}', ["search"]) is True

    def test_ignores_normal_text(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("我帮你查了一下，结果如下", ["web_search"]) is False

    def test_ignores_empty_text(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("", ["web_search"]) is False
        assert rt._detect_simulated_tool_call(None, ["web_search"]) is False

    def test_ignores_tool_name_in_non_intent_context(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("web_search 返回了3个结果", ["web_search"]) is False

    def test_no_tools_returns_false(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("我来用 web_search 查一下", []) is False

    def test_detects_bracket_pattern_chinese(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("[调用 web_search: 道奇比赛]", ["web_search"]) is True

    def test_detects_bracket_pattern_english(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("[tool_call: web_search]", ["web_search"]) is True


class TestModuleLevelSimulatedToolFunctions:
    def test_contains_chinese_bracket(self):
        assert _contains_simulated_tool_call("我来查一下[调用 web_search: 道奇比赛]")

    def test_contains_chinese_parens(self):
        assert _contains_simulated_tool_call("[调用 execute_shell(pwd)]")

    def test_contains_english_tool_call(self):
        assert _contains_simulated_tool_call("[tool_call: web_search]")

    def test_contains_english_calling(self):
        assert _contains_simulated_tool_call("[calling web_search: query]")

    def test_ignores_normal_text(self):
        assert not _contains_simulated_tool_call("我帮你查了一下比赛时间")
        assert not _contains_simulated_tool_call("搜索结果显示...")

    def test_strip_chinese(self):
        assert _strip_simulated_tool_calls("我来查一下[调用 web_search: 道奇比赛]") == "我来查一下"

    def test_strip_returns_empty(self):
        assert _strip_simulated_tool_calls("[调用 web_search: query]") == ""

    def test_strip_english(self):
        assert _strip_simulated_tool_calls("Let me check[tool_call: search]") == "Let me check"
