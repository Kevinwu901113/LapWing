"""MemoryConsolidationAction — 深度提取用户信息（摘要由 Compactor 负责）。"""

import logging
from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.consolidation")


class MemoryConsolidationAction(HeartbeatAction):
    name = "memory_consolidation"
    description = "深度提取用户信息"
    beat_types = ["slow"]

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        try:
            history = await brain.memory.get(ctx.chat_id)
            if not history:
                logger.debug(f"[{ctx.chat_id}] 无对话历史，跳过用户信息提取")
                return

            await brain.fact_extractor.force_extraction(ctx.chat_id)
            logger.info(f"[{ctx.chat_id}] 用户信息深度提取完成")

        except Exception as e:
            logger.error(f"[{ctx.chat_id}] 用户信息提取失败: {e}")
