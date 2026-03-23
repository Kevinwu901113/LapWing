"""ProactiveMessageAction — 主动联系用户。"""

import logging
from src.core.heartbeat import HeartbeatAction, SenseContext, _escape_braces
from src.core.prompt_loader import load_prompt

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

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        try:
            discoveries = await brain.memory.get_unshared_discoveries(ctx.chat_id, limit=3)
            discoveries_summary = self._format_discoveries(discoveries)

            # user_facts_summary and discoveries_summary come from user/DB content — escape { }
            prompt = self._prompt.format(
                now=ctx.now.strftime("%Y-%m-%d %H:%M %Z"),
                silence_hours=ctx.silence_hours,
                user_facts_summary=_escape_braces(ctx.user_facts_summary),
                discoveries_summary=_escape_braces(discoveries_summary),
            )

            reply = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=200,
            )

            if not reply:
                return

            await bot.send_message(chat_id=ctx.chat_id, text=reply)
            await brain.memory.append(ctx.chat_id, "assistant", reply)

            for d in discoveries:
                await brain.memory.mark_discovery_shared(d["id"])

            logger.info(f"[{ctx.chat_id}] 主动消息已发送，长度: {len(reply)}")

        except Exception as e:
            logger.error(f"[{ctx.chat_id}] 主动消息发送失败: {e}")

    def _format_discoveries(self, discoveries: list[dict]) -> str:
        if not discoveries:
            return ""
        lines = []
        for d in discoveries:
            line = f"- {d['title']}: {d['summary']}"
            if d.get("url"):
                line += f" ({d['url']})"
            lines.append(line)
        return "\n".join(lines)
