"""
向后兼容层 — 所有值来自 src.config.get_settings()。

配置加载顺序：env vars (.env) > config.toml > 代码默认值。
TOML 为主配置源；env 为覆盖层（敏感凭据 + 环境特定值）。
现有代码继续 ``from config.settings import X``；新代码建议直接用
``from src.config import get_settings``。
"""

import os as _os
from pathlib import Path

from src.config import get_settings as _get_settings

_s = _get_settings()

# ── 路径常量（从代码计算，不走配置文件） ───────

ROOT_DIR = Path(__file__).parent.parent
CONFIG_DIR = ROOT_DIR / "config"
PROMPTS_DIR = ROOT_DIR / "prompts"
LOGS_DIR = ROOT_DIR / "logs"
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "lapwing.db"

IDENTITY_DIR = DATA_DIR / "identity"
MEMORY_DIR = DATA_DIR / "memory"
CONVERSATION_SUMMARIES_DIR = MEMORY_DIR / "conversations" / "summaries"
CONSTITUTION_PATH = IDENTITY_DIR / "constitution.md"
SOUL_PATH = IDENTITY_DIR / "soul.md"

LAPWING_HOME = Path(_os.getenv("LAPWING_HOME", str(Path.home() / ".lapwing")))
AUTH_DIR = LAPWING_HOME / "auth"
AUTH_PROFILES_PATH = AUTH_DIR / "auth-profiles.json"
API_BOOTSTRAP_TOKEN_PATH = AUTH_DIR / "api-bootstrap-token"

# ── Compaction ───────────────────────────────

COMPACTION_TRIGGER_RATIO: float = _s.compaction.trigger_ratio
COMPACTION_SUMMARY_MAX_TOKENS: int = _s.compaction.summary_max_tokens

# ── 网络代理 ─────────────────────────────────

SEARCH_PROXY_URL: str = _s.proxy.search_url
PROXY_SERVER: str = _s.proxy.server
PROXY_DEFAULT_STRATEGY: str = _s.proxy.default_strategy
PROXY_PERSIST_INTERVAL_SECONDS: int = _s.proxy.persist_interval_seconds

# ── QQ ───────────────────────────────────────

QQ_ENABLED: bool = _s.qq.enabled
QQ_WS_URL: str = _s.qq.ws_url
QQ_ACCESS_TOKEN: str = _s.qq.access_token
QQ_SELF_ID: str = _s.qq.self_id
QQ_KEVIN_ID: str = _s.qq.kevin_id
QQ_GROUP_IDS: list[str] = _s.qq.group_ids
QQ_GROUP_CONTEXT_SIZE: int = _s.qq.group_context_size
QQ_GROUP_COOLDOWN: int = _s.qq.group_cooldown
QQ_GROUP_INTEREST_KEYWORDS: list[str] = _s.qq.group_interest_keywords

# ── LLM ──────────────────────────────────────

LLM_API_KEY: str = _s.llm.api_key
LLM_BASE_URL: str = _s.llm.base_url
LLM_MODEL: str = _s.llm.model
LLM_PROVIDER: str = _s.llm.provider.strip().lower() if _s.llm.provider else ""

LLM_CHAT_API_KEY: str = _s.llm.chat.api_key
LLM_CHAT_BASE_URL: str = _s.llm.chat.base_url
LLM_CHAT_MODEL: str = _s.llm.chat.model
LLM_CHAT_PROVIDER: str = _s.llm.chat.provider.strip().lower() if _s.llm.chat.provider else ""

LLM_TOOL_API_KEY: str = _s.llm.tool.api_key
LLM_TOOL_BASE_URL: str = _s.llm.tool.base_url
LLM_TOOL_MODEL: str = _s.llm.tool.model
LLM_TOOL_PROVIDER: str = _s.llm.tool.provider.strip().lower() if _s.llm.tool.provider else ""

# ── NVIDIA NIM ───────────────────────────────

