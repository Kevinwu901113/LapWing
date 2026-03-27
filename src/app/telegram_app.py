"""Telegram 适配层：命令、消息处理与生命周期桥接。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from config.settings import MAX_REPLY_LENGTH, MESSAGE_BUFFER_SECONDS, SKILLS_COMMANDS_ENABLED

logger = logging.getLogger("lapwing.app.telegram")


class TelegramApp:
    """将 Telegram 交互适配到 AppContainer。"""

    def __init__(self, container) -> None:
        self._container = container
        self._bot = None
        self._message_buffers: dict[str, list[str]] = {}
        self._buffer_tasks: dict[str, asyncio.Task] = {}
        self._buffer_updates: dict[str, object] = {}
        self._skill_shortcut_pattern = re.compile(r"^/([a-z0-9-]+):(.*)$", flags=re.IGNORECASE)

    @staticmethod
    def _import_telegram():
        try:
            from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "当前环境缺少 telegram 依赖。请安装：pip install python-telegram-bot"
            ) from exc
        return Application, CommandHandler, ContextTypes, MessageHandler, filters

    @staticmethod
    def _visible_user_facts(facts: list[dict]) -> list[dict]:
        return [
            fact for fact in facts
            if not str(fact.get("fact_key", "")).startswith("memory_summary_")
        ]

    async def _post_init(self, application) -> None:
        self._bot = application.bot
        await self._container.start(bot=application.bot)

    async def _post_shutdown(self, application) -> None:
        await self._container.shutdown()

    def build_application(self, *, token: str, proxy_url: str = ""):
        Application, _, _, _, _ = self._import_telegram()
        builder = Application.builder().token(token)
        if proxy_url:
            from telegram.request import HTTPXRequest

            proxy_request = HTTPXRequest(proxy=proxy_url)
            builder = builder.request(proxy_request).get_updates_request(proxy_request)
            logger.info("使用代理: %s", proxy_url)

        app = builder.post_init(self._post_init).post_shutdown(self._post_shutdown).build()
        self._register_handlers(app)
        return app

    def _register_handlers(self, app) -> None:
        _, CommandHandler, _, MessageHandler, filters = self._import_telegram()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("reload", self.cmd_reload))
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("clear", self.cmd_clear))
        app.add_handler(CommandHandler("forget", self.cmd_forget))
        app.add_handler(CommandHandler("evolve", self.cmd_evolve))
        app.add_handler(CommandHandler("memory", self.cmd_memory))
        app.add_handler(CommandHandler("interests", self.cmd_interests))
        app.add_handler(CommandHandler("skill", self.cmd_skill))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.handle_voice))

    async def cmd_start(self, update, context) -> None:
        if update.message:
            await update.message.reply_text("你好，我是 Lapwing。有什么想聊的吗？")

    async def cmd_reload(self, update, context) -> None:
        self._container.brain.reload_persona()
        self._container.brain.reload_skills()
        if update.message:
            await update.message.reply_text("人格与技能目录已重新加载。")

    async def cmd_new(self, update, context) -> None:
        if not update.message:
            return
        chat_id = str(update.message.chat_id)
        await self._container.brain.clear_short_term_memory(chat_id)
        await update.message.reply_text("好的，已清空当前对话上下文，我们可以重新开始。")

    async def cmd_clear(self, update, context) -> None:
        await self.cmd_new(update, context)

    async def cmd_forget(self, update, context) -> None:
        if not update.message:
            return
        chat_id = str(update.message.chat_id)
        await self._container.brain.clear_all_memory(chat_id)
        await update.message.reply_text("好的，我已经清空这个 chat 的所有记忆（短期 + 长期）。")

    async def cmd_evolve(self, update, context) -> None:
        if not update.message:
            return

        brain = self._container.brain
        if not hasattr(brain, "prompt_evolver") or brain.prompt_evolver is None:
            await update.message.reply_text("prompt 进化功能尚未启用。")
            return

        args = context.args or []
        if args and args[0] == "revert":
            result = await brain.prompt_evolver.revert()
            if result["success"]:
                brain.reload_persona()
                await update.message.reply_text(f"已回滚到: {result['reverted_to']}")
            else:
                await update.message.reply_text(f"回滚失败: {result.get('error', '未知错误')}")
            return

        await update.message.reply_text("开始分析学习日志，优化人格 prompt……")
        result = await brain.prompt_evolver.evolve()
        if result["success"]:
            brain.reload_persona()
            await update.message.reply_text(
                f"优化完成。\n变更摘要: {result.get('changes_summary', '（无）')}"
            )
        else:
            await update.message.reply_text(f"优化未完成: {result.get('error', '未知错误')}")

    async def cmd_interests(self, update, context) -> None:
        if not update.message:
            return
        chat_id = str(update.message.chat_id)
        interests = await self._container.brain.memory.get_top_interests(chat_id, limit=10)
        if not interests:
            await update.message.reply_text("我还没有记录到明显的兴趣话题。")
            return

        lines = ["你目前记录的兴趣话题："]
        for index, interest in enumerate(interests, start=1):
            lines.append(f"{index}. {interest['topic']}（权重 {interest['weight']:.1f}）")
        await update.message.reply_text("\n".join(lines))

    async def cmd_memory(self, update, context) -> None:
        message = update.message
        if message is None:
            return

        chat_id = str(message.chat_id)
        facts = self._visible_user_facts(await self._container.brain.memory.get_user_facts(chat_id))
        args = context.args or []

        if not args:
            if not facts:
                await message.reply_text("我现在还没有记住关于你的信息。")
                return
            lines = ["你记住了以下关于我的信息："]
            for index, fact in enumerate(facts, start=1):
                lines.append(f"{index}. [{fact['fact_key']}] {fact['fact_value']}")
            await message.reply_text("\n".join(lines))
            return

        if len(args) != 2 or args[0].lower() != "delete":
            await message.reply_text("用法：/memory delete <编号>")
            return

        try:
            target_index = int(args[1])
        except ValueError:
            await message.reply_text("用法：/memory delete <编号>")
            return

        if target_index < 1 or target_index > len(facts):
            await message.reply_text("没有这条记忆")
            return

        fact_key = facts[target_index - 1]["fact_key"]
        deleted = await self._container.brain.memory.delete_user_fact(chat_id, fact_key)
        if not deleted:
            await message.reply_text("没有这条记忆")
            return
        await message.reply_text("这条记忆已经删掉了。")

    async def cmd_skill(self, update, context) -> None:
        message = update.message
        if message is None:
            return
        if not SKILLS_COMMANDS_ENABLED:
            await message.reply_text("技能命令当前已关闭。")
            return

        args = context.args or []
        skill_name = ""
        user_input = ""
        raw_user_message = str(message.text or "").strip()

        if args:
            skill_name = str(args[0]).strip().lower().lstrip(":")
            user_input = " ".join(str(item) for item in args[1:]).strip()
        else:
            match = re.match(r"^/skill:([a-z0-9-]+)(?:\s+(.*))?$", raw_user_message, flags=re.IGNORECASE)
            if match:
                skill_name = match.group(1).strip().lower()
                user_input = (match.group(2) or "").strip()

        if not skill_name:
            await message.reply_text("用法：/skill <name> [args]")
            return

        await self._run_skill_command(
            message=message,
            chat_id=str(message.chat_id),
            raw_user_message=raw_user_message,
            skill_name=skill_name,
            user_input=user_input,
        )

    async def _send_reply(self, message, reply: str) -> None:
        if len(reply) <= MAX_REPLY_LENGTH:
            await message.reply_text(reply)
            return

        chunks = [reply[i: i + MAX_REPLY_LENGTH] for i in range(0, len(reply), MAX_REPLY_LENGTH)]
        for index, chunk in enumerate(chunks):
            if index == 0:
                await message.reply_text(chunk)
            else:
                await message.chat.send_message(chunk)

    def _build_status_sender(self):
        async def _send_status(cid: str, text: str) -> None:
            if self._bot:
                try:
                    await self._bot.send_message(chat_id=int(cid), text=text)
                except Exception:
                    pass

        return _send_status

    async def _think_and_reply(self, message, chat_id: str, user_text: str) -> None:
        await message.chat.send_action("typing")
        reply = await self._container.brain.think(
            chat_id,
            user_text,
            status_callback=self._build_status_sender(),
        )
        await self._send_reply(message, reply)
        logger.info("已回复 [%s]，长度: %s", chat_id, len(reply))

    async def _run_skill_command(
        self,
        *,
        message,
        chat_id: str,
        raw_user_message: str,
        skill_name: str,
        user_input: str,
    ) -> None:
        await message.chat.send_action("typing")
        reply = await self._container.brain.run_skill_command(
            chat_id=chat_id,
            raw_user_message=raw_user_message,
            skill_name=skill_name,
            user_input=user_input,
            status_callback=self._build_status_sender(),
        )
        await self._send_reply(message, reply)

    def _parse_skill_shortcut(self, text: str) -> tuple[str, str] | None:
        if not SKILLS_COMMANDS_ENABLED:
            return None
        cleaned = text.strip()
        if not cleaned:
            return None

        # /skill:name [args]
        if cleaned.lower().startswith("/skill:"):
            rest = cleaned[len("/skill:"):].strip()
            if not rest:
                return None
            parts = rest.split(maxsplit=1)
            skill_name = parts[0].strip().lower()
            user_input = parts[1].strip() if len(parts) > 1 else ""
            return skill_name, user_input

        # /<name>:<args>
        match = self._skill_shortcut_pattern.match(cleaned)
        if not match:
            return None
        skill_name = match.group(1).strip().lower()
        user_input = match.group(2).strip()
        if skill_name == "skill":
            return None
        return skill_name, user_input

    async def _flush_buffer(self, chat_id: str) -> None:
        await asyncio.sleep(MESSAGE_BUFFER_SECONDS)

        messages = self._message_buffers.pop(chat_id, [])
        message_obj = self._buffer_updates.pop(chat_id, None)
        self._buffer_tasks.pop(chat_id, None)

        if not messages or message_obj is None:
            return

        combined = "\n".join(messages)
        await self._think_and_reply(message_obj, chat_id, combined)

    def _enqueue_message(self, chat_id: str, text: str, message_obj: Any) -> None:
        self._message_buffers.setdefault(chat_id, []).append(text)
        self._buffer_updates[chat_id] = message_obj

        old_task = self._buffer_tasks.get(chat_id)
        if old_task and not old_task.done():
            old_task.cancel()

        self._buffer_tasks[chat_id] = asyncio.create_task(self._flush_buffer(chat_id))

    async def handle_message(self, update, context) -> None:
        message = update.message
        if not message or not message.text:
            return

        chat_id = str(message.chat_id)
        skill_shortcut = self._parse_skill_shortcut(message.text)
        if skill_shortcut is not None:
            skill_name, user_input = skill_shortcut
            await self._run_skill_command(
                message=message,
                chat_id=chat_id,
                raw_user_message=message.text,
                skill_name=skill_name,
                user_input=user_input,
            )
            return

        self._enqueue_message(chat_id, message.text, message)

    async def handle_voice(self, update, context) -> None:
        from src.tools import transcriber

        message = update.message
        if not message:
            return

        voice = message.voice or message.audio
        if not voice:
            return

        await message.chat.send_action("typing")
        try:
            file = await context.bot.get_file(voice.file_id)
            audio_bytes = await file.download_as_bytearray()
        except Exception:
            await message.reply_text("语音下载失败，请重试。")
            return

        filename = f"voice.{getattr(voice, 'mime_type', 'audio/ogg').split('/')[-1]}"
        user_text = await transcriber.transcribe(bytes(audio_bytes), filename=filename)
        if not user_text:
            await message.reply_text("听不太清楚，能再说一遍吗？")
            return

        await message.reply_text(f"🎤 {user_text}")
        self._enqueue_message(str(message.chat_id), user_text, message)
