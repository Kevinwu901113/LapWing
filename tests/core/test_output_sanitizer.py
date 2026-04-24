import pytest
from src.core.output_sanitizer import sanitize_outgoing


class TestSanitizeOutgoing:
    def test_removes_next_marker(self):
        assert sanitize_outgoing("没事了[NEXT: 4h]") == "没事了"
        assert sanitize_outgoing("没事了[NEXT: 30m]") == "没事了"
        assert sanitize_outgoing("没事了[NEXT: 60s]") == "没事了"

    def test_removes_enter(self):
        assert sanitize_outgoing("无事[ENTER]") == "无事"

    def test_removes_simulated_tool_call_chinese(self):
        assert sanitize_outgoing("我来查一下[调用 web_search: 道奇比赛]") == "我来查一下"

    def test_removes_simulated_tool_call_english(self):
        assert sanitize_outgoing("Let me check[tool_call: web_search]") == "Let me check"

    def test_removes_think_blocks(self):
        assert sanitize_outgoing("答案是<think>内部推理</think>42") == "答案是42"

    def test_removes_orphan_think_tags(self):
        assert sanitize_outgoing("答案是<think>42") == "答案是42"

    def test_collapses_excessive_newlines(self):
        assert sanitize_outgoing("你好\n\n\n\n世界") == "你好\n\n世界"

    def test_empty_input(self):
        assert sanitize_outgoing("") == ""
        assert sanitize_outgoing(None) is None  # type: ignore

    def test_no_markers(self):
        assert sanitize_outgoing("正常文本，没有标记") == "正常文本，没有标记"

    def test_multiple_markers(self):
        text = "等一下[NEXT: 5m][ENTER]"
        result = sanitize_outgoing(text)
        assert "[NEXT:" not in result
        assert "[ENTER]" not in result
        assert "等一下" in result
