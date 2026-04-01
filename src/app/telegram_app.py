"""Telegram 适配层：命令、消息处理与生命周期桥接。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

import config.settings as _cfg
from config.settings import (
    MESSAGE_BUFFER_SECONDS,
    SKILLS_COMMANDS_ENABLED,
)
from src.adapters.base import BaseAdapter, ChannelType
from src.app.telegram_delivery import send_telegram_reply_text, send_telegram_text_to_chat
from src.core.reasoning_tags import strip_internal_thinking_tags

logger = logging.getLogger("lapwing.app.telegram_app")


class TelegramApp:
    """将 Telegram 交互适配到 AppContainer。"""

    def __init__(self, container, tg_config: dict | None = None) -> None:
        self._container = container
        self._tg_config = tg_config or {}
        self._bot = None
        self._message_buffers: dict[str, list[str]] = {}
        self._buffer_tasks: dict[str, asyncio.Task] = {}
        self._buffer_updates: dict[str, object] = {}
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._active_status_tokens: dict[str, str] = {}
        self._status_last_text: dict[str, str] = {}
        self._status_last_sent_at: dict[str, float] = {}
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

        tg_adapter = TelegramChannelAdapter(
            telegram_app=self,
            config=self._tg_config,
        )
        self._container.channel_manager.register(ChannelType.TELEGRAM, tg_adapter)

        await self._container.start(send_fn=self._container.channel_manager.send_to_kevin)

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
        app.add_handler(CommandHandler("model", self.cmd_model))
        app.add_handler(CommandHandler("models", self.cmd_model))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.handle_voice))
        app.add_error_handler(self._error_handler)

    async def _error_handler(self, update, context) -> None:
        from telegram.error import TimedOut, NetworkError
        err = context.error
        if isinstance(err, (TimedOut, NetworkError)):
            logger.warning("Telegram 网络错误（已忽略）: %s", err)
        else:
            logger.error("Telegram 未处理错误: %s", err, exc_info=err)

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
        if self._container.brain.session_manager is not None:
            await update.message.reply_text("好的，已结束当前话题，下一条消息开始新对话。")
        else:
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
        args = context.args or []

        if not (hasattr(brain, "evolution_engine") and brain.evolution_engine is not None):
            await update.message.reply_text("进化功能尚未启用。")
            return

        if args and args[0] == "revert":
            result = await brain.evolution_engine.revert()
            if result["success"]:
                brain.reload_persona()
                await update.message.reply_text(f"已回滚到: {result['reverted_to']}")
            else:
                await update.message.reply_text(f"回滚失败: {result.get('error', '未知错误')}")
            return

        await update.message.reply_text("开始分析学习日志和行为规则，微调人格……")
        result = await brain.evolution_engine.evolve()
        if result["success"]:
            brain.reload_persona()
            await update.message.reply_text(
                f"进化完成。\n摘要: {result.get('summary', '（无）')}"
            )
        else:
            await update.message.reply_text(f"进化未完成: {result.get('error', '未知错误')}")

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

    async def cmd_model(self, update, context) -> None:
        message = update.message
        if message is None:
            return

        chat_id = str(message.chat_id)
        args = [str(item).strip() for item in (context.args or []) if str(item).strip()]
        keyword = args[0].lower() if args else ""

        try:
            if not args or keyword == "list":
                options = self._container.brain.list_model_options()
                status = self._container.brain.model_status(chat_id)
                await message.reply_text(self._format_model_list(options, status))
                return

            if keyword == "status":
                status = self._container.brain.model_status(chat_id)
                await message.reply_text(self._format_model_status(status))
                return

            if keyword == "default":
                result = self._container.brain.reset_model(chat_id)
                cleared = int(result.get("cleared", 0))
                await message.reply_text(f"已恢复默认模型（清除 {cleared} 个会话覆盖）。")
                return

            selector = " ".join(args).strip()
            result = self._container.brain.switch_model(chat_id, selector)
            await message.reply_text(self._format_model_switch_result(result))
        except ValueError as exc:
            await message.reply_text(f"模型切换失败：{exc}")
        except Exception as exc:
            logger.warning("[model] 处理 /model 失败: %s", exc)
            await message.reply_text("模型切换失败，请稍后再试。")

    def _format_model_list(self, options: list[dict[str, Any]], status: dict[str, Any]) -> str:
        if not options:
            return "当前没有可用模型，请先配置 LLM_MODEL_ALLOWLIST。"

        lines = ["可用模型（会话级）："]
        for option in options:
            alias = str(option.get("alias") or "").strip()
            ref = str(option.get("ref") or "").strip()
            index = option.get("index")
            if alias:
                lines.append(f"{index}. {alias} -> {ref}")
            else:
                lines.append(f"{index}. {ref}")

        purpose_status = dict(status.get("purposes", {}) or {})
        if purpose_status:
            lines.append("")
            lines.append("当前生效：")
            for purpose in ("chat", "tool", "heartbeat"):
                detail = purpose_status.get(purpose) or {}
                effective = str(detail.get("effective") or "").strip()
                if not effective:
                    continue
                if detail.get("override"):
                    lines.append(f"- {purpose}: {effective} (override)")
                else:
                    lines.append(f"- {purpose}: {effective}")
        return "\n".join(lines)

    def _format_model_status(self, status: dict[str, Any]) -> str:
        purpose_status = dict(status.get("purposes", {}) or {})
        lines = ["当前模型状态（会话级）："]
        for purpose in ("chat", "tool", "heartbeat"):
            detail = purpose_status.get(purpose) or {}
            default_model = str(detail.get("default") or "")
            effective_model = str(detail.get("effective") or "")
            override_model = detail.get("override")
            override_text = str(override_model) if override_model else "（无）"
            lines.append(f"- {purpose}:")
            lines.append(f"  default: {default_model}")
            lines.append(f"  effective: {effective_model}")
            lines.append(f"  override: {override_text}")
        return "\n".join(lines)

    def _format_model_switch_result(self, result: dict[str, Any]) -> str:
        selected = dict(result.get("selected", {}) or {})
        selected_ref = str(selected.get("ref") or "")
        applied = dict(result.get("applied", {}) or {})
        skipped = dict(result.get("skipped", {}) or {})

        lines = [f"已选择模型：{selected_ref}"]
        if applied:
            applied_parts = ", ".join(
                f"{purpose}={model_ref}"
                for purpose, model_ref in applied.items()
            )
            lines.append(f"已应用：{applied_parts}")
        else:
            lines.append("没有 purpose 应用该模型。")

        if skipped:
            lines.append("未切换：")
            for purpose, reason in skipped.items():
                lines.append(f"- {purpose}: {reason}")
        return "\n".join(lines)

    def _chat_lock(self, chat_id: str) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    @staticmethod
    def _chat_id_for_api(chat_id: str) -> int | str:
        try:
            return int(chat_id)
        except Exception:
            return chat_id

    def _format_progress_text(self, text: str) -> str:
        """格式化进度文本。stage:* 消息全部静默，由 LLM 自己的中间文字充当进度提示。"""
        if not text:
            return ""
        if _cfg.TELEGRAM_PROGRESS_STYLE != "report":
            return text
        if text.startswith("stage:"):
            return ""
        return text

    def _should_skip_status(self, chat_id: str, text: str, *, force: bool) -> bool:
        if force:
            return False

        last_text = self._status_last_text.get(chat_id, "")
        last_sent_at = self._status_last_sent_at.get(chat_id, 0.0)
        now = time.monotonic()

        if _cfg.TELEGRAM_PROGRESS_DEDUP and text == last_text:
            return True
        if (
            _cfg.TELEGRAM_PROGRESS_THROTTLE_SECONDS > 0
            and now - last_sent_at < _cfg.TELEGRAM_PROGRESS_THROTTLE_SECONDS
            and text.startswith("执行中：")
            and last_text.startswith("执行中：")
        ):
            return True
        return False

    async def _emit_status(self, chat_id: str, task_token: str, raw_text: str, *, force: bool = False) -> None:
        if self._bot is None:
            return
        if self._active_status_tokens.get(chat_id) != task_token:
            return
        try:
            await self._bot.send_chat_action(
                chat_id=self._chat_id_for_api(chat_id), action="typing"
            )
        except Exception:
            pass
        if _cfg.TELEGRAM_PROGRESS_STYLE != "report":
            return
        text = self._format_progress_text(raw_text)
        if not text:
            return
        if self._should_skip_status(chat_id, text, force=force):
            return
        self._status_last_text[chat_id] = text
        self._status_last_sent_at[chat_id] = time.monotonic()
        try:
            await self._bot.send_message(
                chat_id=self._chat_id_for_api(chat_id),
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass

    async def _send_reply(self, message, reply: str) -> None:
        reply = strip_internal_thinking_tags(reply)
        await send_telegram_reply_text(message=message, text=reply)

    def _build_status_sender(self, *, task_token: str):
        async def _send_status(cid: str, text: str) -> None:
            await self._emit_status(str(cid), task_token, text)

        return _send_status

    async def _think_and_reply(self, message, chat_id: str, user_text: str) -> None:
        async with self._chat_lock(chat_id):
            self._container.channel_manager.last_active_channel = ChannelType.TELEGRAM
            task_token = uuid.uuid4().hex
            self._active_status_tokens[chat_id] = task_token
            self._status_last_text.pop(chat_id, None)
            self._status_last_sent_at.pop(chat_id, None)
            try:
                try:
                    await message.chat.send_action("typing")
                except Exception:
                    pass

                async def send_fn(text: str) -> None:
                    await send_telegram_text_to_chat(
                        bot=self._bot,
                        chat_id=self._chat_id_for_api(chat_id),
                        text=text,
                    )

                async def typing_fn() -> None:
                    try:
                        await message.chat.send_action("typing")
                    except Exception:
                        pass

                reply = await self._container.brain.think_conversational(
                    chat_id,
                    user_text,
                    send_fn=send_fn,
                    typing_fn=typing_fn,
                    status_callback=self._build_status_sender(task_token=task_token),
                    adapter="telegram",
                    user_id=str(message.from_user.id) if message.from_user else "",
                )
                logger.info("已回复 [%s]，长度: %s", chat_id, len(reply))
            finally:
                if self._active_status_tokens.get(chat_id) == task_token:
                    self._active_status_tokens.pop(chat_id, None)

    async def _run_skill_command(
        self,
        *,
        message,
        chat_id: str,
        raw_user_message: str,
        skill_name: str,
        user_input: str,
    ) -> None:
        async with self._chat_lock(chat_id):
            task_token = uuid.uuid4().hex
            self._active_status_tokens[chat_id] = task_token
            self._status_last_text.pop(chat_id, None)
            self._status_last_sent_at.pop(chat_id, None)
            try:
                await message.chat.send_action("typing")
                reply = await self._container.brain.run_skill_command(
                    chat_id=chat_id,
                    raw_user_message=raw_user_message,
                    skill_name=skill_name,
                    user_input=user_input,
                    status_callback=self._build_status_sender(task_token=task_token),
                )
                await self._send_reply(message, reply)
            finally:
                if self._active_status_tokens.get(chat_id) == task_token:
                    self._active_status_tokens.pop(chat_id, None)

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
        try:
            await self._think_and_reply(message_obj, chat_id, combined)
        except Exception as exc:
            logger.warning("消息处理失败 [%s]: %s", chat_id, exc)

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


class TelegramChannelAdapter(BaseAdapter):
    """Thin BaseAdapter wrapper around TelegramApp for ChannelManager registration."""

    channel_type = ChannelType.TELEGRAM

    def __init__(self, telegram_app: TelegramApp, config: dict) -> None:
        super().__init__(config)
        self._telegram_app = telegram_app

    async def start(self) -> None:
        pass  # TelegramApp lifecycle managed by python-telegram-bot

    async def stop(self) -> None:
        pass  # TelegramApp lifecycle managed by python-telegram-bot

    async def send_text(self, chat_id: str, text: str) -> None:
        bot = self._telegram_app._bot
        if bot is None:
            return
        try:
            numeric_id = int(chat_id)
        except ValueError:
            numeric_id = chat_id
        await send_telegram_text_to_chat(bot=bot, chat_id=numeric_id, text=text)

    async def is_connected(self) -> bool:
        return self._telegram_app._bot is not None
