"""定时任务工具 — 基于 reminders 表的统一提醒系统。

模型输出结构化参数（trigger_type + 对应字段），工具做纯数值计算后写入数据库。
ReminderDispatchAction 负责到期检查和消息推送。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.schedule_task")


async def _execute_schedule_task(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    memory = context.memory
    chat_id = context.chat_id

    if not memory or not chat_id:
        return ToolExecutionResult(
            success=False,
            payload={"error": "内部错误：缺少 memory 或 chat_id"},
            reason="missing_context",
        )

    args = request.arguments
    content = str(args.get("content", "")).strip()
    trigger_type = str(args.get("trigger_type", "")).strip()
    execution_mode = str(args.get("execution_mode", "notify")).strip()
    if execution_mode not in ("notify", "agent"):
        execution_mode = "notify"

    if not content:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 content 参数"},
            reason="缺少 content",
        )

    now = datetime.now(timezone.utc)

    if trigger_type == "delay":
        delay_minutes = int(args.get("delay_minutes", 0))
        if delay_minutes <= 0:
            return ToolExecutionResult(
                success=False,
                payload={"error": "delay_minutes 必须大于 0"},
                reason="invalid_delay",
            )
        next_trigger = now + timedelta(minutes=delay_minutes)
        recurrence_type = "once"
        interval_minutes = None
        time_of_day = None
        weekday = None

    elif trigger_type == "daily":
        time_str = str(args.get("time_of_day", "")).strip()
        if not time_str:
            return ToolExecutionResult(
                success=False,
                payload={"error": "daily 类型需要 time_of_day 参数（HH:MM）"},
                reason="missing_time_of_day",
            )
        try:
            h, m = map(int, time_str.split(":"))
            # time_of_day 是台北时间，需转换为 UTC 存储
            from src.core.vitals import _TAIPEI_TZ
            now_taipei = now.astimezone(_TAIPEI_TZ)
            target_taipei = now_taipei.replace(hour=h, minute=m, second=0, microsecond=0)
            if target_taipei <= now_taipei:
                target_taipei = target_taipei + timedelta(days=1)
            next_trigger = target_taipei.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return ToolExecutionResult(
                success=False,
                payload={"error": f"无法解析时间: {time_str}，需要 HH:MM 格式"},
                reason="invalid_time",
            )
        recurrence_type = "daily"
        interval_minutes = None
        time_of_day = time_str
        weekday = None

    elif trigger_type == "once":
        dt_str = str(args.get("once_datetime", "")).strip()
        if not dt_str:
            return ToolExecutionResult(
                success=False,
                payload={"error": "once 类型需要 once_datetime 参数（YYYY-MM-DD HH:MM）"},
                reason="missing_datetime",
            )
        try:
            target = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            # 模型给的时间是台北时间，转换为 UTC
            from src.core.vitals import _TAIPEI_TZ
            next_trigger = target.replace(tzinfo=_TAIPEI_TZ).astimezone(timezone.utc)
        except ValueError:
            return ToolExecutionResult(
                success=False,
                payload={"error": f"无法解析日期时间: {dt_str}，需要 YYYY-MM-DD HH:MM 格式"},
                reason="invalid_datetime",
            )
        recurrence_type = "once"
        interval_minutes = None
        time_of_day = None
        weekday = None

    elif trigger_type == "interval":
        ivl = int(args.get("interval_minutes", 0))
        if ivl <= 0:
            return ToolExecutionResult(
                success=False,
                payload={"error": "interval_minutes 必须大于 0"},
                reason="invalid_interval",
            )
        next_trigger = now + timedelta(minutes=ivl)
        recurrence_type = "interval"
        interval_minutes = ivl
        time_of_day = None
        weekday = None

    else:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"未知 trigger_type: {trigger_type}，支持: delay, daily, once, interval"},
            reason="unknown_trigger_type",
        )

    reminder_id = await memory.add_reminder(
        chat_id=chat_id,
        content=content,
        recurrence_type=recurrence_type,
        next_trigger_at=next_trigger,
        weekday=weekday,
        time_of_day=time_of_day,
        interval_minutes=interval_minutes,
        execution_mode=execution_mode,
    )

    if not reminder_id:
        return ToolExecutionResult(
            success=False,
            payload={"error": "写入数据库失败"},
            reason="db_error",
        )

    scheduler = context.services.get("reminder_scheduler")
    if scheduler is not None:
        scheduler.notify_new(
            reminder_id=reminder_id,
            chat_id=chat_id,
            content=content,
            next_trigger_at=next_trigger,
            recurrence_type=recurrence_type,
            interval_minutes=interval_minutes,
            execution_mode=execution_mode,
        )

    time_desc = _human_readable_time(trigger_type, args, next_trigger)
    output = f"已设置提醒：{content}（{time_desc}）"

    logger.info("提醒已创建: #%d chat=%s trigger=%s content=%s", reminder_id, chat_id, trigger_type, content[:50])

    return ToolExecutionResult(
        success=True,
        payload={"output": output, "reminder_id": reminder_id},
    )


async def _execute_list_scheduled_tasks(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    memory = context.memory
    chat_id = context.chat_id

    if not memory or not chat_id:
        return ToolExecutionResult(
            success=False,
            payload={"error": "内部错误：缺少 memory 或 chat_id"},
            reason="missing_context",
        )

    reminders = await memory.list_reminders(chat_id)
    if not reminders:
        return ToolExecutionResult(success=True, payload={"output": "当前没有活跃的提醒。"})

    lines = []
    for r in reminders:
        rec = r["recurrence_type"]
        label = {"once": "单次", "daily": "每天", "weekly": "每周", "interval": "周期"}.get(rec, rec)
        next_at = r.get("next_trigger_at", "未知")
        lines.append(f"  [{r['id']}] {r['content'][:60]} — {label} — 下次: {next_at}")

    output = f"活跃提醒 ({len(reminders)} 个):\n\n" + "\n".join(lines)
    return ToolExecutionResult(success=True, payload={"output": output})


async def _execute_cancel_scheduled_task(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    memory = context.memory
    chat_id = context.chat_id

    if not memory or not chat_id:
        return ToolExecutionResult(
            success=False,
            payload={"error": "内部错误：缺少 memory 或 chat_id"},
            reason="missing_context",
        )

    reminder_id = request.arguments.get("reminder_id")
    if reminder_id is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "缺少 reminder_id 参数"},
            reason="missing_reminder_id",
        )

    try:
        rid = int(reminder_id)
    except (TypeError, ValueError):
        return ToolExecutionResult(
            success=False,
            payload={"error": f"reminder_id 必须是整数，收到: {reminder_id}"},
            reason="invalid_reminder_id",
        )

    success = await memory.cancel_reminder(chat_id, rid)
    if not success:
        return ToolExecutionResult(
            success=False,
            payload={"error": f"未找到活跃提醒 #{rid}"},
            reason="not_found",
        )

    scheduler = context.services.get("reminder_scheduler")
    if scheduler is not None:
        scheduler.notify_cancel(rid)

    return ToolExecutionResult(
        success=True,
        payload={"output": f"已取消提醒 #{rid}"},
    )


def _human_readable_time(trigger_type: str, args: dict, next_trigger: datetime) -> str:
    if trigger_type == "delay":
        mins = int(args.get("delay_minutes", 0))
        if mins >= 60 and mins % 60 == 0:
            return f"{mins // 60}小时后"
        return f"{mins}分钟后"
    if trigger_type == "daily":
        return f"每天 {args.get('time_of_day', '?')}"
    if trigger_type == "once":
        return str(args.get("once_datetime", "?"))
    if trigger_type == "interval":
        mins = int(args.get("interval_minutes", 0))
        if mins >= 60 and mins % 60 == 0:
            return f"每隔{mins // 60}小时"
        return f"每隔{mins}分钟"
    return "?"


SCHEDULE_EXECUTORS = {
    "schedule_task": _execute_schedule_task,
    "list_scheduled_tasks": _execute_list_scheduled_tasks,
    "cancel_scheduled_task": _execute_cancel_scheduled_task,
}
