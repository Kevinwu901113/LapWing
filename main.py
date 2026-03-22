"""Lapwing - 入口文件。"""

import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from config.settings import (
    TELEGRAM_TOKEN,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    MAX_REPLY_LENGTH,
    LOG_LEVEL,
    LOGS_DIR,
    DB_PATH,
)
from src.core.brain import LapwingBrain

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
brain = LapwingBrain(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    model=LLM_MODEL,
    db_path=DB_PATH,
)


# ===== 生命周期回调 =====
async def post_init(application: Application) -> None:
    """应用启动后初始化数据库。"""
    await brain.init_db()
    logger.info("数据库初始化完成")


async def post_shutdown(application: Application) -> None:
    """应用关闭时清理资源。"""
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


# ===== 消息处理 =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通文本消息。"""
    message = update.message
    if not message or not message.text:
        return

    chat_id = str(message.chat_id)
    user_text = message.text
    user_name = message.from_user.first_name if message.from_user else "未知"

    logger.info(f"收到消息 [{chat_id}] {user_name}: {user_text}")

    # 显示正在输入
    await message.chat.send_action("typing")

    # 调用大脑
    reply = await brain.think(chat_id, user_text)

    # 发送回复（处理 Telegram 4096 字符限制）
    if len(reply) <= MAX_REPLY_LENGTH:
        await message.reply_text(reply)
    else:
        chunks = [reply[i:i + MAX_REPLY_LENGTH] for i in range(0, len(reply), MAX_REPLY_LENGTH)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply_text(chunk)
            else:
                await message.chat.send_message(chunk)

    logger.info(f"已回复 [{chat_id}]，长度: {len(reply)}")


# ===== 启动 =====
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN 未配置！请检查 config/.env")
        exit(1)
    if not LLM_API_KEY:
        logger.error("LLM_API_KEY 未配置！请检查 config/.env")
        exit(1)
    if not LLM_BASE_URL:
        logger.error("LLM_BASE_URL 未配置！请检查 config/.env")
        exit(1)

    logger.info(f"Lapwing 正在启动... 模型: {LLM_MODEL}")

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 启动轮询
    app.run_polling(drop_pending_updates=True)
    logger.info("Lapwing 已关闭")
