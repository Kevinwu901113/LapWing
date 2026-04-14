"""ProactiveMessageAction — 主动联系用户。"""

import logging
from datetime import datetime

from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.prompt_loader import load_prompt
from src.heartbeat.proactive_filter import filter_proactive_message

logger = logging.getLogger("lapwing.heartbeat.proactive")


class ProactiveMessageAction(HeartbeatAction):
    name = "proactive_message"
    description = "主动给用户发一条关心或问候的消息，适合用户长时间未联系时"
    beat_types = ["fast"]

    def __init__(self) -> None:
        self._prompt_template: str | None = None

    @property
    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("heartbeat_proactive")
        return self._prompt_template

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        try:
            discoveries = await brain.memory.get_unshared_discoveries(ctx.chat_id, limit=1)
            discoveries_summary = self._format_discoveries(discoveries)

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

            prompt = self._prompt.format(
                now=f"{now.strftime('%Y-%m-%d %H:%M')} 台北时间（{period}）",
                silence_hours=ctx.silence_hours,
                user_facts_summary=ctx.user_facts_summary,
                discoveries_summary=discoveries_summary,
            )

            reply = await brain.compose_proactive(
                purpose="主动消息",
                context_prompt=prompt,
                sense_context={
                    "沉默时长": f"{ctx.silence_hours:.1f}小时",
                    "当前时段": period,
                    "当前时间": now.strftime("%H:%M"),
                },
                max_tokens=200,
                chat_id=ctx.chat_id,
            )

            if not reply:
                return

            # 质量门控
            passed, reason = await filter_proactive_message(brain.router, reply)
            if not passed:
                logger.info(
                    "[%s] 主动消息未通过质量检查，丢弃: %s — %s",
                    ctx.chat_id, reply[:50], reason,
                )
                return

            await send_fn(reply)
            await brain.memory.append(ctx.chat_id, "assistant", reply)
            event_bus = getattr(brain, "event_bus", None)
            if event_bus is not None:
                await event_bus.publish(
                    "proactive_message",
                    {
                        "chat_id": ctx.chat_id,
                        "text": reply,
                    },
                )

            for d in discoveries:
                await brain.memory.mark_discovery_shared(d["id"])

            logger.info(f"[{ctx.chat_id}] 主动消息已发送，长度: {len(reply)}")

        except Exception as e:
            logger.error(f"[{ctx.chat_id}] 主动消息发送失败: {e}")

    def _format_discoveries(self, discoveries: list[dict]) -> str:
        if not discoveries:
            return ""
        d = discoveries[0]
        return f"你最近看到的一个东西：{d['title']}"
