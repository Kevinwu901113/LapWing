"""Lapwing - 入口文件。"""

import asyncio
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from config.settings import (
    TELEGRAM_TOKEN,
    TELEGRAM_PROXY_URL,
    MAX_REPLY_LENGTH,
    MESSAGE_BUFFER_SECONDS,
    LOG_LEVEL,
    LOGS_DIR,
    DB_PATH,
    DATA_DIR,
)
from src.core.brain import LapwingBrain
from src.core.heartbeat import HeartbeatEngine
from src.heartbeat.actions.proactive import ProactiveMessageAction, ReminderDispatchAction
from src.heartbeat.actions.consolidation import MemoryConsolidationAction
from src.heartbeat.actions.autonomous_browsing import AutonomousBrowsingAction
from src.heartbeat.actions.interest_proactive import InterestProactiveAction
from src.heartbeat.actions.self_reflection import SelfReflectionAction
from src.heartbeat.actions.prompt_evolution import PromptEvolutionAction

# ===== 日志配置 =====
LOGS_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "lapwing.log", encoding="utf-8", mode="a"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("lapwing")

# ===== 初始化大脑 =====
brain = LapwingBrain(db_path=DB_PATH)

# ===== 全局 bot 引用（post_init 后赋值，用于非 handler 上下文发消息）=====
_bot = None

# ===== 消息缓冲区（防连发）=====
_message_buffers: dict[str, list[str]] = {}   # chat_id -> 待合并的消息列表
_buffer_tasks: dict[str, asyncio.Task] = {}   # chat_id -> 待执行的 flush 任务
_buffer_updates: dict[str, object] = {}       # chat_id -> 最新的 message 对象（用于回复）


# ===== 生命周期回调 =====
async def post_init(application: Application) -> None:
    """应用启动后初始化数据库并启动心跳引擎。"""
    global _bot
    _bot = application.bot
    await brain.init_db()
    logger.info("数据库初始化完成")

    from src.agents.base import AgentRegistry
    from src.agents.browser import BrowserAgent
    from src.agents.coder import CoderAgent
    from src.agents.file_agent import FileAgent
    from src.agents.researcher import ResearcherAgent
    from src.agents.todo_agent import TodoAgent
    from src.agents.weather_agent import WeatherAgent
    from src.api.event_bus import DesktopEventBus
    from src.api.server import LocalApiServer
    from src.core.dispatcher import AgentDispatcher
    from src.core.knowledge_manager import KnowledgeManager
    from src.memory.interest_tracker import InterestTracker
    from src.memory.vector_store import VectorStore

    # 知识管理器需先初始化，agent 构造时注入
    brain.knowledge_manager = KnowledgeManager()
    brain.vector_store = VectorStore(DATA_DIR / "chroma")
    brain.event_bus = DesktopEventBus()

    agent_registry = AgentRegistry()
    agent_registry.register(ResearcherAgent(memory=brain.memory, knowledge_manager=brain.knowledge_manager))
    agent_registry.register(CoderAgent(memory=brain.memory))
    agent_registry.register(BrowserAgent(memory=brain.memory, knowledge_manager=brain.knowledge_manager))
    agent_registry.register(FileAgent(memory=brain.memory))
    agent_registry.register(WeatherAgent())
    agent_registry.register(TodoAgent(memory=brain.memory))
    brain.dispatcher = AgentDispatcher(
        registry=agent_registry,
        router=brain.router,
        memory=brain.memory,
    )
    logger.info("Agent dispatcher initialized with: researcher, coder, browser, file, weather, todo")

    brain.interest_tracker = InterestTracker(memory=brain.memory, router=brain.router)

    from src.core.self_reflection import SelfReflection
    brain.self_reflection = SelfReflection(memory=brain.memory, router=brain.router)

    from src.core.prompt_evolver import PromptEvolver
    brain.prompt_evolver = PromptEvolver(memory=brain.memory, router=brain.router)

    heartbeat = HeartbeatEngine(brain=brain, bot=application.bot)
    heartbeat.registry.register(ProactiveMessageAction())
    heartbeat.registry.register(ReminderDispatchAction())
    heartbeat.registry.register(AutonomousBrowsingAction())
    heartbeat.registry.register(InterestProactiveAction())
    heartbeat.registry.register(MemoryConsolidationAction())
    heartbeat.registry.register(SelfReflectionAction())
    heartbeat.registry.register(PromptEvolutionAction())
    heartbeat.start()
    application.bot_data["heartbeat"] = heartbeat
    api_server = LocalApiServer(brain=brain, event_bus=brain.event_bus)
    await api_server.start()
    application.bot_data["api_server"] = api_server
    logger.info("心跳引擎已初始化")