NIM_API_KEY: str = _s.nim.api_key
NIM_BASE_URL: str = _s.nim.base_url
NIM_MODEL: str = _s.nim.model
NIM_PROVIDER: str = _s.nim.provider.strip().lower() if _s.nim.provider else ""
LLM_HEARTBEAT_PROVIDER: str = _s.llm.heartbeat.provider.strip().lower() or NIM_PROVIDER

# ── OAuth ────────────────────────────────────

AUTH_REFRESH_SKEW_SECONDS: int = _s.auth.refresh_skew_seconds
OPENAI_CODEX_AUTH_AUTHORIZE_URL: str = _s.codex.auth_authorize_url
OPENAI_CODEX_AUTH_TOKEN_URL: str = _s.codex.auth_token_url
OPENAI_CODEX_AUTH_CLIENT_ID: str = _s.codex.auth_client_id
OPENAI_CODEX_AUTH_REDIRECT_HOST: str = _s.codex.auth_redirect_host
OPENAI_CODEX_AUTH_REDIRECT_PORT: int = _s.codex.auth_redirect_port
OPENAI_CODEX_AUTH_REDIRECT_PATH: str = _s.codex.auth_redirect_path
OPENAI_CODEX_AUTH_PROXY_URL: str = _s.codex.auth_proxy_url
CODEX_FALLBACK_MODEL: str = _s.codex.fallback_model

# ── 意识循环 ─────────────────────────────────

CONSCIOUSNESS_DEFAULT_INTERVAL: int = _s.consciousness.default_interval
CONSCIOUSNESS_MIN_INTERVAL: int = _s.consciousness.min_interval
CONSCIOUSNESS_MAX_INTERVAL: int = _s.consciousness.max_interval
CONSCIOUSNESS_AFTER_CHAT_INTERVAL: int = _s.consciousness.after_chat_interval
CONSCIOUSNESS_CONVERSATION_END_DELAY: int = _s.consciousness.conversation_end_delay
HEARTBEAT_TOOL_BUDGET: int = getattr(_s.consciousness, "tool_budget", 5)

# ── 对话设置 ─────────────────────────────────

MAX_HISTORY_TURNS: int = 20
MESSAGE_BUFFER_SECONDS: float = _s.message.buffer_seconds

# ── 记忆 ─────────────────────────────────────

MEMORY_WORKING_SET_TOP_K: int = _s.memory.working_set_top_k
EPISODIC_EXTRACT_ENABLED: bool = _s.memory.episodic_extract_enabled
EPISODIC_EXTRACT_MIN_TURNS: int = _s.memory.episodic_extract_min_turns
EPISODIC_EXTRACT_WINDOW_SIZE: int = _s.memory.episodic_extract_window_size
SEMANTIC_DISTILL_ENABLED: bool = _s.memory.semantic_distill_enabled
SEMANTIC_DISTILL_EPISODES_WINDOW: int = _s.memory.semantic_distill_episodes_window
SEMANTIC_DISTILL_DEDUP_THRESHOLD: float = _s.memory.semantic_distill_dedup_threshold
AGENT_TEAM_ENABLED: bool = _s.agent_team.enabled
SKILL_SYSTEM_ENABLED: bool = _s.skill.enabled
SKILL_SANDBOX_IMAGE: str = _s.skill.sandbox_image
SKILL_SANDBOX_TIMEOUT: int = _s.skill.sandbox_timeout

# ── Sandbox (unified) ───────────────────────
SANDBOX_DOCKER_IMAGE: str = _s.sandbox.docker_image
SANDBOX_NETWORK: str = _s.sandbox.network
SANDBOX_STRICT_MEMORY_MB: int = _s.sandbox.strict.memory_mb
SANDBOX_STRICT_CPUS: float = _s.sandbox.strict.cpus
SANDBOX_STRICT_TIMEOUT: int = _s.sandbox.strict.timeout
SANDBOX_STANDARD_MEMORY_MB: int = _s.sandbox.standard.memory_mb
SANDBOX_STANDARD_CPUS: float = _s.sandbox.standard.cpus
SANDBOX_STANDARD_TIMEOUT: int = _s.sandbox.standard.timeout
SANDBOX_PRIVILEGED_MEMORY_MB: int = _s.sandbox.privileged.memory_mb
SANDBOX_PRIVILEGED_CPUS: float = _s.sandbox.privileged.cpus
SANDBOX_PRIVILEGED_TIMEOUT: int = _s.sandbox.privileged.timeout

