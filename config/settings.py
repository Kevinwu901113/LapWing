"""项目配置，统一管理环境变量和常量。"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
ROOT_DIR = Path(__file__).parent.parent
CONFIG_DIR = ROOT_DIR / "config"
PROMPTS_DIR = ROOT_DIR / "prompts"
LOGS_DIR = ROOT_DIR / "logs"
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "lapwing.db"

# 加载环境变量
load_dotenv(CONFIG_DIR / ".env")

# Telegram
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")

# LLM（OpenAI 兼容格式）
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "glm-4-flash")

# 对话设置
MAX_HISTORY_TURNS: int = 20  # 保留最近 N 轮对话（每轮 = 1 user + 1 assistant）
MAX_REPLY_LENGTH: int = 4096  # Telegram 消息字符限制

# 日志
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
