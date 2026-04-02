"""自主调度工具 — 让 Lapwing 自己安排定时任务。

Lapwing 可以决定：
- "我每天晚上 11 点回顾今天的对话"
- "每隔 3 小时看一下科技新闻"
- "明天早上 8 点提醒 Kevin 交文档"

任务持久化到 data/scheduled_tasks.json，由 heartbeat ScheduledTasksAction 驱动执行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from config.settings import SCHEDULED_TASKS_PATH
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.schedule_task")


# ── 辅助：任务文件 IO ──

def _load_tasks() -> list[dict]:
    if not SCHEDULED_TASKS_PATH.exists():
        return []
    try:
        return json.loads(SCHEDULED_TASKS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_tasks(tasks: list[dict]) -> None:
    SCHEDULED_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULED_TASKS_PATH.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 时间解析 ──

def _parse_schedule(raw: str) -> dict | None:
    """解析自然语言时间安排为结构化格式。

    支持的格式:
    - "每天HH:MM"         → {"type": "daily", "time": "HH:MM"}
    - "每隔N小时"          → {"type": "interval", "hours": N}
    - "每隔N分钟"          → {"type": "interval", "minutes": N}
    - "YYYY-MM-DD HH:MM" → {"type": "once", "datetime": "..."}
    - "明天/后天 HH:MM"    → {"type": "once", "datetime": "..."}

    Returns:
        解析后的 dict，或 None 表示无法解析。
    """
    raw = raw.strip()

    # 每天 HH:MM（支持全角冒号）
    m = re.match(r"每天\s*(\d{1,2})[:\uff1a](\d{2})", raw)
    if m:
        return {"type": "daily", "time": f"{int(m.group(1)):02d}:{m.group(2)}"}

    # 每隔 N 小时
    m = re.match(r"每隔\s*(\d+)\s*小时", raw)
    if m:
        return {"type": "interval", "hours": int(m.group(1))}

    # 每隔 N 分钟
    m = re.match(r"每隔\s*(\d+)\s*分钟", raw)
    if m:
        return {"type": "interval", "minutes": int(m.group(1))}

    # 日期时间（单次）YYYY-MM-DD HH:MM
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2})[:\uff1a](\d{2})", raw)
    if m:
        return {
            "type": "once",
            "datetime": f"{m.group(1)} {int(m.group(2)):02d}:{m.group(3)}",
        }

    # 明天/后天 HH:MM
    m = re.match(r"(明天|后天)\s*(\d{1,2})[:\uff1a](\d{2})", raw)
    if m:
        days = 1 if m.group(1) == "明天" else 2
        target = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        return {
            "type": "once",
            "datetime": f"{target} {int(m.group(2)):02d}:{m.group(3)}",
        }

    # N分钟后 / N分后
    m = re.match(r"(\d+)\s*分钟?后", raw)
    if m:
        target = datetime.now() + timedelta(minutes=int(m.group(1)))
        return {"type": "once", "datetime": target.strftime("%Y-%m-%d %H:%M")}

    # N小时后
    m = re.match(r"(\d+)\s*小时后", raw)
    if m:
        target = datetime.now() + timedelta(hours=int(m.group(1)))
        return {"type": "once", "datetime": target.strftime("%Y-%m-%d %H:%M")}

    return None


# ── Executor 函数 ──

async def _execute_schedule_task(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    schedule_raw = str(request.arguments.get("schedule", "")).strip()
    task_description = str(request.arguments.get("task_description", "")).strip()
    repeat = bool(request.arguments.get("repeat", True))

    if not schedule_raw:
        return ToolExecutionResult(success=False, payload={"error": "缺少 schedule 参数"}, reason="缺少 schedule 参数")
    if not task_description:
        return ToolExecutionResult(success=False, payload={"error": "缺少 task_description 参数"}, reason="缺少 task_description 参数")

    parsed = _parse_schedule(schedule_raw)
    if parsed is None:
        msg = (
            f"无法解析时间安排: '{schedule_raw}'。"
            "请用以下格式：'每天HH:MM'、'每隔N小时'、'每隔N分钟'、'YYYY-MM-DD HH:MM'、'明天/后天 HH:MM'、'N分钟后'、'N小时后'"
        )
        return ToolExecutionResult(success=False, payload={"error": msg}, reason=msg)

    task = {
        "id": f"sched_{uuid4().hex[:8]}",
        "schedule_raw": schedule_raw,
        "schedule_parsed": parsed,
        "task": task_description,
        "repeat": repeat,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "enabled": True,
    }

    def _create():
        tasks = _load_tasks()
        tasks.append(task)
        _save_tasks(tasks)

    await asyncio.to_thread(_create)
    logger.info("定时任务已创建: %s — %s", task["id"], schedule_raw)

    output = (
        f"已安排: {task_description}\n"
        f"时间: {schedule_raw} ({'重复' if repeat else '单次'})\n"
        f"ID: {task['id']}"
    )
    return ToolExecutionResult(success=True, payload={"output": output, "task_id": task["id"]})


async def _execute_list_scheduled_tasks(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del request, context

    def _list():
        return _load_tasks()

    tasks = await asyncio.to_thread(_list)
    if not tasks:
        return ToolExecutionResult(success=True, payload={"output": "当前没有定时任务。"})

    lines = []
    for t in tasks:
        status = "✓" if t.get("enabled", True) else "✗"
        repeat = "重复" if t.get("repeat", True) else "单次"
        last = t.get("last_run") or "从未执行"
        lines.append(
            f"  {status} [{t['id']}] {t['schedule_raw']} ({repeat})\n"
            f"    任务: {t['task'][:60]}\n"
            f"    上次: {last}"
        )

    output = f"定时任务 ({len(tasks)} 个):\n\n" + "\n\n".join(lines)
    return ToolExecutionResult(success=True, payload={"output": output})


async def _execute_cancel_scheduled_task(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    task_id = str(request.arguments.get("task_id", "")).strip()
    if not task_id:
        return ToolExecutionResult(success=False, payload={"error": "缺少 task_id 参数"}, reason="缺少 task_id 参数")

    def _cancel():
        tasks = _load_tasks()
        for i, t in enumerate(tasks):
            if t["id"] == task_id:
                removed = tasks.pop(i)
                _save_tasks(tasks)
                return True, removed["task"]
        return False, None

    found, task_desc = await asyncio.to_thread(_cancel)
    if not found:
        return ToolExecutionResult(success=False, payload={"error": f"未找到任务: {task_id}"}, reason=f"未找到任务: {task_id}")

    output = f"已取消任务: {task_desc[:50]}"
    return ToolExecutionResult(success=True, payload={"output": output})


# 导出供 registry.py 使用
SCHEDULE_EXECUTORS = {
    "schedule_task": _execute_schedule_task,
    "list_scheduled_tasks": _execute_list_scheduled_tasks,
    "cancel_scheduled_task": _execute_cancel_scheduled_task,
}
