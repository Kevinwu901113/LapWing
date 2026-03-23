"""MemoryConsolidationAction — 整理和压缩长期记忆。"""

import logging
from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.heartbeat.consolidation")

_HISTORY_WINDOW = 50


class MemoryConsolidationAction(HeartbeatAction):
    name = "memory_consolidation"
    description = "整理近期对话，生成记忆摘要，并深度提取用户信息"
    beat_types = ["slow"]

    def __init__(self) -> None:
        self._prompt_template: str | None = None

    @property
    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("heartbeat_consolidation")
        return self._prompt_template

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        try:
            history = await brain.memory.get(ctx.chat_id)
            recent = history[-_HISTORY_WINDOW:] if len(history) > _HISTORY_WINDOW else history

            if not recent:
                logger.debug(f"[{ctx.chat_id}] 无对话历史，跳过记忆整理")
                return

            conversation_text = "\n".join(
                f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
                for m in recent
            )

            # conversation_text 来自数据库，可能含 { } — 用 replace 替换，不用 .format()
            prompt = self._prompt.replace("{conversation}", conversation_text)

            summary = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=200,
            )

            date_str = ctx.now.strftime("%Y-%m-%d")
            await brain.memory.set_user_fact(
                ctx.chat_id,
                f"memory_summary_{date_str}",
                summary,
            )

            await brain.fact_extractor.force_extraction(ctx.chat_id)

            logger.info(f"[{ctx.chat_id}] 记忆整理完成，摘要长度: {len(summary)}")

        except Exception as e:
            logger.error(f"[{ctx.chat_id}] 记忆整理失败: {e}")
