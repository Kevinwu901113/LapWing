"""Lapwing - 入口文件。"""

import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from config.settings import (
    TELEGRAM_TOKEN,
    MAX_REPLY_LENGTH,
    LOG_LEVEL,
    LOGS_DIR,
    DB_PATH,
)
from src.core.brain import LapwingBrain
from src.core.heartbeat import HeartbeatEngine
from src.heartbeat.actions.proactive import ProactiveMessageAction
from src.heartbeat.actions.consolidation import MemoryConsolidationAction
from src.heartbeat.actions.interest_proactive import InterestProactiveAction

# ===== 日志配置 =====
LOGS_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "lapwing.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("lapwing")

# ===== 初始化大脑 =====
brain = LapwingBrain(db_path=DB_PATH)


# ===== 生命周期回调 =====
async def post_init(application: Application) -> None:
    """应用启动后初始化数据库并启动心跳引擎。"""
    await brain.init_db()
    logger.info("数据库初始化完成")

    from src.agents.base import AgentRegistry
    from src.agents.browser import BrowserAgent
    from src.agents.coder import CoderAgent
    from src.agents.researcher import ResearcherAgent
    from src.core.dispatcher import AgentDispatcher
    from src.memory.interest_tracker import InterestTracker

    agent_registry = AgentRegistry()
    agent_registry.register(ResearcherAgent(memory=brain.memory))
    agent_registry.register(CoderAgent(memory=brain.memory))
    agent_registry.register(BrowserAgent(memory=brain.memory))
    brain.dispatcher = AgentDispatcher(
        registry=agent_registry,
        router=brain.router,
        memory=brain.memory,
    )
    logger.info("Agent dispatcher initialized with: researcher, coder, browser")

    brain.interest_tracker = InterestTracker(memory=brain.memory, router=brain.router)

    heartbeat = HeartbeatEngine(brain=brain, bot=application.bot)
    heartbeat.registry.register(ProactiveMessageAction())
    heartbeat.registry.register(InterestProactiveAction())
    heartbeat.registry.register(MemoryConsolidationAction())
    heartbeat.start()
    application.bot_data["heartbeat"] = heartbeat
    logger.info("心跳引擎已初始化")


async def post_shutdown(application: Application) -> None:
    """应用关闭时清理资源。"""
    heartbeat = application.bot_data.get("heartbeat")
    if heartbeat:
        await heartbeat.shutdown()
    if brain.interest_tracker:
        await brain.interest_tracker.shutdown()
    await brain.fact_extractor.shutdown()
    await brain.memory.close()
    logger.info("资源清理完成")


# ===== 命令处理 =====
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令。"""
    await update.message.reply_text("你好，我是 Lapwing。有什么想聊的吗？")


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /reload 命令 - 重新加载人格 prompt。"""
    brain.reload_persona()
    await update.message.reply_text("人格已重新加载。")
    logger.info("通过 /reload 命令重新加载了人格 prompt")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /forget 命令 - 清除当前对话的记忆。"""
    chat_id = str(update.message.chat_id)
    await brain.memory.clear(chat_id)
    await update.message.reply_text("好的，我已经忘记了我们之前的对话。重新开始吧。")
    logger.info(f"通过 /forget 命令清除了频道 {chat_id} 的记忆")


async def cmd_interests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /interests 命令 - 查看当前记录的兴趣图谱。"""
    chat_id = str(update.message.chat_id)
    interests = await brain.memory.get_top_interests(chat_id, limit=10)
    if not interests:
        await update.message.reply_text("我还没有记录到明显的兴趣话题。")
        return

    lines = ["你目前记录的兴趣话题："]
    for index, interest in enumerate(interests, start=1):
        lines.append(
            f"{index}. {interest['topic']}（权重 {interest['weight']:.1f}）"
        )
    await update.message.reply_text("\n".join(lines))


# ===== 消息处理 =====

async def _send_reply(message, reply: str) -> None:
    """发送回复，处理 Telegram 4096 字符限制。"""
    if len(reply) <= MAX_REPLY_LENGTH:
        await message.reply_text(reply)
    else:
        chunks = [reply[i:i + MAX_REPLY_LENGTH] for i in range(0, len(reply), MAX_REPLY_LENGTH)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply_text(chunk)
            else:
                await message.chat.send_message(chunk)


async def _think_and_reply(message, chat_id: str, user_text: str) -> None:
    """调用大脑并回复，文本和语音消息共用。"""
    await message.chat.send_action("typing")
    reply = await brain.think(chat_id, user_text)
    await _send_reply(message, reply)
    logger.info(f"已回复 [{chat_id}]，长度: {len(reply)}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通文本消息。"""
    message = update.message
    if not message or not message.text:
        return

    chat_id = str(message.chat_id)
    user_name = message.from_user.first_name if message.from_user else "未知"
    logger.info(f"收到消息 [{chat_id}] {user_name}: {message.text}")

    await _think_and_reply(message, chat_id, message.text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理语音消息：下载 → Whisper 转写 → 当作文本处理。"""
    from src.tools import transcriber

    message = update.message
    if not message:
        return

    chat_id = str(message.chat_id)
    user_name = message.from_user.first_name if message.from_user else "未知"

    # 取语音或音频附件
    voice = message.voice or message.audio
    if not voice:
        return

    logger.info(f"收到语音 [{chat_id}] {user_name}，时长: {getattr(voice, 'duration', '?')}s")
    await message.chat.send_action("typing")

    # 下载音频字节
    try:
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()
    except Exception as e:
        logger.warning(f"[voice] 下载音频失败: {e}")
        await message.reply_text("语音下载失败，请重试。")
        return

    # Whisper 转写
    filename = f"voice.{getattr(voice, 'mime_type', 'audio/ogg').split('/')[-1]}"
    user_text = await transcriber.transcribe(bytes(audio_bytes), filename=filename)

    if not user_text:
        await message.reply_text("听不太清楚，能再说一遍吗？")
        return

    logger.info(f"[voice] 转写结果 [{chat_id}]: {user_text}")

    # 回复时显示转写内容，再给出正式回复
    await message.reply_text(f"🎤 {user_text}")
    await _think_and_reply(message, chat_id, user_text)


# ===== 启动 =====
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN 未配置！请检查 config/.env")
        exit(1)

    logger.info("Lapwing 正在启动...")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # 注册处理器
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("interests", cmd_interests))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    # 启动轮询
    app.run_polling(drop_pending_updates=True)
    logger.info("Lapwing 已关闭")
