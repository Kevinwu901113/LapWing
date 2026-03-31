"""AutoMemoryAction — 自动记忆提取心跳 action。

触发条件：15 分钟无活动 + 距上次提取 >= 30 分钟。
每次快心跳检查一次，符合条件时提取。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.auto_memory")

_MIN_IDLE_MINUTES = 15
_MIN_EXTRACT_INTERVAL = timedelta(minutes=30)

# 全局记录每个 chat_id 的上次提取时间
_last_extraction: dict[str, datetime] = {}


class AutoMemoryAction(HeartbeatAction):
    """在用户沉默一段时间后，自动提取对话中值得记住的信息。"""

    name = "auto_memory_extract"
    description = "自动从近期对话中提取值得长期记忆的信息"
    beat_types = ["fast"]
    selection_mode = "always"

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        # 条件 1：距上次对话至少 15 分钟
        if ctx.silence_hours * 60 < _MIN_IDLE_MINUTES:
            logger.debug("[%s] 用户仍活跃（沉默 %.1f 分钟），跳过自动提取",
                         ctx.chat_id, ctx.silence_hours * 60)
            return

        # 条件 2：距上次提取至少 30 分钟
        last = _last_extraction.get(ctx.chat_id)
        now = datetime.now(timezone.utc)
        if last is not None and (now - last) < _MIN_EXTRACT_INTERVAL:
            logger.debug("[%s] 距上次提取不足 30 分钟，跳过", ctx.chat_id)
            return

        # 获取近期对话消息
        try:
            messages = await brain.memory.get(ctx.chat_id)
        except Exception as e:
            logger.error("[%s] 获取对话历史失败: %s", ctx.chat_id, e)
            return

        if not messages or len(messages) < 4:
            logger.debug("[%s] 对话太短，跳过自动提取", ctx.chat_id)
            return

        # 执行提取
        extractor = getattr(brain, "auto_memory_extractor", None)
        if extractor is None:
            logger.warning("[%s] brain.auto_memory_extractor 未初始化", ctx.chat_id)
            return

        try:
            results = await extractor.extract_from_messages(messages)
            _last_extraction[ctx.chat_id] = now
            if results:
                logger.info("[%s] 自动提取了 %d 条记忆", ctx.chat_id, len(results))
        except Exception as e:
            logger.error("[%s] 自动记忆提取失败: %s", ctx.chat_id, e)
