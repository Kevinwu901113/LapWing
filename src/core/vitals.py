"""Lapwing 生命体征 — 记录启动时间、提供运行状态快照。"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("lapwing.core.vitals")

# 模块加载时记录启动时间（进程级单例）
_BOOT_TIME = datetime.now(timezone.utc)
_BOOT_MONOTONIC = time.monotonic()
_TAIPEI_TZ = timezone(timedelta(hours=8))

_STATE_FILE: Path | None = None
_previous_state: dict | None = None


# ── A1: 基础时间 / 系统状态 ──────────────────────────────────────────


def boot_time() -> datetime:
    """返回 Lapwing 本次启动的 UTC 时间。"""
    return _BOOT_TIME


def boot_time_taipei() -> datetime:
    return _BOOT_TIME.astimezone(_TAIPEI_TZ)


def uptime_seconds() -> float:
    return time.monotonic() - _BOOT_MONOTONIC


def uptime_human() -> str:
    """返回人类可读的运行时长，如 '2小时15分钟'。"""
    secs = int(uptime_seconds())
    if secs < 60:
        return f"{secs}秒"
    mins = secs // 60
    if mins < 60:
        return f"{mins}分钟"
    hours = mins // 60
    remaining_mins = mins % 60
    if hours < 24:
        if remaining_mins:
            return f"{hours}小时{remaining_mins}分钟"
        return f"{hours}小时"
    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours:
        return f"{days}天{remaining_hours}小时"
    return f"{days}天"


def now_taipei() -> datetime:
    return datetime.now(_TAIPEI_TZ)


def now_taipei_str() -> str:
    now = now_taipei()
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return f"{now.strftime('%Y年%m月%d日 %H:%M')}，{weekday_names[now.weekday()]}"


async def system_snapshot() -> dict:
    """采集 VM 级系统状态（CPU/内存/磁盘）。

    需要 psutil。如果没装，返回基础信息。
    """
    info = {
        "boot_time": boot_time_taipei().strftime("%m月%d日 %H:%M"),
        "uptime": uptime_human(),
        "now": now_taipei_str(),
        "hostname": platform.node(),
        "python": platform.python_version(),
    }
    try:
        import psutil
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu_percent = psutil.cpu_percent(interval=0.5)
        info.update({
            "cpu_percent": f"{cpu_percent:.1f}%",
            "memory_used_gb": f"{mem.used / (1024**3):.1f}",
            "memory_total_gb": f"{mem.total / (1024**3):.1f}",
            "memory_percent": f"{mem.percent:.1f}%",
            "disk_used_gb": f"{disk.used / (1024**3):.1f}",
            "disk_total_gb": f"{disk.total / (1024**3):.1f}",
            "disk_percent": f"{disk.percent:.1f}%",
        })
    except ImportError:
        info["system_note"] = "psutil 未安装，无法获取硬件状态"
    return info


# ── B1: 重启韧性 ──────────────────────────────────────────────────────


def init(data_dir: str | os.PathLike) -> None:
    """容器启动时调用，记录启动时间并读取上次关闭信息。"""
    global _STATE_FILE
    _STATE_FILE = Path(data_dir) / "vitals.json"

    _load_previous_state()
    _save_current_state()

    prev = _previous_state
    if prev:
        shutdown_time = prev.get("last_active")
        if shutdown_time:
            logger.info(
                "Lapwing 醒来。上次活跃: %s，睡了约 %s",
                shutdown_time,
                _format_sleep_duration(prev) or "不到5分钟",
            )


def _load_previous_state() -> None:
    global _previous_state
    if _STATE_FILE is None or not _STATE_FILE.exists():
        _previous_state = None
        return
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        _previous_state = data
    except (json.JSONDecodeError, IOError):
        _previous_state = None


def _save_current_state() -> None:
    if _STATE_FILE is None:
        return
    state = {
        "boot_time": _BOOT_TIME.isoformat(),
        "last_active": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }
    try:
        _STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except IOError as e:
        logger.warning("写入 vitals.json 失败: %s", e)


def update_last_active() -> None:
    """心跳调用，定期更新 last_active 时间戳。"""
    _save_current_state()


def get_previous_state() -> dict | None:
    """获取上次运行的状态信息。"""
    return _previous_state


def get_sleep_summary() -> str | None:
    """如果刚重启，返回'睡了多久'的描述。不到 5 分钟不算'睡过'。"""
    prev = get_previous_state()
    if not prev:
        return None
    return _format_sleep_duration(prev)


def _format_sleep_duration(prev: dict) -> str | None:
    last_active_str = prev.get("last_active")
    if not last_active_str:
        return None
    try:
        last_active = datetime.fromisoformat(last_active_str)
        if last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=timezone.utc)
        gap = (_BOOT_TIME - last_active).total_seconds()
        if gap < 300:  # 不到 5 分钟，不算重启
            return None
        if gap < 3600:
            return f"{int(gap // 60)}分钟"
        if gap < 86400:
            hours = gap / 3600
            return f"{hours:.1f}小时"
        days = gap / 86400
        return f"{days:.1f}天"
    except (ValueError, TypeError):
        return None


def is_fresh_boot() -> bool:
    """是否在启动后 2 分钟内（用于重启感知行为）。"""
    return uptime_seconds() < 120


# ── 桌面端环境感知 ──────────────────────────────────────────────────────

_desktop_sensing: dict[str, dict] = {}  # owner_id -> sensing data


def update_desktop_sensing(
    owner_id: str,
    summary: str,
    state: str,
    current_app: str | None = None,
) -> None:
    """存储桌面端推送的环境感知摘要（按 owner 隔离）。"""
    _desktop_sensing[owner_id] = {
        "summary": summary,
        "state": state,
        "current_app": current_app,
        "updated_at": datetime.now(timezone.utc),
    }


def get_desktop_sensing(owner_id: str | None = None) -> dict | None:
    """获取桌面端环境感知（10 分钟内有效）。

    如果 owner_id 为 None，返回最近更新的任一条目（单 owner 兼容）。
    """
    if owner_id is not None:
        entry = _desktop_sensing.get(owner_id)
    elif _desktop_sensing:
        entry = max(_desktop_sensing.values(), key=lambda e: e["updated_at"])
    else:
        entry = None

    if entry is None:
        return None
    age = (datetime.now(timezone.utc) - entry["updated_at"]).total_seconds()
    if age > 600:
        return None
    return entry
