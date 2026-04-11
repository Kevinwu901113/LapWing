"""项目配置，统一管理环境变量和常量。"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False


# 项目根目录
ROOT_DIR = Path(__file__).parent.parent
CONFIG_DIR = ROOT_DIR / "config"
PROMPTS_DIR = ROOT_DIR / "prompts"
LOGS_DIR = ROOT_DIR / "logs"
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "lapwing.db"

# 文件记忆路径
IDENTITY_DIR = DATA_DIR / "identity"
MEMORY_DIR = DATA_DIR / "memory"
EVOLUTION_DIR = DATA_DIR / "evolution"
JOURNAL_DIR = MEMORY_DIR / "journal"
CONVERSATION_SUMMARIES_DIR = MEMORY_DIR / "conversations" / "summaries"
DIAGNOSTICS_DIR = DATA_DIR / "diagnostics"
DIAGNOSTICS_SAMPLES_DIR = DIAGNOSTICS_DIR / "samples"
CONSTITUTION_PATH = IDENTITY_DIR / "constitution.md"
SOUL_PATH = IDENTITY_DIR / "soul.md"
SELF_NOTES_PATH = MEMORY_DIR / "SELF.md"
KEVIN_NOTES_PATH = MEMORY_DIR / "KEVIN.md"
RULES_PATH = EVOLUTION_DIR / "rules.md"
INTERESTS_PATH = EVOLUTION_DIR / "interests.md"
CHANGELOG_PATH = EVOLUTION_DIR / "changelog.md"

# Compaction 配置
COMPACTION_TRIGGER_RATIO = float(os.getenv("COMPACTION_TRIGGER_RATIO", "0.8"))
COMPACTION_SUMMARY_MAX_TOKENS = 300

LAPWING_HOME = Path(os.getenv("LAPWING_HOME", str(Path.home() / ".lapwing")))
AUTH_DIR = LAPWING_HOME / "auth"
AUTH_PROFILES_PATH = AUTH_DIR / "auth-profiles.json"
API_BOOTSTRAP_TOKEN_PATH = AUTH_DIR / "api-bootstrap-token"

# 加载环境变量
load_dotenv(CONFIG_DIR / ".env")

# Telegram
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_PROXY_URL: str = os.getenv("TELEGRAM_PROXY_URL", "")
SEARCH_PROXY_URL: str = os.getenv("SEARCH_PROXY_URL", "") or TELEGRAM_PROXY_URL
TELEGRAM_TEXT_MODE: str = os.getenv("TELEGRAM_TEXT_MODE", "markdown").strip().lower()
TELEGRAM_MARKDOWN_TABLE_MODE: str = os.getenv("TELEGRAM_MARKDOWN_TABLE_MODE", "code").strip().lower()
TELEGRAM_HTML_CHUNK_LIMIT: int = int(os.getenv("TELEGRAM_HTML_CHUNK_LIMIT", "4000"))
TELEGRAM_PROGRESS_STYLE: str = os.getenv("TELEGRAM_PROGRESS_STYLE", "silent").strip().lower()
TELEGRAM_PROGRESS_DEDUP: bool = os.getenv("TELEGRAM_PROGRESS_DEDUP", "true").lower() == "true"
TELEGRAM_PROGRESS_THROTTLE_SECONDS: float = float(
    os.getenv("TELEGRAM_PROGRESS_THROTTLE_SECONDS", "1.0")
)
TELEGRAM_KEVIN_ID: str = os.getenv("TELEGRAM_KEVIN_ID", "")

# QQ (NapCat OneBot v11)
QQ_ENABLED: bool = os.getenv("QQ_ENABLED", "false").lower() == "true"
QQ_WS_URL: str = os.getenv("QQ_WS_URL", "ws://127.0.0.1:3001")
QQ_ACCESS_TOKEN: str = os.getenv("QQ_ACCESS_TOKEN", "")
QQ_SELF_ID: str = os.getenv("QQ_SELF_ID", "")
QQ_KEVIN_ID: str = os.getenv("QQ_KEVIN_ID", "")

# QQ 群聊
QQ_GROUP_IDS: list[str] = [
    g.strip() for g in os.getenv("QQ_GROUP_IDS", "").split(",") if g.strip()
]
QQ_GROUP_CONTEXT_SIZE: int = int(os.getenv("QQ_GROUP_CONTEXT_SIZE", "30"))
QQ_GROUP_COOLDOWN: int = int(os.getenv("QQ_GROUP_COOLDOWN", "60"))
QQ_GROUP_INTEREST_KEYWORDS: list[str] = [
    k.strip() for k in os.getenv("QQ_GROUP_INTEREST_KEYWORDS", "").split(",") if k.strip()
]

# LLM（OpenAI 兼容格式）
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "glm-4-flash")
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "").strip().lower()

# 多模型路由配置（可选，不配置时回退到通用 LLM_* 配置）
LLM_CHAT_API_KEY: str = os.getenv("LLM_CHAT_API_KEY", "")
LLM_CHAT_BASE_URL: str = os.getenv("LLM_CHAT_BASE_URL", "")
LLM_CHAT_MODEL: str = os.getenv("LLM_CHAT_MODEL", "")
LLM_CHAT_PROVIDER: str = os.getenv("LLM_CHAT_PROVIDER", "").strip().lower()

LLM_TOOL_API_KEY: str = os.getenv("LLM_TOOL_API_KEY", "")
LLM_TOOL_BASE_URL: str = os.getenv("LLM_TOOL_BASE_URL", "")
LLM_TOOL_MODEL: str = os.getenv("LLM_TOOL_MODEL", "")
LLM_TOOL_PROVIDER: str = os.getenv("LLM_TOOL_PROVIDER", "").strip().lower()

# NVIDIA NIM（心跳专用模型，可选）
NIM_API_KEY: str = os.getenv("NIM_API_KEY", "")
NIM_BASE_URL: str = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL: str = os.getenv("NIM_MODEL", "meta/llama-3.1-8b-instruct")
NIM_PROVIDER: str = os.getenv("NIM_PROVIDER", "").strip().lower()
LLM_HEARTBEAT_PROVIDER: str = (
    os.getenv("LLM_HEARTBEAT_PROVIDER", "").strip().lower()
    or NIM_PROVIDER
)
# OAuth 刷新提前量
AUTH_REFRESH_SKEW_SECONDS: int = int(os.getenv("AUTH_REFRESH_SKEW_SECONDS", "300"))

# OpenAI Codex OAuth PKCE
OPENAI_CODEX_AUTH_AUTHORIZE_URL: str = os.getenv("OPENAI_CODEX_AUTH_AUTHORIZE_URL", "https://auth.openai.com/oauth/authorize")
OPENAI_CODEX_AUTH_TOKEN_URL: str = os.getenv("OPENAI_CODEX_AUTH_TOKEN_URL", "https://auth.openai.com/oauth/token")
OPENAI_CODEX_AUTH_CLIENT_ID: str = os.getenv("OPENAI_CODEX_AUTH_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
OPENAI_CODEX_AUTH_REDIRECT_HOST: str = os.getenv("OPENAI_CODEX_AUTH_REDIRECT_HOST", "localhost")
OPENAI_CODEX_AUTH_REDIRECT_PORT: int = int(os.getenv("OPENAI_CODEX_AUTH_REDIRECT_PORT", "1455"))
OPENAI_CODEX_AUTH_REDIRECT_PATH: str = os.getenv("OPENAI_CODEX_AUTH_REDIRECT_PATH", "/auth/callback")
OPENAI_CODEX_AUTH_PROXY_URL: str = os.getenv("OPENAI_CODEX_AUTH_PROXY_URL", "")

# 心跳配置
HEARTBEAT_ENABLED: bool = os.getenv("HEARTBEAT_ENABLED", "true").lower() == "true"
HEARTBEAT_FAST_INTERVAL_MINUTES: int = int(os.getenv("HEARTBEAT_FAST_INTERVAL_MINUTES", "60"))
HEARTBEAT_SLOW_HOUR: int = int(os.getenv("HEARTBEAT_SLOW_HOUR", "3"))

# 意识循环配置
CONSCIOUSNESS_ENABLED: bool = os.getenv("CONSCIOUSNESS_ENABLED", "true").lower() == "true"
CONSCIOUSNESS_DEFAULT_INTERVAL: int = int(os.getenv("CONSCIOUSNESS_DEFAULT_INTERVAL", "600"))
CONSCIOUSNESS_MIN_INTERVAL: int = int(os.getenv("CONSCIOUSNESS_MIN_INTERVAL", "120"))
CONSCIOUSNESS_MAX_INTERVAL: int = int(os.getenv("CONSCIOUSNESS_MAX_INTERVAL", "1800"))
CONSCIOUSNESS_AFTER_CHAT_INTERVAL: int = int(os.getenv("CONSCIOUSNESS_AFTER_CHAT_INTERVAL", "120"))
CONSCIOUSNESS_CONVERSATION_END_DELAY: int = int(os.getenv("CONSCIOUSNESS_CONVERSATION_END_DELAY", "300"))

# 自主浏览配置
BROWSE_ENABLED: bool = os.getenv("BROWSE_ENABLED", "true").lower() == "true"
BROWSE_INTERVAL_HOURS: int = int(os.getenv("BROWSE_INTERVAL_HOURS", "2"))
_BROWSE_SOURCES_DEFAULT = "hackernews,reddit/technology,reddit/science"
BROWSE_SOURCES: list[str] = [
    item.strip()
    for item in os.getenv("BROWSE_SOURCES", _BROWSE_SOURCES_DEFAULT).split(",")
    if item.strip()
]

# 对话设置
MAX_HISTORY_TURNS: int = 20  # 保留最近 N 轮对话（每轮 = 1 user + 1 assistant）
MAX_REPLY_LENGTH: int = 4096  # Telegram 消息字符限制
MESSAGE_BUFFER_SECONDS: float = float(os.getenv("MESSAGE_BUFFER_SECONDS", "4"))  # 消息合并等待时间

# 用户画像提取
FACT_EXTRACT_IDLE_SECONDS: int = int(os.getenv("FACT_EXTRACT_IDLE_SECONDS", "300"))  # 空闲 N 秒后触发提取
FACT_EXTRACT_TURN_THRESHOLD: int = int(os.getenv("FACT_EXTRACT_TURN_THRESHOLD", "3"))  # 满 N 轮触发提取
INTEREST_EXTRACT_TURN_THRESHOLD: int = int(os.getenv("INTEREST_EXTRACT_TURN_THRESHOLD", "5"))

# ── Wave 1 功能开关 ──
MEMORY_CRUD_ENABLED: bool = os.getenv("MEMORY_CRUD_ENABLED", "true").lower() in ("true", "1", "yes")
AUTO_MEMORY_EXTRACT_ENABLED: bool = os.getenv("AUTO_MEMORY_EXTRACT_ENABLED", "true").lower() in ("true", "1", "yes")
MEMORY_GUARD_ENABLED: bool = os.getenv("MEMORY_GUARD_ENABLED", "true").lower() in ("true", "1", "yes")
DELEGATION_ENABLED: bool = os.getenv("DELEGATION_ENABLED", "false").lower() in ("true", "1", "yes")
DELEGATION_MAX_CONCURRENT: int = int(os.getenv("DELEGATION_MAX_CONCURRENT", "3"))
DELEGATION_MAX_ITERATIONS: int = int(os.getenv("DELEGATION_MAX_ITERATIONS", "20"))
SELF_SCHEDULE_ENABLED: bool = os.getenv("SELF_SCHEDULE_ENABLED", "true").lower() in ("true", "1", "yes")
QUALITY_CHECK_ENABLED: bool = os.getenv("LAPWING_FLAG_QUALITY_CHECK", "true").lower() in ("true", "1", "yes")
PROGRESS_REPORT_ENABLED: bool = os.getenv("PROGRESS_REPORT_ENABLED", "true").lower() in ("true", "1", "yes")
TASK_RESUMPTION_ENABLED: bool = os.getenv("TASK_RESUMPTION_ENABLED", "true").lower() in ("true", "1", "yes")
MESSAGE_SPLIT_ENABLED: bool = os.getenv("MESSAGE_SPLIT_ENABLED", "true").lower() in ("true", "1", "yes")
MESSAGE_SPLIT_FALLBACK_NEWLINE: bool = os.getenv("MESSAGE_SPLIT_FALLBACK_NEWLINE", "true").lower() in ("true", "1", "yes")
MESSAGE_SPLIT_DELAY_BASE: float = float(os.getenv("MESSAGE_SPLIT_DELAY_BASE", "0.8"))
MESSAGE_SPLIT_DELAY_PER_CHAR: float = float(os.getenv("MESSAGE_SPLIT_DELAY_PER_CHAR", "0.008"))
MESSAGE_SPLIT_DELAY_MAX: float = float(os.getenv("MESSAGE_SPLIT_DELAY_MAX", "2.5"))
MESSAGE_SPLIT_SINGLE_NL_MIN_LEN: int = int(os.getenv("MESSAGE_SPLIT_SINGLE_NL_MIN_LEN", "80"))

# ── Session 管理 ──
SESSION_ENABLED: bool = os.getenv("SESSION_ENABLED", "true").lower() in ("true", "1")
SESSION_TIMEOUT_MINUTES: int = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
SESSION_DORMANT_TTL_HOURS: float = float(os.getenv("SESSION_DORMANT_TTL_HOURS", "3"))
SESSION_MIN_MESSAGES_TO_KEEP: int = int(os.getenv("SESSION_MIN_MESSAGES_TO_KEEP", "4"))
SESSION_MAX_DORMANT_PER_CHAT: int = int(os.getenv("SESSION_MAX_DORMANT_PER_CHAT", "5"))
SESSION_TOPIC_DETECT_ENABLED: bool = False  # Phase 2: LLM 话题检测
SESSION_SNAPSHOTS_DIR: Path = MEMORY_DIR / "sessions"

# 语音转写（Whisper，可选；不填则回退到通用 LLM_* 配置）
WHISPER_API_KEY: str = os.getenv("WHISPER_API_KEY", "")
WHISPER_BASE_URL: str = os.getenv("WHISPER_BASE_URL", "")
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-1")

# Shell 执行
SHELL_ENABLED: bool = os.getenv("SHELL_ENABLED", "true").lower() == "true"
SHELL_ALLOW_SUDO: bool = os.getenv("SHELL_ALLOW_SUDO", "false").lower() == "true"
SHELL_TIMEOUT: int = int(os.getenv("SHELL_TIMEOUT", "30"))
SHELL_DEFAULT_CWD: str = os.getenv("SHELL_DEFAULT_CWD", str(ROOT_DIR))
SHELL_MAX_OUTPUT_CHARS: int = int(os.getenv("SHELL_MAX_OUTPUT_CHARS", "4000"))
SHELL_BACKEND: str = os.getenv("SHELL_BACKEND", "local")
TASK_MAX_TOOL_ROUNDS: int = int(os.getenv("TASK_MAX_TOOL_ROUNDS", "32"))

# Skills（AgentSkills / OpenClaw-compatible）
SKILLS_ENABLED: bool = os.getenv("SKILLS_ENABLED", "true").lower() == "true"
SKILLS_COMMANDS_ENABLED: bool = os.getenv("SKILLS_COMMANDS_ENABLED", "true").lower() == "true"
SKILLS_WORKSPACE_DIR: str = os.getenv("SKILLS_WORKSPACE_DIR", str(ROOT_DIR / "skills"))
SKILLS_MANAGED_DIR: str = os.getenv("SKILLS_MANAGED_DIR", str(Path.home() / ".lapwing" / "skills"))
SKILLS_BUNDLED_DIR: str = os.getenv("SKILLS_BUNDLED_DIR", str(ROOT_DIR / "bundled_skills"))
SKILLS_EXTRA_DIRS: list[str] = [
    item.strip()
    for item in os.getenv("SKILLS_EXTRA_DIRS", "").split(",")
    if item.strip()
]
SKILLS_DISPATCH_TOOL_WHITELIST: set[str] = {
    item.strip()
    for item in os.getenv("SKILLS_DISPATCH_TOOL_WHITELIST", "execute_shell").split(",")
    if item.strip()
}

# Experience Skills（Lapwing 自身经验积累系统）
EXPERIENCE_SKILLS_DIR: Path = ROOT_DIR / "skills"
SKILL_TRACES_DIR: Path = ROOT_DIR / "skill_traces"
EXPERIENCE_SKILLS_ENABLED: bool = os.getenv("EXPERIENCE_SKILLS_ENABLED", "true").lower() == "true"
EXPERIENCE_SKILLS_MAX_INJECT_TOKENS: int = int(os.getenv("EXPERIENCE_SKILLS_MAX_INJECT_TOKENS", "4000"))

# Latency / 体感 SLO（监控+告警）
TOOL_LOOP_SLO_SHELL_P95_MS: int = int(os.getenv("TOOL_LOOP_SLO_SHELL_P95_MS", "2000"))
TOOL_LOOP_SLO_WEB_P95_MS: int = int(os.getenv("TOOL_LOOP_SLO_WEB_P95_MS", "5000"))
TOOL_LOOP_LONG_COMMAND_CUTOFF_MS: int = int(os.getenv("TOOL_LOOP_LONG_COMMAND_CUTOFF_MS", "10000"))
TOOL_EVENT_START_TO_UI_P95_MS: int = int(os.getenv("TOOL_EVENT_START_TO_UI_P95_MS", "200"))
TOOL_EVENT_UPDATE_THROTTLE_MS: int = int(os.getenv("TOOL_EVENT_UPDATE_THROTTLE_MS", "500"))
TOOL_LATENCY_WINDOW_SIZE: int = int(os.getenv("TOOL_LATENCY_WINDOW_SIZE", "200"))
TOOL_LATENCY_MIN_SAMPLES_FOR_SLO: int = int(os.getenv("TOOL_LATENCY_MIN_SAMPLES_FOR_SLO", "20"))

# 工具循环检测
LOOP_DETECTION_ENABLED: bool = os.getenv("LOOP_DETECTION_ENABLED", "true").lower() == "true"
LOOP_DETECTION_HISTORY_SIZE: int = int(os.getenv("LOOP_DETECTION_HISTORY_SIZE", "30"))
LOOP_DETECTION_WARNING_THRESHOLD: int = int(os.getenv("LOOP_DETECTION_WARNING_THRESHOLD", "10"))
LOOP_DETECTION_CRITICAL_THRESHOLD: int = int(os.getenv("LOOP_DETECTION_CRITICAL_THRESHOLD", "20"))
LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD: int = int(
    os.getenv("LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD", "30")
)
LOOP_DETECTION_DETECTOR_GENERIC_REPEAT: bool = (
    os.getenv("LOOP_DETECTION_DETECTOR_GENERIC_REPEAT", "true").lower() == "true"
)
LOOP_DETECTION_DETECTOR_PING_PONG: bool = (
    os.getenv("LOOP_DETECTION_DETECTOR_PING_PONG", "true").lower() == "true"
)
LOOP_DETECTION_DETECTOR_KNOWN_POLL_NO_PROGRESS: bool = (
    os.getenv("LOOP_DETECTION_DETECTOR_KNOWN_POLL_NO_PROGRESS", "true").lower() == "true"
)

# ── 浏览器子系统 ──
BROWSER_ENABLED: bool = os.getenv("BROWSER_ENABLED", "false").lower() in ("true", "1", "yes")
BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "true").lower() in ("true", "1", "yes")
BROWSER_USER_DATA_DIR: str = os.getenv("BROWSER_USER_DATA_DIR", str(DATA_DIR / "browser" / "profile"))
BROWSER_MAX_TABS: int = int(os.getenv("BROWSER_MAX_TABS", "8"))
BROWSER_PAGE_TEXT_MAX_CHARS: int = int(os.getenv("BROWSER_PAGE_TEXT_MAX_CHARS", "4000"))
BROWSER_NAVIGATION_TIMEOUT_MS: int = int(os.getenv("BROWSER_NAVIGATION_TIMEOUT_MS", "30000"))
BROWSER_ACTION_TIMEOUT_MS: int = int(os.getenv("BROWSER_ACTION_TIMEOUT_MS", "10000"))
BROWSER_SCREENSHOT_DIR: str = os.getenv("BROWSER_SCREENSHOT_DIR", str(DATA_DIR / "browser" / "screenshots"))
BROWSER_SCREENSHOT_RETAIN_DAYS: int = int(os.getenv("BROWSER_SCREENSHOT_RETAIN_DAYS", "7"))
BROWSER_VIEWPORT_WIDTH: int = int(os.getenv("BROWSER_VIEWPORT_WIDTH", "1280"))
BROWSER_VIEWPORT_HEIGHT: int = int(os.getenv("BROWSER_VIEWPORT_HEIGHT", "720"))
BROWSER_LOCALE: str = os.getenv("BROWSER_LOCALE", "zh-CN")
BROWSER_TIMEZONE: str = os.getenv("BROWSER_TIMEZONE", "Asia/Taipei")
BROWSER_MAX_ELEMENT_COUNT: int = int(os.getenv("BROWSER_MAX_ELEMENT_COUNT", "50"))
BROWSER_WAIT_AFTER_ACTION_MS: int = int(os.getenv("BROWSER_WAIT_AFTER_ACTION_MS", "1000"))
BROWSER_URL_BLACKLIST: list[str] = [
    item.strip()
    for item in os.getenv("BROWSER_URL_BLACKLIST", "").split(",")
    if item.strip()
]
BROWSER_URL_WHITELIST: list[str] = [
    item.strip()
    for item in os.getenv("BROWSER_URL_WHITELIST", "").split(",")
    if item.strip()
]
BROWSER_BLOCK_INTERNAL_NETWORK: bool = os.getenv("BROWSER_BLOCK_INTERNAL_NETWORK", "true").lower() in ("true", "1", "yes")
BROWSER_SENSITIVE_ACTION_WORDS: list[str] = [
    item.strip()
    for item in os.getenv(
        "BROWSER_SENSITIVE_ACTION_WORDS",
        "delete,remove,pay,purchase,buy,submit order,删除,移除,支付,购买,确认订单,提交订单",
    ).split(",")
    if item.strip()
]
# 浏览器视觉理解
BROWSER_VISION_ENABLED: bool = os.getenv("BROWSER_VISION_ENABLED", "true").lower() in ("true", "1", "yes")
BROWSER_VISION_SLOT: str = os.getenv("BROWSER_VISION_SLOT", "browser_vision")
BROWSER_VISION_MAX_DESCRIPTION_CHARS: int = int(os.getenv("BROWSER_VISION_MAX_DESCRIPTION_CHARS", "500"))
BROWSER_VISION_CACHE_TTL_SECONDS: int = int(os.getenv("BROWSER_VISION_CACHE_TTL_SECONDS", "30"))
BROWSER_VISION_IMG_THRESHOLD: int = int(os.getenv("BROWSER_VISION_IMG_THRESHOLD", "5"))
BROWSER_VISION_ALT_RATIO_THRESHOLD: float = float(os.getenv("BROWSER_VISION_ALT_RATIO_THRESHOLD", "0.3"))
# 凭据保险柜
CREDENTIAL_VAULT_PATH: str = os.getenv("CREDENTIAL_VAULT_PATH", str(DATA_DIR / "credentials" / "vault.enc"))

if TASK_MAX_TOOL_ROUNDS <= 0:
    raise ValueError("TASK_MAX_TOOL_ROUNDS 必须是正整数。")
if TOOL_LOOP_SLO_SHELL_P95_MS <= 0:
    raise ValueError("TOOL_LOOP_SLO_SHELL_P95_MS 必须是正整数。")
if TOOL_LOOP_SLO_WEB_P95_MS <= 0:
    raise ValueError("TOOL_LOOP_SLO_WEB_P95_MS 必须是正整数。")
if TOOL_LOOP_LONG_COMMAND_CUTOFF_MS <= 0:
    raise ValueError("TOOL_LOOP_LONG_COMMAND_CUTOFF_MS 必须是正整数。")
if TOOL_EVENT_START_TO_UI_P95_MS <= 0:
    raise ValueError("TOOL_EVENT_START_TO_UI_P95_MS 必须是正整数。")
if TOOL_EVENT_UPDATE_THROTTLE_MS <= 0:
    raise ValueError("TOOL_EVENT_UPDATE_THROTTLE_MS 必须是正整数。")
if TOOL_LATENCY_WINDOW_SIZE <= 0:
    raise ValueError("TOOL_LATENCY_WINDOW_SIZE 必须是正整数。")
if TOOL_LATENCY_MIN_SAMPLES_FOR_SLO <= 0:
    raise ValueError("TOOL_LATENCY_MIN_SAMPLES_FOR_SLO 必须是正整数。")
if LOOP_DETECTION_HISTORY_SIZE <= 0:
    raise ValueError("LOOP_DETECTION_HISTORY_SIZE 必须是正整数。")
if LOOP_DETECTION_WARNING_THRESHOLD <= 0:
    raise ValueError("LOOP_DETECTION_WARNING_THRESHOLD 必须是正整数。")
if LOOP_DETECTION_CRITICAL_THRESHOLD <= 0:
    raise ValueError("LOOP_DETECTION_CRITICAL_THRESHOLD 必须是正整数。")
if LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD <= 0:
    raise ValueError("LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD 必须是正整数。")
if LOOP_DETECTION_WARNING_THRESHOLD >= LOOP_DETECTION_CRITICAL_THRESHOLD:
    raise ValueError(
        "LOOP_DETECTION_WARNING_THRESHOLD 必须小于 LOOP_DETECTION_CRITICAL_THRESHOLD。"
    )
if LOOP_DETECTION_CRITICAL_THRESHOLD >= LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD:
    raise ValueError(
        "LOOP_DETECTION_CRITICAL_THRESHOLD 必须小于 LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD。"
    )
if TELEGRAM_TEXT_MODE not in {"markdown", "html"}:
    raise ValueError("TELEGRAM_TEXT_MODE 仅支持 markdown 或 html。")
if TELEGRAM_MARKDOWN_TABLE_MODE not in {"code", "bullets", "off"}:
    raise ValueError("TELEGRAM_MARKDOWN_TABLE_MODE 仅支持 code、bullets、off。")
if TELEGRAM_HTML_CHUNK_LIMIT <= 0:
    raise ValueError("TELEGRAM_HTML_CHUNK_LIMIT 必须是正整数。")
if TELEGRAM_HTML_CHUNK_LIMIT > 4096:
    raise ValueError("TELEGRAM_HTML_CHUNK_LIMIT 不能超过 Telegram 限制 4096。")
if TELEGRAM_PROGRESS_STYLE not in {"report", "silent"}:
    raise ValueError("TELEGRAM_PROGRESS_STYLE 仅支持 report 或 silent。")
if TELEGRAM_PROGRESS_THROTTLE_SECONDS < 0:
    raise ValueError("TELEGRAM_PROGRESS_THROTTLE_SECONDS 不能小于 0。")
if QQ_GROUP_CONTEXT_SIZE <= 0:
    raise ValueError("QQ_GROUP_CONTEXT_SIZE 必须是正整数。")
if QQ_GROUP_COOLDOWN < 0:
    raise ValueError("QQ_GROUP_COOLDOWN 不能小于 0。")

# 自省与进化
SELF_REFLECTION_HOUR: int = int(os.getenv("SELF_REFLECTION_HOUR", "2"))  # 每日自省时间（小时）

# 搜索配置
SEARCH_MAX_RESULTS: int = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
CHAT_WEB_TOOLS_ENABLED: bool = os.getenv("CHAT_WEB_TOOLS_ENABLED", "true").lower() == "true"
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
SEARCH_PROVIDER: str = os.getenv("SEARCH_PROVIDER", "auto").strip().lower()  # "auto" | "tavily" | "ddg"
TAVILY_SEARCH_DEPTH: str = os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip().lower()  # "basic" | "advanced"
WEB_FETCH_MAX_CHARS: int = int(os.getenv("WEB_FETCH_MAX_CHARS", "8000"))
SEARCH_CACHE_TTL_SECONDS: int = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "300"))

# 本地 API Auth
API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
API_PORT: int = int(os.getenv("API_PORT", "8765"))
API_SESSION_COOKIE_NAME: str = os.getenv("API_SESSION_COOKIE_NAME", "lapwing_session")
API_SESSION_TTL_SECONDS: int = int(os.getenv("API_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
_API_ALLOWED_ORIGINS_DEFAULT = "http://localhost:1420,http://127.0.0.1:1420,http://127.0.0.1:8765"
API_ALLOWED_ORIGINS: list[str] = [
    item.strip()
    for item in os.getenv(
        "API_ALLOWED_ORIGINS",
        (
            _API_ALLOWED_ORIGINS_DEFAULT
            + ",tauri://localhost,http://tauri.localhost,https://tauri.localhost"
        ),
    ).split(",")
    if item.strip()
]

# 日志
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# 权限认证 (AuthorityGate)
# OWNER_IDS：合并环境变量 + 已有的 Telegram/QQ Kevin ID
OWNER_IDS: set[str] = {
    item.strip()
    for item in os.getenv("OWNER_IDS", "").split(",")
    if item.strip()
}
if TELEGRAM_KEVIN_ID:
    OWNER_IDS.add(TELEGRAM_KEVIN_ID)
if QQ_KEVIN_ID:
    OWNER_IDS.add(QQ_KEVIN_ID)

TRUSTED_IDS: set[str] = {
    item.strip()
    for item in os.getenv("TRUSTED_IDS", "").split(",")
    if item.strip()
}

# 桌面连接是否默认视为 OWNER（本地连接不做身份验证）
DESKTOP_DEFAULT_OWNER: bool = os.getenv("DESKTOP_DEFAULT_OWNER", "true").lower() == "true"
DESKTOP_WS_CHAT_ID_PREFIX: str = os.getenv("DESKTOP_WS_CHAT_ID_PREFIX", "desktop")
DESKTOP_AUTH_TOKENS_PATH: Path = AUTH_DIR / "desktop-tokens.json"
