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
    # ── memory.wiki ──
    "MEMORY_WIKI_ENABLED": ["memory", "wiki", "enabled"],
    "MEMORY_WIKI_CONTEXT_ENABLED": ["memory", "wiki", "context_enabled"],
    "MEMORY_WIKI_WRITE_ENABLED": ["memory", "wiki", "write_enabled"],
    "MEMORY_WIKI_GATE_ENABLED": ["memory", "wiki", "gate_enabled"],
    "MEMORY_WIKI_LINT_ENABLED": ["memory", "wiki", "lint_enabled"],
    "MEMORY_WIKI_DIR": ["memory", "wiki", "wiki_dir"],
    "MEMORY_WIKI_CONTEXT_BUDGET_RATIO": ["memory", "wiki", "context_budget_ratio"],
    # ── shell ──
    "SHELL_ENABLED": ["shell", "enabled"],
    "SHELL_ALLOW_SUDO": ["shell", "allow_sudo"],
    "SHELL_TIMEOUT": ["shell", "timeout"],
    "SHELL_DEFAULT_CWD": ["shell", "default_cwd"],
    "SHELL_MAX_OUTPUT_CHARS": ["shell", "max_output_chars"],
    "SHELL_BACKEND": ["shell", "backend"],
    "SHELL_WORKSPACE_OWNER": ["shell", "workspace_owner"],
    "SHELL_DOCKER_IMAGE": ["shell", "docker_image"],
    "SHELL_DOCKER_WORKSPACE": ["shell", "docker_workspace"],
    "LAPWING_WORKSPACE_OWNER": ["shell", "workspace_owner"],
    "LAPWING_DOCKER_IMAGE": ["shell", "docker_image"],
    "LAPWING_DOCKER_WORKSPACE": ["shell", "docker_workspace"],
    # ── task ──
    "TASK_MAX_TOOL_ROUNDS": ["task", "max_tool_rounds"],
    "TASK_NO_ACTION_BUDGET": ["task", "no_action_budget"],
    "TASK_ERROR_BURST_THRESHOLD": ["task", "error_burst_threshold"],
    # ── intent_router ──
    "INTENT_ROUTER_ENABLED": ["intent_router", "enabled"],
    "INTENT_ROUTER_SESSION_TTL_SECONDS": ["intent_router", "session_ttl_seconds"],
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
    "LOOP_DETECTION_BLOCKING": ["loop_detection", "blocking"],
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
    "AGENT_TEAM_RESEARCHER_MAX_ROUNDS": ["agent_team", "researcher", "max_rounds"],
    "AGENT_TEAM_RESEARCHER_TIMEOUT_SECONDS": ["agent_team", "researcher", "timeout_seconds"],
    "AGENT_TEAM_RESEARCHER_MAX_TOKENS": ["agent_team", "researcher", "max_tokens"],
    "AGENT_TEAM_CODER_MAX_ROUNDS": ["agent_team", "coder", "max_rounds"],
    "AGENT_TEAM_CODER_TIMEOUT_SECONDS": ["agent_team", "coder", "timeout_seconds"],
    "AGENT_TEAM_CODER_MAX_TOKENS": ["agent_team", "coder", "max_tokens"],
    # ── codex ──
    "OPENAI_CODEX_AUTH_AUTHORIZE_URL": ["codex", "auth_authorize_url"],
    "OPENAI_CODEX_AUTH_TOKEN_URL": ["codex", "auth_token_url"],
    "OPENAI_CODEX_AUTH_CLIENT_ID": ["codex", "auth_client_id"],
    "OPENAI_CODEX_AUTH_REDIRECT_HOST": ["codex", "auth_redirect_host"],
    "OPENAI_CODEX_AUTH_REDIRECT_PORT": ["codex", "auth_redirect_port"],
    "OPENAI_CODEX_AUTH_REDIRECT_PATH": ["codex", "auth_redirect_path"],
    "OPENAI_CODEX_AUTH_PROXY_URL": ["codex", "auth_proxy_url"],
    "CODEX_FALLBACK_MODEL": ["codex", "fallback_model"],
    # ── focus ──
    "FOCUS_ENABLED": ["focus", "enabled"],
    "FOCUS_TIMEOUT_SECONDS": ["focus", "timeout_seconds"],
    "FOCUS_RAPID_GAP_SECONDS": ["focus", "rapid_gap_seconds"],
    "FOCUS_MIN_ENTRIES_TO_KEEP": ["focus", "min_entries_to_keep"],
    "FOCUS_MAX_DORMANT": ["focus", "max_dormant"],
    "FOCUS_DORMANT_TTL_HOURS": ["focus", "dormant_ttl_hours"],
    "FOCUS_CLOSED_TTL_HOURS": ["focus", "closed_ttl_hours"],
    "FOCUS_REACTIVATE_THRESHOLD": ["focus", "reactivate_threshold"],
    # ── identity ──
    "IDENTITY_PARSER_ENABLED": ["identity", "parser_enabled"],
    "IDENTITY_STORE_ENABLED": ["identity", "store_enabled"],
    "IDENTITY_RETRIEVER_ENABLED": ["identity", "retriever_enabled"],
    "IDENTITY_INJECTOR_ENABLED": ["identity", "injector_enabled"],
    "IDENTITY_GATE_ENABLED": ["identity", "gate_enabled"],
    "IDENTITY_SYSTEM_KILLSWITCH": ["identity", "identity_system_killswitch"],
    # ── log ──
    "LOG_LEVEL": ["log", "level"],
    # ── top-level ──
    "CREDENTIAL_VAULT_PATH": ["credential_vault_path"],
    "PHASE0_MODE": ["phase0_mode"],
    # ── agents ──
    "AGENTS_REQUIRE_CANDIDATE_APPROVAL_FOR_PERSISTENCE": ["agents", "require_candidate_approval_for_persistence"],
    "AGENTS_CANDIDATE_TOOLS_ENABLED": ["agents", "candidate_tools_enabled"],
    "AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS": ["agents", "candidate_evidence_max_age_days"],
    # ── capabilities ──
    "CAPABILITIES_ENABLED": ["capabilities", "enabled"],
    "CAPABILITIES_RETRIEVAL_ENABLED": ["capabilities", "retrieval_enabled"],
    "CAPABILITIES_CURATOR_ENABLED": ["capabilities", "curator_enabled"],
    "CAPABILITIES_CURATOR_DRY_RUN_ENABLED": ["capabilities", "curator_dry_run_enabled"],
    "CAPABILITIES_AUTO_DRAFT_ENABLED": ["capabilities", "auto_draft_enabled"],
    "CAPABILITIES_EXECUTION_SUMMARY_ENABLED": ["capabilities", "execution_summary_enabled"],
    "CAPABILITIES_AUTO_PROPOSAL_ENABLED": ["capabilities", "auto_proposal_enabled"],
    "CAPABILITIES_AUTO_PROPOSAL_MIN_CONFIDENCE": ["capabilities", "auto_proposal_min_confidence"],
    "CAPABILITIES_AUTO_PROPOSAL_ALLOW_HIGH_RISK": ["capabilities", "auto_proposal_allow_high_risk"],
    "CAPABILITIES_AUTO_PROPOSAL_MAX_PER_SESSION": ["capabilities", "auto_proposal_max_per_session"],
    "CAPABILITIES_AUTO_PROPOSAL_DEDUPE_WINDOW_HOURS": ["capabilities", "auto_proposal_dedupe_window_hours"],
    "CAPABILITIES_EXTERNAL_IMPORT_ENABLED": ["capabilities", "external_import_enabled"],
    "CAPABILITIES_QUARANTINE_TRANSITION_REQUESTS_ENABLED": ["capabilities", "quarantine_transition_requests_enabled"],
    "CAPABILITIES_QUARANTINE_ACTIVATION_PLANNING_ENABLED": ["capabilities", "quarantine_activation_planning_enabled"],
    "CAPABILITIES_LIFECYCLE_TOOLS_ENABLED": ["capabilities", "lifecycle_tools_enabled"],
    "CAPABILITIES_TRUST_ROOT_TOOLS_ENABLED": ["capabilities", "trust_root_tools_enabled"],
    "CAPABILITIES_STABLE_PROMOTION_TRUST_GATE_ENABLED": ["capabilities", "stable_promotion_trust_gate_enabled"],
    "CAPABILITIES_REPAIR_QUEUE_TOOLS_ENABLED": ["capabilities", "repair_queue_tools_enabled"],
    "CAPABILITIES_DATA_DIR": ["capabilities", "data_dir"],
    "CAPABILITIES_INDEX_DB_PATH": ["capabilities", "index_db_path"],
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


