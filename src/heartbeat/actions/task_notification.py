"""TaskNotificationAction — 检查任务流通知队列，主动向用户汇报进展。"""

from __future__ import annotations

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.reasoning_tags import strip_internal_thinking_tags

logger = logging.getLogger("lapwing.heartbeat.task_notification")


class TaskNotificationAction(HeartbeatAction):
    name = "task_notification"
    description = "检查任务流进展并主动汇报"
    beat_types = ["fast"]
    selection_mode = "always"

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        flow_manager = getattr(brain, "task_flow_manager", None)
        if flow_manager is None:
            return

        # 收集属于当前 chat 的通知（不属于的放回）
        deferred: list[dict] = []
        notifs: list[dict] = []
        while not flow_manager.notification_queue.empty():
            try:
                notif = flow_manager.notification_queue.get_nowait()
            except Exception:
                break
            if notif.get("chat_id") == ctx.chat_id:
                notifs.append(notif)
            else:
                deferred.append(notif)

        for notif in deferred:
            await flow_manager.notification_queue.put(notif)

        if not notifs:
            return

        # 只汇报最后一条（避免刷屏；中间状态已折叠）
        notif = notifs[-1]
        completed_count = len(notif.get("completed_steps", []))
        remaining = notif.get("remaining_steps", [])
        remaining_text = "、".join(remaining) if remaining else "无"

        prompt = (
            f"你正在执行的任务「{notif['title']}」有进展：\n"
            f"{notif['message']}\n\n"
            f"已完成：{completed_count} 步\n"
            f"剩余：{remaining_text}\n\n"
            f"用你自己的方式简短告诉 Kevin，一两句话就好。"
        )

        try:
            reply = await brain.router.query_lightweight(
                system=brain.system_prompt,
                user=prompt,
                slot="main_conversation",
            )
            reply = strip_internal_thinking_tags(reply)
            await send_fn(ctx.chat_id, reply)
        except Exception as e:
            logger.warning("任务通知生成失败: %s", e)
