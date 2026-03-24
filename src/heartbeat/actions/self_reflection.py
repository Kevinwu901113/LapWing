"""SelfReflectionAction — 每日慢心跳时回顾前一天对话，生成学习日志。"""

import logging
from datetime import timedelta

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.self_reflection")


class SelfReflectionAction(HeartbeatAction):
    name = "self_reflection"
    description = "每日回顾对话表现，提取经验，写入学习日志"
    beat_types = ["slow"]

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        if not hasattr(brain, "self_reflection") or brain.self_reflection is None:
            return

        # 回顾昨天（慢心跳通常在凌晨，回顾前一天）
        yesterday = (ctx.now - timedelta(days=1)).strftime("%Y-%m-%d")
        today = ctx.now.strftime("%Y-%m-%d")

        for date_str in (yesterday, today):
            try:
                result = await brain.self_reflection.reflect_on_day(ctx.chat_id, date_str)
                if result:
                    logger.info(f"[{ctx.chat_id}] 自省完成: {date_str}")
            except Exception as exc:
                logger.error(f"[{ctx.chat_id}] 自省失败 ({date_str}): {exc}")