class MemoryWikiConfig(BaseModel):
    """Wiki layer (Phase 1+) — controlled rollout via flags.

    Phase 1 ships read-only injection; ``write_enabled`` stays off until
    Phase 2. ``context_budget_ratio`` is the Phase 1 hard ceiling; Phase 3
    replaces it with a dynamic allocator.
    """
    enabled: bool = True
    context_enabled: bool = True
    write_enabled: bool = False
    gate_enabled: bool = True
    lint_enabled: bool = False
    wiki_dir: str = "data/memory/wiki"
    context_budget_ratio: float = Field(default=0.40, ge=0.0, le=1.0)


class MemoryConfig(BaseModel):
    episodic_extract_enabled: bool = True
    episodic_extract_min_turns: int = 3
    episodic_extract_window_size: int = 20
    semantic_distill_enabled: bool = True
    semantic_distill_episodes_window: int = 20
    semantic_distill_dedup_threshold: float = 0.85
    working_set_top_k: int = 10
    wiki: MemoryWikiConfig = Field(default_factory=MemoryWikiConfig)


class ShellConfig(BaseModel):
    enabled: bool = True
    allow_sudo: bool = False
    timeout: int = 30
    default_cwd: str = ""
    max_output_chars: int = 4000
    backend: str = "local"
    workspace_owner: str = ""
    docker_image: str = "lapwing-sandbox:latest"
    docker_workspace: str = "/workspace"


