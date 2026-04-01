"""ProactiveMessageAction — 主动联系用户。"""

import logging
from datetime import datetime

from config.settings import REMINDER_DISPATCH_GRACE_SECONDS, REMINDER_MAX_DUE_PER_CHAT
from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.prompt_loader import load_prompt
from src.core.reasoning_tags import strip_internal_thinking_tags

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
            discoveries = await brain.memory.get_unshared_discoveries(ctx.chat_id, limit=3)
            discoveries_summary = self._format_discoveries(discoveries)

            prompt = self._prompt.format(
                now=ctx.now.strftime("%Y-%m-%d %H:%M %Z"),
                silence_hours=ctx.silence_hours,
                user_facts_summary=ctx.user_facts_summary,
                discoveries_summary=discoveries_summary,
            )

            reply = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                slot="heartbeat_proactive",
                max_tokens=200,
                session_key=f"chat:{ctx.chat_id}",
                origin="heartbeat.proactive_message",
            )

            if not reply:
                return
            reply = strip_internal_thinking_tags(reply)
            if not reply:
                return

            await send_fn(reply)
            await brain.memory.append(ctx.chat_id, "assistant", reply)
            event_bus = brain.__dict__.get("event_bus") if hasattr(brain, "__dict__") else None
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
        lines = []
        for d in discoveries:
            line = f"- {d['title']}: {d['summary']}"
            if d.get("url"):
                line += f" ({d['url']})"
            lines.append(line)
        return "\n".join(lines)


class ReminderDispatchAction(HeartbeatAction):
    name = "reminder_dispatch"
    description = "派发到期提醒任务"
    beat_types = ["minute"]
    selection_mode = "always"

    def __init__(self) -> None:
        self._prompt_template: str | None = None

    @property
    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("heartbeat_proactive")
        return self._prompt_template

    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
        reminders = await brain.memory.get_due_reminders(
            ctx.chat_id,
            now=ctx.now,
            grace_seconds=REMINDER_DISPATCH_GRACE_SECONDS,
            limit=REMINDER_MAX_DUE_PER_CHAT,
        )
        if not reminders:
            return

        for reminder in reminders:
            try:
                reminder_summary = self._format_reminder(reminder)
                prompt = self._prompt.format(
                    now=ctx.now.strftime("%Y-%m-%d %H:%M %Z"),
                    silence_hours=ctx.silence_hours,
                    user_facts_summary=ctx.user_facts_summary,
                    discoveries_summary=reminder_summary,
                )
                message = await brain.router.complete(
                    [{"role": "user", "content": prompt}],
                    slot="heartbeat_proactive",
                    max_tokens=120,
                    session_key=f"chat:{ctx.chat_id}",
                    origin="heartbeat.reminder_dispatch",
                )
                if message:
                    message = strip_internal_thinking_tags(message)
                if not message:
                    message = f"提醒你：{reminder['content']}"

                await send_fn(message)
                await brain.memory.append(ctx.chat_id, "assistant", message)
                await brain.memory.complete_or_reschedule_reminder(reminder["id"], now=ctx.now)

                event_bus = brain.__dict__.get("event_bus") if hasattr(brain, "__dict__") else None
                if event_bus is not None:
                    await event_bus.publish(
                        "reminder_message",
                        {
                            "chat_id": ctx.chat_id,
                            "text": message,
                        },
                    )

                logger.info(
                    f"[{ctx.chat_id}] 已发送提醒 #{reminder['id']} ({reminder['recurrence_type']})"
                )
            except Exception as exc:
                logger.error(f"[{ctx.chat_id}] 提醒派发失败 #{reminder.get('id')}: {exc}")

    def _format_reminder(self, reminder: dict) -> str:
        recurrence = self._recurrence_label(reminder.get("recurrence_type"))
        next_trigger = self._format_time(reminder.get("next_trigger_at"))
        content = str(reminder.get("content", "")).strip()
        return (
            "[到期提醒]\n"
            f"内容：{content}\n"
            f"类型：{recurrence}\n"
            f"计划触发时间（UTC）：{next_trigger}"
        )

    def _recurrence_label(self, recurrence_type: str | None) -> str:
        mapping = {
            "once": "一次性",
            "daily": "每天",
            "weekly": "每周",
        }
        return mapping.get(str(recurrence_type or "").lower(), "一次性")

    def _format_time(self, value: str | None) -> str:
        if not value:
            return "未知"
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return value
