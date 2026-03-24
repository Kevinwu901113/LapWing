"""InterestProactiveAction - 基于兴趣主动分享内容。"""

import logging

from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.prompt_loader import load_prompt
from src.tools import web_search

logger = logging.getLogger("lapwing.heartbeat.interest_proactive")


class InterestProactiveAction(HeartbeatAction):
    """基于用户兴趣主动搜索并分享相关内容。"""

    name = "interest_proactive"
    description = "基于用户兴趣图谱，搜索并主动分享相关内容"
    beat_types = ["fast"]

    def __init__(self) -> None:
        self._prompt_template: str | None = None

    @property
    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("heartbeat_interest_proactive")
        return self._prompt_template

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        if ctx.silence_hours < 2.0:
            return
        if ctx.now.hour >= 23 or ctx.now.hour < 7:
            return

        try:
            top_interests = await brain.memory.get_top_interests(ctx.chat_id, limit=3)
            if not top_interests:
                return

            topic = top_interests[0]["topic"]
            results = await web_search.search(topic, max_results=3)
            if not results:
                return

            search_results = "\n\n".join(
                f"[{result['title']}]({result['url']})\n{result['snippet']}"
                for result in results
            )
            prompt = self._prompt.format(
                topic=topic,
                search_results=search_results,
                user_facts_summary=ctx.user_facts_summary,
            )

            message = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=300,
            )
            if not message:
                return

            await bot.send_message(chat_id=ctx.chat_id, text=message)

            first = results[0]
            await brain.memory.add_discovery(
                chat_id=ctx.chat_id,
                source="interest_search",
                title=first.get("title", topic),
                summary=message[:500],
                url=first.get("url"),
            )
            await brain.memory.append(ctx.chat_id, "assistant", message)
            await brain.memory.decay_interests(ctx.chat_id, factor=0.9)
            if hasattr(brain, "knowledge_manager") and brain.knowledge_manager is not None:
                brain.knowledge_manager.save_note(
                    topic=topic,
                    source_url=first.get("url", ""),
                    content=message,
                )
            logger.info(f"[{ctx.chat_id}] 已发送兴趣驱动主动消息，topic={topic!r}")
        except Exception as exc:
            logger.error(f"[{ctx.chat_id}] 兴趣主动分享失败: {exc}")