async def post_shutdown(application: Application) -> None:
    """应用关闭时清理资源。"""
    heartbeat = application.bot_data.get("heartbeat")
    if heartbeat:
        await heartbeat.shutdown()
    api_server = application.bot_data.get("api_server")
    if api_server:
        await api_server.shutdown()
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


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /new 命令 - 清除当前对话的短期记忆。"""
    chat_id = str(update.message.chat_id)
    await brain.clear_short_term_memory(chat_id)
    await update.message.reply_text("好的，已清空当前对话上下文，我们可以重新开始。")
    logger.info(f"通过 /new 命令清除了频道 {chat_id} 的短期记忆")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令 - 清除当前对话的短期记忆。"""
    chat_id = str(update.message.chat_id)
    await brain.clear_short_term_memory(chat_id)
    await update.message.reply_text("好的，已清空当前对话上下文，我们可以重新开始。")
    logger.info(f"通过 /clear 命令清除了频道 {chat_id} 的短期记忆")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /forget 命令 - 清除当前对话的全部记忆（长短期）。"""
    chat_id = str(update.message.chat_id)
    await brain.clear_all_memory(chat_id)
    await update.message.reply_text("好的，我已经清空这个 chat 的所有记忆（短期 + 长期）。")
    logger.info(f"通过 /forget 命令清除了频道 {chat_id} 的全部记忆（长短期）")


async def cmd_evolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /evolve 命令 - 手动触发 prompt 自我优化。"""
    if not hasattr(brain, "prompt_evolver") or brain.prompt_evolver is None:
        await update.message.reply_text("prompt 进化功能尚未启用。")
        return

    args = context.args
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


def _visible_user_facts(facts: list[dict]) -> list[dict]:
    """过滤掉内部 memory summary，仅保留可展示的用户画像。"""
    return [
        fact for fact in facts
        if not str(fact.get("fact_key", "")).startswith("memory_summary_")
    ]


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /memory 命令 - 查看或删除当前用户的画像信息。"""
    message = update.message
    if message is None:
        return

    chat_id = str(message.chat_id)
    facts = _visible_user_facts(await brain.memory.get_user_facts(chat_id))
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
    deleted = await brain.memory.delete_user_fact(chat_id, fact_key)
    if not deleted:
        await message.reply_text("没有这条记忆")
        return

    await message.reply_text("这条记忆已经删掉了。")


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
    async def _send_status(cid: str, text: str) -> None:
        if _bot:
            try:
                await _bot.send_message(chat_id=int(cid), text=text)
            except Exception:
                pass

    await message.chat.send_action("typing")
    reply = await brain.think(chat_id, user_text, status_callback=_send_status)
    await _send_reply(message, reply)
    logger.info(f"已回复 [{chat_id}]，长度: {len(reply)}")


async def _flush_buffer(chat_id: str) -> None:
    """等待缓冲时间到期后，合并所有消息并统一回复。"""
    await asyncio.sleep(MESSAGE_BUFFER_SECONDS)

    messages = _message_buffers.pop(chat_id, [])
    message_obj = _buffer_updates.pop(chat_id, None)
    _buffer_tasks.pop(chat_id, None)

    if not messages or not message_obj:
        return

    combined = "\n".join(messages)
    if len(messages) > 1:
        logger.info(f"合并 {len(messages)} 条消息 [{chat_id}]: {combined[:80]}...")
    await _think_and_reply(message_obj, chat_id, combined)


def _enqueue_message(chat_id: str, text: str, message_obj) -> None:
    """追加消息到缓冲区并重置定时器（文本和语音共用）。"""
    _message_buffers.setdefault(chat_id, []).append(text)
    _buffer_updates[chat_id] = message_obj

    old_task = _buffer_tasks.get(chat_id)
    if old_task and not old_task.done():
        old_task.cancel()

    _buffer_tasks[chat_id] = asyncio.create_task(_flush_buffer(chat_id))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通文本消息 — 带缓冲合并，防止连发刷屏。"""
    message = update.message
    if not message or not message.text:
        return

    chat_id = str(message.chat_id)
    user_name = message.from_user.first_name if message.from_user else "未知"
    logger.info(f"收到消息 [{chat_id}] {user_name}: {message.text}")

    _enqueue_message(chat_id, message.text, message)


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

    # 即时回显转写内容，处理走缓冲区（与文本消息合并）
    await message.reply_text(f"🎤 {user_text}")
    _enqueue_message(chat_id, user_text, message)


# ===== 启动 =====
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN 未配置！请检查 config/.env")
        exit(1)

    logger.info("Lapwing 正在启动...")

    builder = Application.builder().token(TELEGRAM_TOKEN)
    if TELEGRAM_PROXY_URL:
        from telegram.request import HTTPXRequest
        proxy_request = HTTPXRequest(proxy=TELEGRAM_PROXY_URL)
        builder = builder.request(proxy_request).get_updates_request(proxy_request)
        logger.info(f"使用代理: {TELEGRAM_PROXY_URL}")
    app = builder.post_init(post_init).post_shutdown(post_shutdown).build()

    # 注册处理器
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("evolve", cmd_evolve))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("interests", cmd_interests))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    # 启动轮询
    app.run_polling(drop_pending_updates=True)
    logger.info("Lapwing 已关闭")
