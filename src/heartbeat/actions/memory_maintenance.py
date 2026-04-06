"""MemoryMaintenanceAction — 每天凌晨归档过期且低重要性的记忆条目。"""

from __future__ import annotations

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.memory_maintenance")


class MemoryMaintenanceAction(HeartbeatAction):
    name = "memory_maintenance"
    description = "归档过期且低重要性的记忆条目"
    beat_types = ["slow"]
    selection_mode = "always"

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        memory_index = getattr(brain, "memory_index", None)
        if memory_index is None:
            return

        archived = memory_index.archive_stale(max_age_days=90, min_importance=0.2)
        if archived:
            logger.info("[%s] 归档了 %d 条过期记忆", ctx.chat_id, len(archived))
