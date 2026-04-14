"""MemoryMaintenanceAction — 每天凌晨归档过期且低重要性的记忆条目。"""

from __future__ import annotations

import glob
import logging
import os
import time

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.memory_maintenance")

_TOOL_RESULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "tool_results",
)
_TOOL_RESULT_MAX_AGE_SECONDS = 86400  # 24 小时


class MemoryMaintenanceAction(HeartbeatAction):
    name = "memory_maintenance"
    description = "归档过期记忆条目，清理旧工具结果缓存"
    beat_types = ["slow"]
    selection_mode = "always"

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        memory_index = getattr(brain, "memory_index", None)
        if memory_index is None:
            return

        archived = memory_index.archive_stale(max_age_days=90, min_importance=0.2)
        if archived:
            logger.info("[%s] 归档了 %d 条过期记忆", ctx.chat_id, len(archived))

        # 清理 data/tool_results/ 下超过 24 小时的文件
        self._cleanup_tool_results()

        # 归档已解决的 incident（30 天后）
        incident_manager = getattr(brain, "incident_manager", None)
        if incident_manager is not None:
            try:
                archived_inc = incident_manager.archive_resolved(max_age_days=30)
                if archived_inc:
                    logger.info("归档了 %d 个已解决的 incident", archived_inc)
            except Exception:
                logger.debug("incident 归档失败", exc_info=True)

        # 清理过期的 tactical rules（60 天后）
        tactical_rules = getattr(brain, "tactical_rules", None)
        if tactical_rules is not None and hasattr(tactical_rules, "cleanup_stale_rules"):
            try:
                cleaned = await tactical_rules.cleanup_stale_rules(max_age_days=60)
                if cleaned:
                    logger.info("清理了 %d 条过期规则", cleaned)
            except Exception:
                logger.debug("tactical rules 清理失败", exc_info=True)

    @staticmethod
    def _cleanup_tool_results() -> None:
        if not os.path.isdir(_TOOL_RESULT_DIR):
            return
        cutoff = time.time() - _TOOL_RESULT_MAX_AGE_SECONDS
        removed = 0
        for f in glob.glob(os.path.join(_TOOL_RESULT_DIR, "*.txt")):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
                    removed += 1
            except OSError:
                pass
        if removed:
            logger.info("[memory_maintenance] 清理了 %d 个过期工具结果文件", removed)
