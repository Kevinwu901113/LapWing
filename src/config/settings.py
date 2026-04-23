"""
Lapwing 统一配置 — Pydantic BaseSettings + config.toml + .env。

加载优先级（高 → 低）：
1. 环境变量（含 config/.env）— 覆盖层，用于 Docker/CI 或敏感凭据
2. config.toml — 主配置源，所有非敏感配置在此集中管理
3. 代码中的默认值

config.toml 为主配置文件，结构化按模块分组。
.env 仅保留敏感凭据（API keys/tokens）和环境特定覆盖（proxy URLs）。

用法：
    from src.config import get_settings
    settings = get_settings()
    print(settings.qq.ws_url)
    print(settings.browser.vision.enabled)
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Annotated

from pydantic import BaseModel, Field, BeforeValidator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv
    _env_file = os.getenv("ENV_FILE", str(_PROJECT_ROOT / "config" / ".env"))
    load_dotenv(_env_file)
except ImportError:
    pass


# ── helpers ──────────────────────────────────

def _csv_to_list(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return []
        if v.startswith("["):
            import json
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
        return [x.strip() for x in v.split(",") if x.strip()]
    return v


CsvList = Annotated[list[str], BeforeValidator(_csv_to_list)]


def _load_toml() -> dict[str, Any]:
    config_path = Path(os.getenv("CONFIG_PATH", str(_PROJECT_ROOT / "config.toml")))
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


class _TomlSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self._data = _load_toml()

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


# 环境变量名 → settings 嵌套路径的完整映射。
# 不使用 pydantic 的 env_nested_delimiter（因为系统 SHELL=/bin/bash 等会冲突），
# 改为在 model_validator 中显式注入。env 覆盖 TOML（覆盖层语义）。
_ENV_MAP: dict[str, list[str]] = {
    # ── proxy ──
    "SEARCH_PROXY_URL": ["proxy", "search_url"],
    "PROXY_SERVER": ["proxy", "server"],
    "PROXY_DEFAULT_STRATEGY": ["proxy", "default_strategy"],
    "PROXY_PERSIST_INTERVAL_SECONDS": ["proxy", "persist_interval_seconds"],
    # ── qq ──
    "QQ_ENABLED": ["qq", "enabled"],
    "QQ_WS_URL": ["qq", "ws_url"],
    "QQ_ACCESS_TOKEN": ["qq", "access_token"],
    "QQ_SELF_ID": ["qq", "self_id"],
    "QQ_KEVIN_ID": ["qq", "kevin_id"],
    "QQ_GROUP_IDS": ["qq", "group_ids"],
    "QQ_GROUP_CONTEXT_SIZE": ["qq", "group_context_size"],
    "QQ_GROUP_COOLDOWN": ["qq", "group_cooldown"],
    "QQ_GROUP_INTEREST_KEYWORDS": ["qq", "group_interest_keywords"],
    # ── llm ──
    "LLM_API_KEY": ["llm", "api_key"],
    "LLM_BASE_URL": ["llm", "base_url"],
    "LLM_MODEL": ["llm", "model"],
    "LLM_PROVIDER": ["llm", "provider"],
    "LLM_CHAT_API_KEY": ["llm", "chat", "api_key"],
    "LLM_CHAT_BASE_URL": ["llm", "chat", "base_url"],
    "LLM_CHAT_MODEL": ["llm", "chat", "model"],
    "LLM_CHAT_PROVIDER": ["llm", "chat", "provider"],
    "LLM_TOOL_API_KEY": ["llm", "tool", "api_key"],
    "LLM_TOOL_BASE_URL": ["llm", "tool", "base_url"],
    "LLM_TOOL_MODEL": ["llm", "tool", "model"],
    "LLM_TOOL_PROVIDER": ["llm", "tool", "provider"],
    "LLM_HEARTBEAT_PROVIDER": ["llm", "heartbeat", "provider"],
    # ── nim ──
    "NIM_API_KEY": ["nim", "api_key"],
    "NIM_BASE_URL": ["nim", "base_url"],
    "NIM_MODEL": ["nim", "model"],
    "NIM_PROVIDER": ["nim", "provider"],
    # ── consciousness ──
    "CONSCIOUSNESS_DEFAULT_INTERVAL": ["consciousness", "default_interval"],
    "CONSCIOUSNESS_MIN_INTERVAL": ["consciousness", "min_interval"],
    "CONSCIOUSNESS_MAX_INTERVAL": ["consciousness", "max_interval"],
    "CONSCIOUSNESS_AFTER_CHAT_INTERVAL": ["consciousness", "after_chat_interval"],
    "CONSCIOUSNESS_CONVERSATION_END_DELAY": ["consciousness", "conversation_end_delay"],
    # ── message ──
    "MESSAGE_BUFFER_SECONDS": ["message", "buffer_seconds"],
    # ── memory ──
    "MEMORY_WORKING_SET_TOP_K": ["memory", "working_set_top_k"],
    "EPISODIC_EXTRACT_ENABLED": ["memory", "episodic_extract_enabled"],
    "EPISODIC_EXTRACT_MIN_TURNS": ["memory", "episodic_extract_min_turns"],
    "EPISODIC_EXTRACT_WINDOW_SIZE": ["memory", "episodic_extract_window_size"],
    "SEMANTIC_DISTILL_ENABLED": ["memory", "semantic_distill_enabled"],
    "SEMANTIC_DISTILL_EPISODES_WINDOW": ["memory", "semantic_distill_episodes_window"],
    "SEMANTIC_DISTILL_DEDUP_THRESHOLD": ["memory", "semantic_distill_dedup_threshold"],
    # ── shell ──
    "SHELL_ENABLED": ["shell", "enabled"],
    "SHELL_ALLOW_SUDO": ["shell", "allow_sudo"],
    "SHELL_TIMEOUT": ["shell", "timeout"],
    "SHELL_DEFAULT_CWD": ["shell", "default_cwd"],
    "SHELL_MAX_OUTPUT_CHARS": ["shell", "max_output_chars"],
    "SHELL_BACKEND": ["shell", "backend"],
    # ── task ──
    "TASK_MAX_TOOL_ROUNDS": ["task", "max_tool_rounds"],
    "TASK_NO_ACTION_BUDGET": ["task", "no_action_budget"],
    "TASK_ERROR_BURST_THRESHOLD": ["task", "error_burst_threshold"],
    # ── slo ──
    "TOOL_LOOP_SLO_SHELL_P95_MS": ["slo", "shell_p95_ms"],
    "TOOL_LOOP_SLO_WEB_P95_MS": ["slo", "web_p95_ms"],
    "TOOL_LOOP_LONG_COMMAND_CUTOFF_MS": ["slo", "long_command_cutoff_ms"],
    "TOOL_EVENT_START_TO_UI_P95_MS": ["slo", "event_start_to_ui_p95_ms"],
    "TOOL_EVENT_UPDATE_THROTTLE_MS": ["slo", "event_update_throttle_ms"],
    "TOOL_LATENCY_WINDOW_SIZE": ["slo", "latency_window_size"],
    "TOOL_LATENCY_MIN_SAMPLES_FOR_SLO": ["slo", "latency_min_samples"],
    # ── loop_detection ──
    "LOOP_DETECTION_ENABLED": ["loop_detection", "enabled"],
    "LOOP_DETECTION_HISTORY_SIZE": ["loop_detection", "history_size"],
    "LOOP_DETECTION_WARNING_THRESHOLD": ["loop_detection", "warning_threshold"],
    "LOOP_DETECTION_CRITICAL_THRESHOLD": ["loop_detection", "critical_threshold"],
    "LOOP_DETECTION_GLOBAL_CIRCUIT_BREAKER_THRESHOLD": ["loop_detection", "global_circuit_breaker_threshold"],
    "LOOP_DETECTION_DETECTOR_GENERIC_REPEAT": ["loop_detection", "detector_generic_repeat"],
    "LOOP_DETECTION_DETECTOR_PING_PONG": ["loop_detection", "detector_ping_pong"],
    "LOOP_DETECTION_DETECTOR_KNOWN_POLL_NO_PROGRESS": ["loop_detection", "detector_known_poll_no_progress"],
    # ── search ──
    "CHAT_WEB_TOOLS_ENABLED": ["search", "chat_web_tools_enabled"],
    "TAVILY_API_KEY": ["search", "tavily_api_key"],
    "TAVILY_COUNTRY": ["search", "tavily_country"],
    "BOCHA_API_KEY": ["search", "bocha_api_key"],
    # ── api ──
    "API_HOST": ["api", "host"],
    "API_PORT": ["api", "port"],
    "API_SESSION_COOKIE_NAME": ["api", "session_cookie_name"],
    "API_SESSION_TTL_SECONDS": ["api", "session_ttl_seconds"],
    "API_ALLOWED_ORIGINS": ["api", "allowed_origins"],
    # ── browser ──
    "BROWSER_ENABLED": ["browser", "enabled"],
    "BROWSER_HEADLESS": ["browser", "headless"],
    "BROWSER_PROXY_SERVER": ["browser", "proxy_server"],
    "BROWSER_USER_DATA_DIR": ["browser", "user_data_dir"],
    "BROWSER_MAX_TABS": ["browser", "max_tabs"],
    "BROWSER_PAGE_TEXT_MAX_CHARS": ["browser", "page_text_max_chars"],
    "BROWSER_NAVIGATION_TIMEOUT_MS": ["browser", "navigation_timeout_ms"],
    "BROWSER_ACTION_TIMEOUT_MS": ["browser", "action_timeout_ms"],
    "BROWSER_SCREENSHOT_DIR": ["browser", "screenshot_dir"],
    "BROWSER_SCREENSHOT_RETAIN_DAYS": ["browser", "screenshot_retain_days"],
    "BROWSER_VIEWPORT_WIDTH": ["browser", "viewport_width"],
    "BROWSER_VIEWPORT_HEIGHT": ["browser", "viewport_height"],
    "BROWSER_LOCALE": ["browser", "locale"],
    "BROWSER_TIMEZONE": ["browser", "timezone"],
    "BROWSER_MAX_ELEMENT_COUNT": ["browser", "max_element_count"],
    "BROWSER_WAIT_AFTER_ACTION_MS": ["browser", "wait_after_action_ms"],
    "BROWSER_URL_BLACKLIST": ["browser", "url_blacklist"],
    "BROWSER_URL_WHITELIST": ["browser", "url_whitelist"],
    "BROWSER_BLOCK_INTERNAL_NETWORK": ["browser", "block_internal_network"],
    "BROWSER_SENSITIVE_ACTION_WORDS": ["browser", "sensitive_action_words"],
    "BROWSER_VISION_ENABLED": ["browser", "vision", "enabled"],
    "BROWSER_VISION_SLOT": ["browser", "vision", "slot"],
    "BROWSER_VISION_MAX_DESCRIPTION_CHARS": ["browser", "vision", "max_description_chars"],
    "BROWSER_VISION_CACHE_TTL_SECONDS": ["browser", "vision", "cache_ttl_seconds"],
    "BROWSER_VISION_IMG_THRESHOLD": ["browser", "vision", "img_threshold"],
    "BROWSER_VISION_ALT_RATIO_THRESHOLD": ["browser", "vision", "alt_ratio_threshold"],
    "MINIMAX_VLM_ENABLED": ["browser", "minimax_vlm", "enabled"],
    "MINIMAX_VLM_API_KEY": ["browser", "minimax_vlm", "api_key"],
    "MINIMAX_VLM_HOST": ["browser", "minimax_vlm", "host"],
    # ── auth ──
    "OWNER_IDS": ["auth", "owner_ids"],
    "TRUSTED_IDS": ["auth", "trusted_ids"],
    "AUTH_REFRESH_SKEW_SECONDS": ["auth", "refresh_skew_seconds"],
    # ── desktop ──
    "DESKTOP_DEFAULT_OWNER": ["desktop", "default_owner"],
    "DESKTOP_WS_CHAT_ID_PREFIX": ["desktop", "ws_chat_id_prefix"],
    # ── skill ──
    "SKILL_SYSTEM_ENABLED": ["skill", "enabled"],
    "SKILL_SANDBOX_IMAGE": ["skill", "sandbox_image"],
    "SKILL_SANDBOX_TIMEOUT": ["skill", "sandbox_timeout"],
    # ── sandbox ──
    "SANDBOX_DOCKER_IMAGE": ["sandbox", "docker_image"],
    "SANDBOX_NETWORK": ["sandbox", "network"],
    "SANDBOX_STRICT_MEMORY_MB": ["sandbox", "strict", "memory_mb"],
    "SANDBOX_STRICT_CPUS": ["sandbox", "strict", "cpus"],
    "SANDBOX_STRICT_TIMEOUT": ["sandbox", "strict", "timeout"],
    "SANDBOX_STANDARD_MEMORY_MB": ["sandbox", "standard", "memory_mb"],
    "SANDBOX_STANDARD_CPUS": ["sandbox", "standard", "cpus"],
    "SANDBOX_STANDARD_TIMEOUT": ["sandbox", "standard", "timeout"],
    "SANDBOX_PRIVILEGED_MEMORY_MB": ["sandbox", "privileged", "memory_mb"],
    "SANDBOX_PRIVILEGED_CPUS": ["sandbox", "privileged", "cpus"],
    "SANDBOX_PRIVILEGED_TIMEOUT": ["sandbox", "privileged", "timeout"],
    # ── agent_team ──
    "AGENT_TEAM_ENABLED": ["agent_team", "enabled"],
    # ── codex ──
    "OPENAI_CODEX_AUTH_AUTHORIZE_URL": ["codex", "auth_authorize_url"],
    "OPENAI_CODEX_AUTH_TOKEN_URL": ["codex", "auth_token_url"],
    "OPENAI_CODEX_AUTH_CLIENT_ID": ["codex", "auth_client_id"],
    "OPENAI_CODEX_AUTH_REDIRECT_HOST": ["codex", "auth_redirect_host"],
    "OPENAI_CODEX_AUTH_REDIRECT_PORT": ["codex", "auth_redirect_port"],
    "OPENAI_CODEX_AUTH_REDIRECT_PATH": ["codex", "auth_redirect_path"],
    "OPENAI_CODEX_AUTH_PROXY_URL": ["codex", "auth_proxy_url"],
    "CODEX_FALLBACK_MODEL": ["codex", "fallback_model"],
    # ── compaction ──
    "COMPACTION_TRIGGER_RATIO": ["compaction", "trigger_ratio"],
    # ── log ──
    "LOG_LEVEL": ["log", "level"],
    # ── top-level ──
    "CREDENTIAL_VAULT_PATH": ["credential_vault_path"],
    "PHASE0_MODE": ["phase0_mode"],
}


# ── sub-config models ────────────────────────

class ProxyConfig(BaseModel):
    search_url: str = ""
    server: str = ""                    # 代理服务器地址，空则禁用 ProxyRouter
    default_strategy: str = "proxy"     # 默认策略：proxy | direct
    persist_interval_seconds: int = 300 # 规则持久化间隔（秒）


class QQConfig(BaseModel):
    enabled: bool = True
    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    self_id: str = ""
    kevin_id: str = ""
    group_ids: CsvList = Field(default_factory=list)
    group_context_size: int = Field(default=30, ge=1)
    group_cooldown: int = Field(default=60, ge=0)
    group_interest_keywords: CsvList = Field(default_factory=list)


class LLMSlotConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    provider: str = ""


class LLMHeartbeatConfig(BaseModel):
    provider: str = "nvidia"


class LLMConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = "MiniMax-M2.7"
    provider: str = "minimax"
    chat: LLMSlotConfig = Field(default_factory=LLMSlotConfig)
    tool: LLMSlotConfig = Field(default_factory=LLMSlotConfig)
    heartbeat: LLMHeartbeatConfig = Field(default_factory=LLMHeartbeatConfig)


class NIMConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://integrate.api.nvidia.com/v1"
    model: str = "moonshotai/kimi-k2-instruct"
    provider: str = ""


class ConsciousnessConfig(BaseModel):
    default_interval: int = 600
    min_interval: int = 120
    max_interval: int = 14400
    after_chat_interval: int = 120
    conversation_end_delay: int = 300


class MessageConfig(BaseModel):
    buffer_seconds: float = 4.0


class MemoryConfig(BaseModel):
    episodic_extract_enabled: bool = True
    episodic_extract_min_turns: int = 3
    episodic_extract_window_size: int = 20
    semantic_distill_enabled: bool = True
    semantic_distill_episodes_window: int = 20
    semantic_distill_dedup_threshold: float = 0.85
    working_set_top_k: int = 10


class ShellConfig(BaseModel):
    enabled: bool = True
    allow_sudo: bool = False
    timeout: int = 30
    default_cwd: str = ""
    max_output_chars: int = 4000
    backend: str = "local"


class TaskConfig(BaseModel):
    max_tool_rounds: int = Field(default=32, ge=1)
    no_action_budget: int = 3
    error_burst_threshold: int = 3


class SLOConfig(BaseModel):
    shell_p95_ms: int = Field(default=2000, ge=1)
    web_p95_ms: int = Field(default=5000, ge=1)
    long_command_cutoff_ms: int = Field(default=10000, ge=1)
    event_start_to_ui_p95_ms: int = Field(default=200, ge=1)
    event_update_throttle_ms: int = Field(default=500, ge=1)
    latency_window_size: int = Field(default=200, ge=1)
    latency_min_samples: int = Field(default=20, ge=1)


class LoopDetectionConfig(BaseModel):
    enabled: bool = True
    history_size: int = Field(default=30, ge=1)
    warning_threshold: int = Field(default=10, ge=1)
    critical_threshold: int = Field(default=20, ge=1)
    global_circuit_breaker_threshold: int = Field(default=30, ge=1)
    detector_generic_repeat: bool = True
    detector_ping_pong: bool = True
    detector_known_poll_no_progress: bool = True


class SearchConfig(BaseModel):
    chat_web_tools_enabled: bool = True
    tavily_api_key: str = ""
    tavily_country: str = "china"
    bocha_api_key: str = ""


class APIConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765
    session_cookie_name: str = "lapwing_session"
    session_ttl_seconds: int = 43200
    allowed_origins: CsvList = Field(default_factory=lambda: [
        "http://localhost:1420", "http://127.0.0.1:1420", "http://127.0.0.1:8765",
        "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
    ])


class BrowserVisionConfig(BaseModel):
    enabled: bool = True
    slot: str = "browser_vision"
    max_description_chars: int = 500
    cache_ttl_seconds: int = 30
    img_threshold: int = 5
    alt_ratio_threshold: float = 0.3


class MiniMaxVLMConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    host: str = "https://api.minimaxi.com"


class BrowserConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    proxy_server: str = ""
    user_data_dir: str = ""
    max_tabs: int = 8
    page_text_max_chars: int = 4000
    navigation_timeout_ms: int = 30000
    action_timeout_ms: int = 10000
    screenshot_dir: str = ""
    screenshot_retain_days: int = 7
    viewport_width: int = 1280
    viewport_height: int = 720
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    max_element_count: int = 50
    wait_after_action_ms: int = 1000
    url_blacklist: CsvList = Field(default_factory=list)
    url_whitelist: CsvList = Field(default_factory=list)
    block_internal_network: bool = True
    sensitive_action_words: CsvList = Field(default_factory=lambda: [
        "delete", "remove", "pay", "purchase", "buy", "submit order",
        "删除", "移除", "支付", "购买", "确认订单", "提交订单",
    ])
    vision: BrowserVisionConfig = Field(default_factory=BrowserVisionConfig)
    minimax_vlm: MiniMaxVLMConfig = Field(default_factory=MiniMaxVLMConfig)


class AuthConfig(BaseModel):
    owner_ids: CsvList = Field(default_factory=list)
    trusted_ids: CsvList = Field(default_factory=list)
    refresh_skew_seconds: int = 300


class DesktopConfig(BaseModel):
    default_owner: bool = False
    ws_chat_id_prefix: str = "desktop"


class SkillConfig(BaseModel):
    enabled: bool = False
    sandbox_image: str = "lapwing-sandbox"
    sandbox_timeout: int = 30


class SandboxTierConfig(BaseModel):
    memory_mb: int = 256
    cpus: float = 0.5
    timeout: int = 30


class SandboxConfig(BaseModel):
    docker_image: str = "lapwing-sandbox:latest"
    network: str = "lapwing-sandbox"
    strict: SandboxTierConfig = Field(default_factory=lambda: SandboxTierConfig(
        memory_mb=256, cpus=0.5, timeout=30,
    ))
    standard: SandboxTierConfig = Field(default_factory=lambda: SandboxTierConfig(
        memory_mb=512, cpus=1.0, timeout=60,
    ))
    privileged: SandboxTierConfig = Field(default_factory=lambda: SandboxTierConfig(
        memory_mb=1024, cpus=2.0, timeout=300,
    ))


class AgentTeamConfig(BaseModel):
    enabled: bool = True


class CodexConfig(BaseModel):
    auth_authorize_url: str = "https://auth.openai.com/oauth/authorize"
    auth_token_url: str = "https://auth.openai.com/oauth/token"
    auth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    auth_redirect_host: str = "localhost"
    auth_redirect_port: int = 1455
    auth_redirect_path: str = "/auth/callback"
    auth_proxy_url: str = ""
    fallback_model: str = "gpt-5.3-codex"


class CompactionConfig(BaseModel):
    trigger_ratio: float = 0.8
    summary_max_tokens: int = 300


class LogConfig(BaseModel):
    level: str = "INFO"


# ── root settings ────────────────────────────

def _inject_env(data: dict[str, Any]) -> dict[str, Any]:
    """从 os.environ 注入环境变量到 data dict，优先级高于 TOML 和默认值。"""
    for env_name, path in _ENV_MAP.items():
        val = os.environ.get(env_name)
        if val is None:
            continue
        if len(path) == 1:
            data[path[0]] = val
            continue
        d = data
        ok = True
        for p in path[:-1]:
            if p not in d:
                d[p] = {}
            elif not isinstance(d[p], dict):
                ok = False
                break
            d = d[p]
        if ok:
            d[path[-1]] = val
    return data


class LapwingSettings(BaseSettings):
    """
    Lapwing 根配置。

    加载顺序：env (.env + os.environ) > config.toml > 默认值。
    TOML 为主配置源；env vars 为覆盖层（Docker/CI/敏感凭据）。
    不使用 pydantic 的 env_nested_delimiter（系统 SHELL 等冲突），
    改为在 model_validator 中按 _ENV_MAP 显式注入。
    """

    model_config = SettingsConfigDict(
        extra="ignore",
    )

    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    nim: NIMConfig = Field(default_factory=NIMConfig)
    consciousness: ConsciousnessConfig = Field(default_factory=ConsciousnessConfig)
    message: MessageConfig = Field(default_factory=MessageConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    shell: ShellConfig = Field(default_factory=ShellConfig)
    task: TaskConfig = Field(default_factory=TaskConfig)
    slo: SLOConfig = Field(default_factory=SLOConfig)
    loop_detection: LoopDetectionConfig = Field(default_factory=LoopDetectionConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    desktop: DesktopConfig = Field(default_factory=DesktopConfig)
    skill: SkillConfig = Field(default_factory=SkillConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    agent_team: AgentTeamConfig = Field(default_factory=AgentTeamConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    credential_vault_path: str = ""
    phase0_mode: str = ""

    @model_validator(mode="before")
    @classmethod
    def _inject_env_vars(cls, data: dict[str, Any]) -> dict[str, Any]:
        """按 _ENV_MAP 将 os.environ 注入，优先级高于 TOML 和默认值。"""
        return _inject_env(data)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, _TomlSource(settings_cls))


@lru_cache
def get_settings() -> LapwingSettings:
    return LapwingSettings()


def reload_settings() -> LapwingSettings:
    get_settings.cache_clear()
    return get_settings()
