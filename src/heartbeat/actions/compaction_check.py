"""CompactionCheckAction — 快心跳中检查并触发对话压缩。"""

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.compaction_check")


class CompactionCheckAction(HeartbeatAction):
    name = "compaction_check"
    description = "检查对话窗口是否需要压缩"
    beat_types = ["fast"]
    selection_mode = "always"  # 每次快心跳都检查

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        if not hasattr(brain, "compactor") or brain.compactor is None:
            return
        try:
            compacted = await brain.compactor.try_compact(ctx.chat_id)
            if compacted:
                logger.info(f"[{ctx.chat_id}] 快心跳触发 Compaction 完成")
        except Exception as exc:
            logger.error(f"[{ctx.chat_id}] 快心跳 Compaction 失败: {exc}")