class TaskConfig(BaseModel):
    max_tool_rounds: int = Field(default=32, ge=1)
    no_action_budget: int = 3
    error_burst_threshold: int = 3


class IntentRouterConfig(BaseModel):
    enabled: bool = True
    session_ttl_seconds: int = Field(default=300, ge=1)


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
    blocking: bool = True
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


class AgentRoleConfig(BaseModel):
    """单个 agent 的运行参数。"""
    max_rounds: int = Field(default=15, ge=1)
    timeout_seconds: int = Field(default=300, ge=1)
    max_tokens: int = Field(default=40000, ge=256)


class AgentTeamDynamicConfig(BaseModel):
    """Dynamic-agent runtime caps (Blueprint §13)."""
    enabled: bool = True
    max_persistent_agents: int = Field(default=10, ge=0)
    max_session_agents: int = Field(default=5, ge=0)
    session_cleanup_interval_seconds: int = Field(default=300, ge=1)


class AgentTeamConfig(BaseModel):
    enabled: bool = True
    researcher: AgentRoleConfig = Field(
        default_factory=lambda: AgentRoleConfig(
            max_rounds=15, timeout_seconds=300, max_tokens=40000,
        )
    )
    coder: AgentRoleConfig = Field(
        default_factory=lambda: AgentRoleConfig(
            max_rounds=20, timeout_seconds=600, max_tokens=50000,
        )
    )
    dynamic: AgentTeamDynamicConfig = Field(default_factory=AgentTeamDynamicConfig)


class BudgetConfig(BaseModel):
    """Per-turn budget caps shared across Brain + delegated agents (Blueprint §5)."""
    max_llm_calls: int = Field(default=50, ge=1)
    max_tool_calls: int = Field(default=100, ge=1)
    max_total_tokens: int = Field(default=200_000, ge=1)
    max_wall_time_seconds: float = Field(default=600.0, gt=0)
    max_delegation_depth: int = Field(default=1, ge=0)


class CodexConfig(BaseModel):
    auth_authorize_url: str = "https://auth.openai.com/oauth/authorize"
    auth_token_url: str = "https://auth.openai.com/oauth/token"
    auth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    auth_redirect_host: str = "localhost"
    auth_redirect_port: int = 1455
    auth_redirect_path: str = "/auth/callback"
    auth_proxy_url: str = ""
    fallback_model: str = "gpt-5.3-codex"


class FocusConfig(BaseModel):
    enabled: bool = True
    timeout_seconds: int = Field(default=1800, ge=1)
    rapid_gap_seconds: int = Field(default=60, ge=0)
    min_entries_to_keep: int = Field(default=4, ge=0)
    max_dormant: int = Field(default=10, ge=0)
    dormant_ttl_hours: int = Field(default=24, ge=1)
    closed_ttl_hours: int = Field(default=72, ge=1)
    reactivate_threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class LogConfig(BaseModel):
    level: str = "INFO"


