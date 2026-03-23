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

# 多模型路由配置（可选，不配置时回退到通用 LLM_* 配置）
LLM_CHAT_API_KEY: str = os.getenv("LLM_CHAT_API_KEY", "")
LLM_CHAT_BASE_URL: str = os.getenv("LLM_CHAT_BASE_URL", "")
LLM_CHAT_MODEL: str = os.getenv("LLM_CHAT_MODEL", "")

LLM_TOOL_API_KEY: str = os.getenv("LLM_TOOL_API_KEY", "")
LLM_TOOL_BASE_URL: str = os.getenv("LLM_TOOL_BASE_URL", "")
LLM_TOOL_MODEL: str = os.getenv("LLM_TOOL_MODEL", "")

# NVIDIA NIM（心跳专用模型，可选）
NIM_API_KEY: str = os.getenv("NIM_API_KEY", "")
NIM_BASE_URL: str = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL: str = os.getenv("NIM_MODEL", "meta/llama-3.1-8b-instruct")

# 心跳配置
HEARTBEAT_ENABLED: bool = os.getenv("HEARTBEAT_ENABLED", "true").lower() == "true"
HEARTBEAT_FAST_INTERVAL_MINUTES: int = int(os.getenv("HEARTBEAT_FAST_INTERVAL_MINUTES", "60"))
HEARTBEAT_SLOW_HOUR: int = int(os.getenv("HEARTBEAT_SLOW_HOUR", "3"))

# 对话设置
MAX_HISTORY_TURNS: int = 20  # 保留最近 N 轮对话（每轮 = 1 user + 1 assistant）
MAX_REPLY_LENGTH: int = 4096  # Telegram 消息字符限制

# 用户画像提取
FACT_EXTRACT_IDLE_SECONDS: int = int(os.getenv("FACT_EXTRACT_IDLE_SECONDS", "300"))  # 空闲 N 秒后触发提取
FACT_EXTRACT_TURN_THRESHOLD: int = int(os.getenv("FACT_EXTRACT_TURN_THRESHOLD", "3"))  # 满 N 轮触发提取

# 搜索配置
SEARCH_MAX_RESULTS: int = int(os.getenv("SEARCH_MAX_RESULTS", "5"))

# 日志
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
