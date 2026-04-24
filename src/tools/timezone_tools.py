"""时区转换工具。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.tools.types import (
    ToolExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)

CONVERT_TIMEZONE_DESCRIPTION = (
    "将时间从一个时区转换到另一个时区。不要自己算时区——用这个工具。"
)

CONVERT_TIMEZONE_SCHEMA = {
    "type": "object",
    "properties": {
        "time_str": {
            "type": "string",
            "description": "要转换的时间，格式 YYYY-MM-DD HH:MM 或 HH:MM（默认今天）",
        },
        "from_tz": {
            "type": "string",
            "description": "源时区，如 America/Los_Angeles、Asia/Tokyo",
        },
        "to_tz": {
            "type": "string",
            "description": "目标时区，默认 Asia/Taipei",
        },
    },
    "required": ["time_str", "from_tz"],
    "additionalProperties": False,
}

GET_CURRENT_DATETIME_DESCRIPTION = "获取指定时区的当前日期和时间。"

GET_CURRENT_DATETIME_SCHEMA = {
    "type": "object",
    "properties": {
        "timezone": {
            "type": "string",
            "description": "时区名称，如 Asia/Taipei、America/Los_Angeles。默认 Asia/Taipei",
        },
    },
    "additionalProperties": False,
}


async def convert_timezone_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    time_str = str(request.arguments.get("time_str", "")).strip()
    from_tz_name = str(request.arguments.get("from_tz", "")).strip()
    to_tz_name = str(request.arguments.get("to_tz", "Asia/Taipei")).strip()

    if not time_str or not from_tz_name:
        return ToolExecutionResult(
            success=False,
            payload={"error": "time_str 和 from_tz 不能为空"},
            reason="缺少必要参数",
        )

    try:
        from_tz = ZoneInfo(from_tz_name)
        to_tz = ZoneInfo(to_tz_name)
    except (KeyError, Exception) as e:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"无效时区: {e}"},
            reason=f"时区解析失败: {e}",
        )

    try:
        from src.core.time_utils import now as _now
        today = _now().date()

        if len(time_str) <= 5 and ":" in time_str:
            parts = time_str.split(":")
            dt = datetime(today.year, today.month, today.day,
                          int(parts[0]), int(parts[1]), tzinfo=from_tz)
        else:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M").replace(tzinfo=from_tz)

        converted = dt.astimezone(to_tz)

        return ToolExecutionResult(
            success=True,
            payload={
                "original": dt.strftime("%Y-%m-%d %H:%M %Z"),
                "converted": converted.strftime("%Y-%m-%d %H:%M %Z"),
                "from_tz": from_tz_name,
                "to_tz": to_tz_name,
            },
        )
    except Exception as e:
        return ToolExecutionResult(
            success=False,
            payload={"error": str(e)},
            reason=f"时间转换失败: {e}",
        )


async def get_current_datetime_executor(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    tz_name = str(request.arguments.get("timezone", "Asia/Taipei")).strip()

    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, Exception) as e:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"无效时区: {e}"},
            reason=f"时区解析失败: {e}",
        )

    from src.core.time_utils import now as _now
    now = _now().astimezone(tz)

    weekday_names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

    return ToolExecutionResult(
        success=True,
        payload={
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": tz_name,
            "weekday": weekday_names[now.weekday()],
            "iso": now.isoformat(),
        },
    )
