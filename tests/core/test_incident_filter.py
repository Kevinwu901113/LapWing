"""incident_filter 规则过滤测试。"""

from dataclasses import dataclass

import pytest

from src.core.incident_filter import should_create_incident, tool_failure_severity


@dataclass
class FakeResult:
    reason: str = ""


def test_filter_empty_search_results():
    result = FakeResult(reason="No results found for query")
    should, _ = should_create_incident("web_search", result)
    assert not should


def test_filter_chinese_not_found():
    result = FakeResult(reason="未找到相关结果")
    should, _ = should_create_incident("web_search", result)
    assert not should


def test_filter_cancel():
    result = FakeResult(reason="Operation cancelled by user")
    should, _ = should_create_incident("web_search", result)
    assert not should


def test_filter_invalid_argument():
    result = FakeResult(reason="Invalid argument: query is empty")
    should, _ = should_create_incident("web_search", result)
    assert not should


def test_filter_file_not_found():
    result = FakeResult(reason="File not found: /tmp/test.txt")
    should, _ = should_create_incident("read_file", result)
    assert not should


def test_filter_file_not_found_other_tool():
    """非 file_read 工具的 not found 不应过滤。"""
    result = FakeResult(reason="Resource not found")
    should, _ = should_create_incident("web_fetch", result)
    assert should


def test_classify_timeout():
    result = FakeResult(reason="Request timed out after 30s")
    should, error_type = should_create_incident("web_search", result)
    assert should
    assert error_type == "timeout"


def test_classify_http_5xx():
    result = FakeResult(reason="Server returned 502 Bad Gateway")
    should, error_type = should_create_incident("web_search", result)
    assert should
    assert error_type == "http_5xx"


def test_classify_529_overload():
    result = FakeResult(reason="529 overloaded")
    should, error_type = should_create_incident("web_search", result)
    assert should
    assert error_type == "http_5xx"


def test_classify_permission_denied():
    result = FakeResult(reason="Permission denied: cannot access /etc/shadow")
    should, error_type = should_create_incident("execute_shell", result)
    assert should
    assert error_type == "permission_denied"


def test_classify_exception():
    result = FakeResult(reason="Traceback: KeyError")
    should, error_type = should_create_incident("web_search", result)
    assert should
    assert error_type == "exception"


def test_classify_unknown():
    result = FakeResult(reason="Something went wrong")
    should, error_type = should_create_incident("web_search", result)
    assert should
    assert error_type == "unknown"


def test_severity_exception():
    assert tool_failure_severity("exception") == "high"


def test_severity_permission():
    assert tool_failure_severity("permission_denied") == "high"


def test_severity_http():
    assert tool_failure_severity("http_5xx") == "medium"


def test_severity_timeout():
    assert tool_failure_severity("timeout") == "low"


def test_severity_unknown():
    assert tool_failure_severity("unknown") == "low"