# ── Shell ────────────────────────────────────

SHELL_ENABLED: bool = _s.shell.enabled
SHELL_ALLOW_SUDO: bool = _s.shell.allow_sudo
SHELL_TIMEOUT: int = _s.shell.timeout
SHELL_DEFAULT_CWD: str = _s.shell.default_cwd or str(ROOT_DIR)
SHELL_MAX_OUTPUT_CHARS: int = _s.shell.max_output_chars
SHELL_BACKEND: str = _s.shell.backend
TASK_MAX_TOOL_ROUNDS: int = _s.task.max_tool_rounds
TASK_NO_ACTION_BUDGET: int = _s.task.no_action_budget
TASK_ERROR_BURST_THRESHOLD: int = _s.task.error_burst_threshold

# ── SLO ──────────────────────────────────────

TOOL_LOOP_SLO_SHELL_P95_MS: int = _s.slo.shell_p95_ms
TOOL_LOOP_SLO_WEB_P95_MS: int = _s.slo.web_p95_ms
TOOL_LOOP_LONG_COMMAND_CUTOFF_MS: int = _s.slo.long_command_cutoff_ms
TOOL_EVENT_START_TO_UI_P95_MS: int = _s.slo.event_start_to_ui_p95_ms
TOOL_EVENT_UPDATE_THROTTLE_MS: int = _s.slo.event_update_throttle_ms
TOOL_LATENCY_WINDOW_SIZE: int = _s.slo.latency_window_size
TOOL_LATENCY_MIN_SAMPLES_FOR_SLO: int = _s.slo.latency_min_samples

# ── Loop detection ───────────────────────────

LOOP_DETECTION_ENABLED: bool = _s.loop_detection.enabled
LOOP_DETECTION_HISTORY_SIZE: int = _s.loop_detection.history_size
LOOP_DETECTION_WARNING_THRESHOLD: int = _s.loop_detection.warning_threshold
LOOP_DETECTION_CRITICAL_THRESHOLD: int = _s.loop_detection.critical_threshold
LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD: int = _s.loop_detection.global_circuit_breaker_threshold
LOOP_DETECTION_DETECTOR_GENERIC_REPEAT: bool = _s.loop_detection.detector_generic_repeat
LOOP_DETECTION_DETECTOR_PING_PONG: bool = _s.loop_detection.detector_ping_pong
LOOP_DETECTION_DETECTOR_KNOWN_POLL_NO_PROGRESS: bool = _s.loop_detection.detector_known_poll_no_progress

# ── 浏览器 ───────────────────────────────────

BROWSER_ENABLED: bool = _s.browser.enabled
BROWSER_HEADLESS: bool = _s.browser.headless
BROWSER_PROXY_SERVER: str = _s.browser.proxy_server
BROWSER_USER_DATA_DIR: str = _s.browser.user_data_dir or str(DATA_DIR / "browser" / "profile")
BROWSER_MAX_TABS: int = _s.browser.max_tabs
BROWSER_PAGE_TEXT_MAX_CHARS: int = _s.browser.page_text_max_chars
BROWSER_NAVIGATION_TIMEOUT_MS: int = _s.browser.navigation_timeout_ms
BROWSER_ACTION_TIMEOUT_MS: int = _s.browser.action_timeout_ms
BROWSER_SCREENSHOT_DIR: str = _s.browser.screenshot_dir or str(DATA_DIR / "browser" / "screenshots")
BROWSER_SCREENSHOT_RETAIN_DAYS: int = _s.browser.screenshot_retain_days
BROWSER_VIEWPORT_WIDTH: int = _s.browser.viewport_width
BROWSER_VIEWPORT_HEIGHT: int = _s.browser.viewport_height
BROWSER_LOCALE: str = _s.browser.locale
BROWSER_TIMEZONE: str = _s.browser.timezone
BROWSER_MAX_ELEMENT_COUNT: int = _s.browser.max_element_count
BROWSER_WAIT_AFTER_ACTION_MS: int = _s.browser.wait_after_action_ms
BROWSER_URL_BLACKLIST: list[str] = _s.browser.url_blacklist
BROWSER_URL_WHITELIST: list[str] = _s.browser.url_whitelist
BROWSER_BLOCK_INTERNAL_NETWORK: bool = _s.browser.block_internal_network
BROWSER_SENSITIVE_ACTION_WORDS: list[str] = _s.browser.sensitive_action_words

