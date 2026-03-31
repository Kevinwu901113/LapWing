"""Session 清理 heartbeat action — 清除过期的休眠 session。"""

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.session_reaper")


class SessionReaperAction(HeartbeatAction):
    name = "session_reaper"
    description = "清理过期的对话会话（休眠超时删除 + 总量控制）"
    beat_types = ["slow"]
    selection_mode = "always"

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        if brain.session_manager is None:
            return
        try:
            condensed, deleted = await brain.session_manager.reap_expired(ctx.chat_id)
            if condensed > 0 or deleted > 0:
                logger.info(
                    f"[{ctx.chat_id}] Session reaper: {condensed} 个压缩归档，{deleted} 个删除"
                )
        except Exception as exc:
            logger.error(f"[{ctx.chat_id}] Session reaper 执行失败: {exc}")
