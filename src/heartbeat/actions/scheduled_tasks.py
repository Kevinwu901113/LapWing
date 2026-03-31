"""ScheduledTasksAction — 检查并触发到期的定时任务。

每分钟心跳检查一次 data/scheduled_tasks.json，到期任务通过 brain.think() 执行。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from config.settings import SCHEDULED_TASKS_PATH
from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.scheduled_tasks")

_CHECK_WINDOW_SECONDS = 300  # 到期时间在当前时间 ±5 分钟内视为到期


class ScheduledTasksAction(HeartbeatAction):
    """每分钟检查定时任务，触发到期任务。"""

    name = "scheduled_tasks_check"
    description = "检查并执行到期的自主安排定时任务"
    beat_types = ["minute"]
    selection_mode = "always"

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        if not SCHEDULED_TASKS_PATH.exists():
            return

        try:
            tasks = json.loads(SCHEDULED_TASKS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取定时任务文件失败: %s", e)
            return

        now = datetime.now()
        modified = False
        to_execute: list[dict] = []

        for task in tasks:
            if not task.get("enabled", True):
                continue
            if _should_run(task, now):
                to_execute.append(task)
                task["last_run"] = now.isoformat()
                modified = True
                if not task.get("repeat", True):
                    task["enabled"] = False

        if modified:
            # 清理已完成的单次任务
            tasks = [t for t in tasks if t.get("enabled", True)]
            try:
                SCHEDULED_TASKS_PATH.write_text(
                    json.dumps(tasks, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as e:
                logger.error("保存定时任务文件失败: %s", e)

        for task in to_execute:
            task_desc = task["task"]
            logger.info("[%s] 定时任务触发: %s — %s", ctx.chat_id, task["id"], task_desc[:50])
            try:
                await _run_task(task_desc, ctx, brain, send_fn)
            except Exception as e:
                logger.error("定时任务执行失败 [%s]: %s", task["id"], e)


async def _run_task(task_description: str, ctx: SenseContext, brain, send_fn) -> None:
    """执行一个定时任务 prompt。

    通过 brain.think() 处理，结果通过 send_fn 发送给 Kevin（如果有输出）。
    """
    prompt = f"[定时任务触发] {task_description}"
    try:
        response = await brain.think(
            user_message=prompt,
            chat_id=ctx.chat_id,
        )
        if response and response.strip():
            await send_fn(ctx.chat_id, response)
    except Exception as e:
        logger.error("定时任务 brain.think 失败: %s", e)


def _should_run(task: dict, now: datetime) -> bool:
    """判断任务是否到了执行时间。"""
    parsed = task.get("schedule_parsed", {})
    last_run_str = task.get("last_run")
    last_run = datetime.fromisoformat(last_run_str) if last_run_str else None

    stype = parsed.get("type")

    if stype == "daily":
        target_time = parsed.get("time", "00:00")
        try:
            h, m = map(int, target_time.split(":"))
        except ValueError:
            return False
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if abs((now - target).total_seconds()) > _CHECK_WINDOW_SECONDS:
            return False
        if last_run and last_run.date() == now.date():
            return False
        return True

    if stype == "interval":
        hours = parsed.get("hours", 0)
        minutes = parsed.get("minutes", 0)
        interval = timedelta(hours=hours, minutes=minutes)
        if interval.total_seconds() < 60:
            return False
        if last_run is None:
            return True
        return (now - last_run) >= interval

    if stype == "once":
        try:
            target = datetime.strptime(parsed["datetime"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            return False
        if abs((now - target).total_seconds()) > _CHECK_WINDOW_SECONDS:
            return False
        if last_run:
            return False
        return True

    return False
