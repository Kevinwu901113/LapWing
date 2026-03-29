"""SelfReflectionAction — 每日慢心跳时回顾前一天对话，生成学习日志。"""

import logging
from datetime import timedelta

from config.settings import RULES_PATH
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

        # 自省后检查规则累积量，达到阈值则触发进化
        if hasattr(brain, "evolution_engine") and brain.evolution_engine is not None:
            try:
                if RULES_PATH.exists():
                    rules_text = RULES_PATH.read_text(encoding="utf-8")
                    rule_count = len([
                        line for line in rules_text.split("\n")
                        if line.strip().startswith("- [")
                    ])
                    if rule_count >= 5:
                        logger.info(f"[{ctx.chat_id}] 规则累积 {rule_count} 条，触发进化")
                        result = await brain.evolution_engine.evolve()
                        if result["success"]:
                            brain.reload_persona()
                            logger.info(
                                f"[{ctx.chat_id}] 进化完成: {result.get('summary', '')}"
                            )
            except Exception as exc:
                logger.error(f"[{ctx.chat_id}] 进化检查失败: {exc}")
