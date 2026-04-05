"""ReminderScheduler — 基于 asyncio 的精确提醒调度器。

与 HeartbeatEngine 完全独立。不使用 APScheduler，不调用 LLM。
提醒到期时直接发送消息，延迟从分钟级降至秒级。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("lapwing.core.reminder_scheduler")


class ReminderScheduler:
    """事件驱动的提醒调度器。

    生命周期：
      1. AppContainer 创建实例，注入 memory + send_fn + event_bus
      2. container.start() 时调用 start()，从 DB 加载所有 active reminders
      3. schedule_task 工具创建提醒后调用 notify_new()
      4. cancel_scheduled_task 工具取消后调用 notify_cancel()
      5. container.shutdown() 时调用 shutdown()

    调度机制：
      - 每个 pending reminder 持有一个 asyncio.Task
      - Task 内部 sleep 到 next_trigger_at，然后触发
      - 触发后更新 DB，若为循环提醒则重新调度下一次
    """

    def __init__(self, memory, send_fn, event_bus=None) -> None:
        self._memory = memory
        self._send_fn = send_fn
        self._event_bus = event_bus
        self._tasks: dict[int, asyncio.Task] = {}  # reminder_id → Task
        self._started = False

    async def start(self) -> None:
        """启动时从 DB 加载所有 active reminders 并调度。"""
        if self._started:
            return

        self._started = True
        try:
            chat_ids = await self._memory.get_all_chat_ids()
            total = 0
            for chat_id in chat_ids:
                reminders = await self._memory.list_reminders(chat_id)
                for r in reminders:
                    if r.get("active"):
                        self._schedule_reminder(r)
                        total += 1
            logger.info("ReminderScheduler 已启动，加载 %d 个活跃提醒", total)
        except Exception as exc:
            logger.error("ReminderScheduler 启动加载失败: %s", exc)

    async def shutdown(self) -> None:
        """取消所有待执行的 Task。"""
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._started = False
        logger.info("ReminderScheduler 已关闭")

    def notify_new(
        self,
        reminder_id: int,
        chat_id: str,
        content: str,
        next_trigger_at: datetime,
        recurrence_type: str,
        interval_minutes: int | None = None,
    ) -> None:
        """schedule_task 工具创建提醒后调用，立即注册调度。

        调用方无需 await——内部用 create_task 调度。
        """
        if not self._started:
            return

        reminder = {
            "id": reminder_id,
            "chat_id": chat_id,
            "content": content,
            "next_trigger_at": (
                next_trigger_at.isoformat()
                if isinstance(next_trigger_at, datetime)
                else next_trigger_at
            ),
            "recurrence_type": recurrence_type,
            "interval_minutes": interval_minutes,
            "active": True,
        }
        self._schedule_reminder(reminder)
        logger.info("已注册提醒 #%d（%s）到调度器", reminder_id, recurrence_type)

    def notify_cancel(self, reminder_id: int) -> None:
        """cancel_scheduled_task 工具取消提醒后调用。"""
        task = self._tasks.pop(reminder_id, None)
        if task is not None:
            task.cancel()
            logger.info("已从调度器移除提醒 #%d", reminder_id)

    # ── 内部实现 ──────────────────────────────────────────────────

    def _schedule_reminder(self, reminder: dict) -> None:
        """为一个 reminder 创建等待 Task。如已有旧 Task 则先取消。"""
        rid = int(reminder["id"])

        old_task = self._tasks.pop(rid, None)
        if old_task is not None:
            old_task.cancel()

        task = asyncio.create_task(
            self._wait_and_fire(reminder),
            name=f"reminder-{rid}",
        )
        self._tasks[rid] = task
        task.add_done_callback(lambda t: self._tasks.pop(rid, None))

    async def _wait_and_fire(self, reminder: dict) -> None:
        """Sleep 到触发时间，然后发送提醒。"""
        rid = int(reminder["id"])
        chat_id = str(reminder["chat_id"])
        content = str(reminder.get("content", ""))
        recurrence = str(reminder.get("recurrence_type", "once"))

        try:
            next_dt = self._parse_dt(reminder["next_trigger_at"])
            delay = self._compute_delay(next_dt)

            if delay > 0:
                await asyncio.sleep(delay)

            now = datetime.now(timezone.utc)
            message = f"⏰ {content}"

            try:
                await self._send_fn(message)
            except Exception as send_exc:
                logger.error("[#%d] 提醒发送失败: %s", rid, send_exc)
                return

            # 记录到对话历史
            try:
                await self._memory.append(chat_id, "assistant", message)
            except Exception as mem_exc:
                logger.warning("[#%d] 提醒写入记忆失败: %s", rid, mem_exc)

            # 通知 desktop 端
            if self._event_bus is not None:
                try:
                    await self._event_bus.publish(
                        "reminder_message",
                        {"chat_id": chat_id, "text": message},
                    )
                except Exception as bus_exc:
                    logger.warning("[#%d] event_bus 发布失败: %s", rid, bus_exc)

            # 更新 DB（完成或重排下一次）
            try:
                await self._memory.complete_or_reschedule_reminder(rid, now=now)
            except Exception as db_exc:
                logger.error("[#%d] 提醒状态更新失败: %s", rid, db_exc)
                return

            logger.info("[%s] 提醒 #%d 已发送: %s", chat_id, rid, content[:50])

            # 循环提醒：重新调度下一次
            if recurrence != "once":
                await self._reload_and_reschedule(rid)

        except asyncio.CancelledError:
            pass  # 正常取消，不记日志
        except Exception as exc:
            logger.exception("[#%d] 提醒调度异常: %s", rid, exc)

    async def _reload_and_reschedule(self, reminder_id: int) -> None:
        """从 DB 重新读取 reminder 并调度下一次触发。"""
        try:
            reminder = await self._memory.get_reminder_by_id(reminder_id)
            if reminder is None:
                return  # 已被取消或设为不活跃
            self._schedule_reminder(reminder)
        except Exception as exc:
            logger.error("[#%d] 重新加载提醒失败: %s", reminder_id, exc)

    @staticmethod
    def _parse_dt(value) -> datetime:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _compute_delay(target: datetime) -> float:
        now = datetime.now(timezone.utc)
        delta = (target - now).total_seconds()
        return max(delta, 0)
