"""InterestProactiveAction - 基于兴趣主动分享内容。"""

import logging
import random

from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.prompt_loader import load_prompt
from src.heartbeat.proactive_filter import filter_proactive_message
from src.tools import web_fetcher, web_search

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

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        # 提高门槛：至少沉默 3 小时（原来 2 小时）
        if ctx.silence_hours < 3.0:
            return
        if ctx.now_taipei_hour >= 23 or ctx.now_taipei_hour < 8:
            return
        # 随机跳过 40%，避免每次心跳都触发
        if random.random() < 0.4:
            return

        try:
            top_interests = await brain.memory.get_top_interests(ctx.chat_id, limit=3)
            if not top_interests:
                return

            topic = top_interests[0]["topic"]
            results = await web_search.search(topic, max_results=3)
            if not results:
                return

            # 先读一篇全文，确保真的理解了再说
            best_result = results[0]
            comprehension_context = ""
            try:
                fetched = await web_fetcher.fetch(best_result.get("url", ""))
                if fetched.success and fetched.text:
                    comprehension_context = f"\n\n全文摘要：\n{fetched.text[:1500]}"
            except Exception:
                pass  # 抓不到全文就用 snippet

            search_results = "\n\n".join(
                f"[{result['title']}]({result['url']})\n{result['snippet']}"
                for result in results
            )
            from src.core.vitals import now_taipei
            now = now_taipei()
            hour = now.hour
            if 5 <= hour < 12:
                period = "早上"
            elif 12 <= hour < 18:
                period = "下午"
            elif 18 <= hour < 23:
                period = "晚上"
            else:
                period = "深夜"
            time_context = f"现在是台北时间{now.strftime('%H:%M')}（{period}）。注意：说话要符合这个时间段。"

            prompt = self._prompt.format(
                topic=topic,
                search_results=search_results + comprehension_context,
                user_facts_summary=ctx.user_facts_summary,
            )
            prompt = f"{time_context}\n\n{prompt}"

            message = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                slot="heartbeat_proactive",
                max_tokens=300,
                session_key=f"chat:{ctx.chat_id}",
                origin="heartbeat.interest_proactive",
            )
            if not message:
                return

            # 质量门控：检查消息是否自然
            passed, reason = await filter_proactive_message(brain.router, message)
            if not passed:
                logger.info(
                    "[%s] 兴趣主动消息未通过质量检查，丢弃: %s — %s",
                    ctx.chat_id, message[:50], reason,
                )
                return

            await send_fn(message)
            event_bus = brain.__dict__.get("event_bus") if hasattr(brain, "__dict__") else None
            if event_bus is not None:
                await event_bus.publish(
                    "interest_proactive",
                    {
                        "chat_id": ctx.chat_id,
                        "text": message,
                        "topic": topic,
                    },
                )

            first = results[0]
            await brain.memory.add_discovery(
                chat_id=ctx.chat_id,
                source="interest_search",
                title=first.get("title", topic),
                summary=message[:500],
                url=first.get("url"),
            )
            # 写入记忆时附加来源标注，帮助后续对话保持一致性
            source_tag = f"\n[source: 基于搜索「{topic}」的结果主动分享，已确认内容]"
            await brain.memory.append(ctx.chat_id, "assistant", message + source_tag)
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
