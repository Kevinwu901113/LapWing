"""PromptEvolutionAction — 每周日慢心跳时自动进化人格（diff-based）。"""

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.prompt_evolution")


class PromptEvolutionAction(HeartbeatAction):
    name = "prompt_evolution"
    description = "每周根据学习日志和行为规则自动微进化 Lapwing 人格"
    beat_types = ["slow"]

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        if not hasattr(brain, "evolution_engine") or brain.evolution_engine is None:
            return

        # 只在周日触发深度进化
        if ctx.now.weekday() != 6:
            return

        logger.info(f"[prompt_evolution] 周日深度进化触发 [{ctx.chat_id}]")
        try:
            result = await brain.evolution_engine.evolve()
            if result["success"]:
                brain.reload_persona()
                logger.info(f"[prompt_evolution] 进化完成: {result.get('summary', '')}")
            else:
                logger.info(f"[prompt_evolution] 进化未执行: {result.get('error', '')}")
        except Exception as exc:
            logger.error(f"[prompt_evolution] 进化失败: {exc}")
