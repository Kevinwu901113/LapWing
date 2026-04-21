"""共享的日期时间工具函数。"""

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def now() -> datetime:
    """统一时间入口，时区由配置决定。"""
    from src.config import get_settings
    tz_name = getattr(get_settings(), 'browser', None)
    tz_str = "Asia/Shanghai"
    if tz_name is not None:
        tz_str = getattr(tz_name, 'timezone', "Asia/Shanghai") or "Asia/Shanghai"
    return datetime.now(ZoneInfo(tz_str))


def parse_iso_datetime(value: Any) -> datetime | None:
    """解析 ISO 8601 日期字符串、Unix 时间戳为 UTC datetime。

    支持: str（含 Z 后缀）、int/float 时间戳。
    解析失败或值为空时返回 None。
    """
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()