BROWSER_VISION_ENABLED: bool = _s.browser.vision.enabled
BROWSER_VISION_SLOT: str = _s.browser.vision.slot
BROWSER_VISION_MAX_DESCRIPTION_CHARS: int = _s.browser.vision.max_description_chars
BROWSER_VISION_CACHE_TTL_SECONDS: int = _s.browser.vision.cache_ttl_seconds
BROWSER_VISION_IMG_THRESHOLD: int = _s.browser.vision.img_threshold
BROWSER_VISION_ALT_RATIO_THRESHOLD: float = _s.browser.vision.alt_ratio_threshold

MINIMAX_VLM_ENABLED: bool = _s.browser.minimax_vlm.enabled
MINIMAX_VLM_API_KEY: str = (
    _s.browser.minimax_vlm.api_key or LLM_CHAT_API_KEY or LLM_API_KEY
)
MINIMAX_VLM_HOST: str = _s.browser.minimax_vlm.host

# ── 凭据保险柜 ───────────────────────────────

CREDENTIAL_VAULT_PATH: str = _s.credential_vault_path or str(
    DATA_DIR / "credentials" / "vault.enc"
)

# ── 搜索 ─────────────────────────────────────

CHAT_WEB_TOOLS_ENABLED: bool = _s.search.chat_web_tools_enabled
TAVILY_API_KEY: str = _s.search.tavily_api_key
TAVILY_COUNTRY: str = _s.search.tavily_country
BOCHA_API_KEY: str = _s.search.bocha_api_key

# ── API ──────────────────────────────────────

API_HOST: str = _s.api.host
API_PORT: int = _s.api.port
API_SESSION_COOKIE_NAME: str = _s.api.session_cookie_name
API_SESSION_TTL_SECONDS: int = _s.api.session_ttl_seconds
API_ALLOWED_ORIGINS: list[str] = _s.api.allowed_origins

# ── 日志 ─────────────────────────────────────

LOG_LEVEL: str = _s.log.level

# ── 权限 ─────────────────────────────────────

OWNER_IDS: set[str] = set(_s.auth.owner_ids)
if QQ_KEVIN_ID:
    OWNER_IDS.add(QQ_KEVIN_ID)

TRUSTED_IDS: set[str] = set(_s.auth.trusted_ids)

# ── 其他 ─────────────────────────────────────

PHASE0_MODE: str = _s.phase0_mode.strip().upper()
DESKTOP_DEFAULT_OWNER: bool = _s.desktop.default_owner
DESKTOP_WS_CHAT_ID_PREFIX: str = _s.desktop.ws_chat_id_prefix
DESKTOP_AUTH_TOKENS_PATH: Path = AUTH_DIR / "desktop-tokens.json"

# ── 验证（保留原有约束） ─────────────────────

if LOOP_DETECTION_WARNING_THRESHOLD >= LOOP_DETECTION_CRITICAL_THRESHOLD:
    raise ValueError(
        "LOOP_DETECTION_WARNING_THRESHOLD 必须小于 LOOP_DETECTION_CRITICAL_THRESHOLD。"
    )
if LOOP_DETECTION_CRITICAL_THRESHOLD >= LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD:
    raise ValueError(
        "LOOP_DETECTION_CRITICAL_THRESHOLD 必须小于 LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD。"
    )