class InnerTickConfig(BaseModel):
    """Per-tick runtime budget for the autonomous inner-thinking loop.

    Distinct from [consciousness] which controls scheduling cadence.
    These knobs cap how much one tick may spend before yielding —
    inner ticks must not turn into long-running maintenance jobs.
    """
    enabled: bool = True
    base_interval_seconds: int = 600
    min_interval_seconds: int = 300
    max_interval_seconds: int = 14400
    timeout_seconds: int = 120
    max_tool_rounds: int = 3
    no_action_budget: int = 2
    error_burst_threshold: int = 2


class ProactiveMessagesConfig(BaseModel):
    """Rate limiting + quiet hours for proactive send_message calls.

    Direct chat replies use bare assistant text and never go through
    send_message — this gate only fires on proactive/background flows
    (inner ticks, reminders, agent compose_proactive paths).
    """
    enabled: bool = True
    max_per_day: int = 3
    min_minutes_between: int = 90
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "08:00"
    allow_urgent_bypass: bool = True
    urgent_bypass_categories: list[str] = Field(
        default_factory=lambda: ["reminder_due", "safety", "explicit_commitment"]
    )


class IdentityConfig(BaseModel):
    parser_enabled: bool = True
    store_enabled: bool = True
    retriever_enabled: bool = True
    injector_enabled: bool = False
    gate_enabled: bool = False
    reviewer_enabled: bool = False
    l1_memory_enabled: bool = False
    evolution_enabled: bool = False
    identity_system_killswitch: bool = False


class AgentsConfig(BaseModel):
    """Feature flags for the dynamic agent system (Phase 6C+).

    All flags default to False so that existing behavior is unchanged
    unless explicitly enabled.
    """
    require_candidate_approval_for_persistence: bool = False
    # Phase 6D: operator tools for managing AgentCandidate objects.
    candidate_tools_enabled: bool = False
    # Phase 6D: optional evidence staleness threshold in days.
    # None = no enforcement; an integer = max age for high/medium evidence.
    # Default 90 days; set to 0 or None to disable freshness checks.
    candidate_evidence_max_age_days: int | None = 90


class CapabilitiesConfig(BaseModel):
    """Feature flags for the Capability Evolution System (Phase 0+).

    All flags default to False. No runtime code reads these in Phase 0/1;
    they exist so Phase 2+ can gate capability retrieval, curation, and
    auto-drafting behind them.

    Phase 3C: lifecycle_tools_enabled gates evaluate/plan/transition tools
    which require both capabilities.enabled=true AND this flag to be registered.
    """
    enabled: bool = False
    retrieval_enabled: bool = False
    curator_enabled: bool = False
    curator_dry_run_enabled: bool = False
    auto_draft_enabled: bool = False
    execution_summary_enabled: bool = False
    lifecycle_tools_enabled: bool = False
    # Phase 5D: controlled auto-proposal persistence
    auto_proposal_enabled: bool = False
    auto_proposal_min_confidence: float = 0.75
    auto_proposal_allow_high_risk: bool = False
    auto_proposal_max_per_session: int = 3
    auto_proposal_dedupe_window_hours: int = 24
    # Phase 7A: external package import into quarantine only
    external_import_enabled: bool = False
    # Phase 7C: quarantine testing transition requests (operator-only bridge)
    quarantine_transition_requests_enabled: bool = False
    # Phase 7D-A: quarantine activation planning (planner-only, no activation)
    quarantine_activation_planning_enabled: bool = False
    # Phase 7D-B: quarantine activation apply (operator-only, testing only)
    quarantine_activation_apply_enabled: bool = False
    # Phase 8B-3: trust root operator tools (operator-only metadata management)
    trust_root_tools_enabled: bool = False
    # Phase 8C-1: stable promotion trust gate (testing -> stable provenance/trust/integrity checks)
    stable_promotion_trust_gate_enabled: bool = False
    # Maintenance C: repair queue operator tools (operator-only)
    repair_queue_tools_enabled: bool = False
    data_dir: str = "data/capabilities"
    index_db_path: str = "data/capabilities/capability_index.sqlite"


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
    intent_router: IntentRouterConfig = Field(default_factory=IntentRouterConfig)
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
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    focus: FocusConfig = Field(default_factory=FocusConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    proactive_messages: ProactiveMessagesConfig = Field(default_factory=ProactiveMessagesConfig)
    inner_tick: InnerTickConfig = Field(default_factory=InnerTickConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
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
