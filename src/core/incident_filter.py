"""Incident 创建过滤器。

第一层：规则过滤，快速排除明显不是 incident 的情况。
仅用于 tool_failure 来源。user_correction / quality_check / self_note 不需要额外过滤。
"""

import logging

logger = logging.getLogger("lapwing.core.incident_filter")


def should_create_incident(
    tool_name: str,
    result,  # ToolExecutionResult
) -> tuple[bool, str]:
    """第一层规则过滤。

    返回 (should_proceed, error_type)。
    should_proceed=False 表示不创建 incident。

    排除的情况：
    - 搜索返回空结果（正常业务逻辑）
    - 用户取消操作
    - 参数校验失败（调用方的问题）
    - file_read 文件不存在（正常情况）
    """
    reason = result.reason if hasattr(result, "reason") else str(result)
    reason_lower = reason.lower() if reason else ""

    # ── 排除项 ──

    if "no results" in reason_lower or "未找到" in reason_lower or "没有找到" in reason_lower:
        return False, ""

    if "cancel" in reason_lower or "取消" in reason_lower:
        return False, ""

    if "invalid argument" in reason_lower or "missing required" in reason_lower:
        return False, ""
    if "参数" in reason_lower and ("缺少" in reason_lower or "无效" in reason_lower):
        return False, ""

    if tool_name in (
        "file_read", "read_file", "file_read_segment",
        "memory_read", "memory_read_segment", "memory_list",
    ) and ("not found" in reason_lower or "不存在" in reason_lower):
        return False, ""

    # ── 分类 ──

    error_type = "unknown"

    if "timeout" in reason_lower or "timed out" in reason_lower or "超时" in reason_lower:
        error_type = "timeout"
    elif any(code in reason_lower for code in ("500", "502", "503", "529")):
        error_type = "http_5xx"
    elif "permission" in reason_lower or "denied" in reason_lower or "权限" in reason_lower:
        error_type = "permission_denied"
    elif "exception" in reason_lower or "traceback" in reason_lower or "error" in reason_lower:
        error_type = "exception"

    return True, error_type


def tool_failure_severity(error_type: str) -> str:
    """根据错误类型确定 severity。"""
    if error_type == "exception":
        return "high"
    if error_type == "permission_denied":
        return "high"
    if error_type == "http_5xx":
        return "medium"
    return "low"
