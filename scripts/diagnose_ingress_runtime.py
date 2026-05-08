#!/usr/bin/env python3
"""Stage-0 production/version/config diagnostic for QQ ingress debugging."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANUAL_VERIFICATION_COMMANDS = [
    "git rev-parse HEAD",
    "git log -1 --oneline --decorate",
    "ps -ef | grep -i '[l]apwing\\|python.*main.py'",
    "cat data/lapwing.pid",
    (
        "tail -n 500 logs/lapwing.log | grep -E "
        "'Lapwing 正在启动|Lapwing 已启动|MainLoop started|QQ adapter|QQ 通道已注册|"
        "通道已启动|foreground_turn|owner_over_owner'"
    ),
]


def _run(*args: str) -> str:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.stdout.strip()


def _safe_identifier(value: str) -> dict[str, Any]:
    if not value:
        return {"present": False, "tail": "", "sha256_8": ""}
    return {
        "present": True,
        "tail": value[-4:],
        "sha256_8": hashlib.sha256(value.encode("utf-8")).hexdigest()[:8],
    }


def _iso_from_epoch(epoch: float | int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _read_pid() -> int | None:
    pid_path = ROOT / "data/lapwing.pid"
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except Exception:
        return None


def _process_info(pid: int | None) -> dict[str, Any]:
    if pid is None:
        return {"pid": None, "running": False, "source": "data/lapwing.pid missing"}

    info: dict[str, Any] = {"pid": pid, "running": False}
    try:
        os.kill(pid, 0)
        info["running"] = True
    except PermissionError as exc:
        info["running"] = True
        info["permission_limited"] = str(exc)
    except OSError as exc:
        info["error"] = str(exc)
        return info

    try:
        import psutil

        proc = psutil.Process(pid)
        info.update({
            "cmdline": proc.cmdline(),
            "cwd": proc.cwd(),
            "start_time": _iso_from_epoch(proc.create_time()),
            "start_epoch": proc.create_time(),
        })
        return info
    except Exception as exc:
        info["psutil_error"] = str(exc)

    ps_line = _run("ps", "-p", str(pid), "-o", "pid=,lstart=,etime=,cmd=")
    info["ps"] = ps_line
    lstart = _run("ps", "-p", str(pid), "-o", "lstart=")
    if lstart:
        info["start_time_local"] = lstart
        try:
            start_dt = datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y")
            info["start_epoch"] = start_dt.timestamp()
            info["start_time"] = _iso_from_epoch(info["start_epoch"])
        except ValueError as exc:
            info["start_parse_error"] = str(exc)
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        info["cmdline"] = [
            part for part in cmdline_path.read_bytes().decode("utf-8", "replace").split("\0")
            if part
        ]
    except Exception as exc:
        info["cmdline_error"] = str(exc)
    return info


def _git_info() -> dict[str, Any]:
    epoch_raw = _run("git", "log", "-1", "--format=%ct")
    try:
        commit_epoch = int(epoch_raw)
    except ValueError:
        commit_epoch = None
    return {
        "head": _run("git", "rev-parse", "HEAD"),
        "head_oneline": _run("git", "log", "-1", "--oneline", "--decorate"),
        "head_commit_time": _iso_from_epoch(commit_epoch),
        "head_commit_epoch": commit_epoch,
    }


def _settings_info() -> dict[str, Any]:
    from src.config import get_settings
    import config.settings as legacy_settings

    settings = get_settings()
    return {
        "QQ_ENABLED": bool(settings.qq.enabled),
        "QQ_KEVIN_ID": _safe_identifier(str(settings.qq.kevin_id or "")),
        "OWNER_IDS_count": len(getattr(legacy_settings, "OWNER_IDS", set()) or set()),
        "runtime_interaction_hardening.enabled": bool(
            settings.runtime_interaction_hardening.enabled
        ),
        "foreground_turn_timeout_seconds": (
            settings.runtime_interaction_hardening.foreground_turn_timeout_seconds
        ),
        "owner_status_probe_grace_seconds": (
            settings.runtime_interaction_hardening.owner_status_probe_grace_seconds
        ),
        "concurrent_bg_work.enabled": bool(settings.concurrent_bg_work.enabled),
        "concurrent_bg_work.p4_cancellation_evolution": bool(
            settings.concurrent_bg_work.p4_cancellation_evolution
        ),
        "proactive_messages.enabled": bool(settings.proactive_messages.enabled),
    }


def _recent_log_markers() -> dict[str, Any]:
    log_path = ROOT / "logs/lapwing.log"
    if not log_path.exists():
        return {"log_path": str(log_path), "available": False}

    lines = log_path.read_text(errors="replace").splitlines()[-500:]
    start_index = 0
    for idx, line in enumerate(lines):
        if "Lapwing 正在启动" in line:
            start_index = idx
    recent = lines[start_index:]
    last_connected = max(
        (idx for idx, line in enumerate(recent) if "QQ adapter 已连接" in line),
        default=-1,
    )
    last_stopped = max(
        (idx for idx, line in enumerate(recent) if "QQ adapter 已停止" in line),
        default=-1,
    )
    main_loop_started = any("MainLoop started" in line for line in recent)
    return {
        "log_path": str(log_path),
        "available": True,
        "latest_start_marker": next(
            (line for line in recent if "Lapwing 正在启动" in line),
            None,
        ),
        "MainLoop_started": main_loop_started,
        "owner_watcher_started": (
            "inferred_from_MainLoop_started"
            if main_loop_started
            else "not_observed"
        ),
        "QQ_adapter_registered": any("QQ 通道已注册" in line for line in recent),
        "QQ_adapter_connected_status": last_connected > last_stopped,
        "latest_QQ_connected_marker": (
            recent[last_connected] if last_connected >= 0 else None
        ),
    }


def main() -> int:
    pid = _read_pid()
    git = _git_info()
    process = _process_info(pid)
    process_epoch = process.get("start_epoch")
    head_epoch = git.get("head_commit_epoch")

    report = {
        "git": git,
        "process": process,
        "running_process_started_after_latest_deployment": (
            bool(process.get("running"))
            and process_epoch is not None
            and head_epoch is not None
            and float(process_epoch) >= float(head_epoch)
        ),
        "config": _settings_info(),
        "runtime_markers": _recent_log_markers(),
        "manual_verification_commands": MANUAL_VERIFICATION_COMMANDS,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
