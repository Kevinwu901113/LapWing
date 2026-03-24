"""PromptEvolutionAction — 每周日慢心跳时自动优化人格 prompt。"""

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext

logger = logging.getLogger("lapwing.heartbeat.prompt_evolution")


class PromptEvolutionAction(HeartbeatAction):
    name = "prompt_evolution"
    description = "每周根据学习日志自动优化 Lapwing 人格 prompt"
    beat_types = ["slow"]

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        if not hasattr(brain, "prompt_evolver") or brain.prompt_evolver is None:
            return

        # 只在周日触发
        if ctx.now.weekday() != 6:
            return

        logger.info(f"[prompt_evolution] 周日自动进化触发 [{ctx.chat_id}]")
        try:
            result = await brain.prompt_evolver.evolve()
            if result["success"]:
                logger.info(f"[prompt_evolution] 进化完成: {result.get('changes_summary', '')}")
            else:
                logger.warning(f"[prompt_evolution] 进化未完成: {result.get('error', '')}")
        except Exception as exc:
            logger.error(f"[prompt_evolution] 进化失败: {exc}")
