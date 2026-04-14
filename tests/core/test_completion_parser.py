import pytest
from src.core.task_runtime import TaskRuntime


class TestParseCompletionResult:
    def test_normal_json_in_code_block(self):
        raw = '```json\n{"completed": true, "worth_retrying": false, "remaining": ""}\n```'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is True
        assert result["worth_retrying"] is False

    def test_json_without_code_block(self):
        raw = '{"completed": false, "worth_retrying": true, "remaining": "还没做完"}'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is False
        assert result["worth_retrying"] is True

    def test_json_with_preamble_text(self):
        raw = '让我分析一下任务完成情况：\n{"completed": true, "remaining": ""}'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is True

    def test_bare_string_returns_safe_default(self):
        """之前的 bug：裸字符串 "completed" 导致 AttributeError"""
        raw = '"completed"'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is False
        assert result["worth_retrying"] is True

    def test_garbage_input_returns_safe_default(self):
        raw = "这不是 JSON 格式的输出"
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is False
        assert result["worth_retrying"] is True

    def test_think_blocks_stripped(self):
        raw = '<think>让我想想</think>```json\n{"completed": true}\n```'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is True

    def test_array_input_extracts_inner_dict(self):
        """数组输入时，{...} 提取策略从中找到内部 dict"""
        raw = '[{"completed": true}]'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is True

    def test_pure_array_no_dict_returns_safe_default(self):
        """纯数组（无内部 dict）应返回安全默认值"""
        raw = '["completed", "done"]'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is False
        assert result["worth_retrying"] is True

    def test_empty_input(self):
        result = TaskRuntime._parse_completion_result("")
        assert result["completed"] is False

    def test_newline_completed_fragment(self):
        """复现日志中的实际失败 case"""
        raw = '\n  "completed"'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is False
        assert result["worth_retrying"] is True

    def test_default_completed_is_false(self):
        """确保缺少 completed 字段时默认为 False（安全默认值）"""
        raw = '{"worth_retrying": true, "remaining": "未知"}'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["completed"] is False

    def test_default_worth_retrying_is_true(self):
        """确保缺少 worth_retrying 字段时默认为 True（安全默认值）"""
        raw = '{"completed": false, "remaining": "未完成"}'
        result = TaskRuntime._parse_completion_result(raw)
        assert result["worth_retrying"] is True
