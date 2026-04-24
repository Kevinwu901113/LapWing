"""LLM 路由器 - 按用途（purpose）选择对应的模型和 client。"""

import json
import logging
import time
from typing import Any, Awaitable, Callable

from src.auth.service import AuthManager
from src.core.reasoning_tags import strip_internal_thinking_tags
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    current_chat_id,
    current_iteration_id,
    new_request_id,
    set_last_llm_request_id,
)

# Re-export types for backward compatibility
from src.core.llm_types import ToolCallRequest, ToolTurnResult, ModelOption  # noqa: F401
from src.core.llm_protocols import (  # noqa: F401
    _detect_api_type,
    _is_native_anthropic,
    _mark_last_user_message_cache,
    _normalize_anthropic_base_url,
    _split_system_messages,
    _extract_anthropic_text,
    _has_anthropic_thinking,
    _safe_parse_json,
    _extract_json_from_text,
    _normalize_openai_message_content,
    _normalize_openai_tools,
    _normalize_anthropic_tools,
    _extract_openai_tool_calls,
    _extract_anthropic_tool_calls,
    _convert_messages_to_responses_api,
    _normalize_responses_api_tools,
    _extract_responses_api_tool_calls,
)
from config.settings import (
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_CHAT_BASE_URL,
    LLM_CHAT_MODEL,
    LLM_TOOL_BASE_URL,
    LLM_TOOL_MODEL,
    NIM_BASE_URL,
    NIM_MODEL,
)

logger = logging.getLogger("lapwing.core.llm_router")

_RECOVERABLE_FAILURES = {"auth", "rate_limit", "timeout", "billing"}

_THINKING_RETRY_COOLDOWN_SECONDS = 30.0
_MODEL_PURPOSES: tuple[str, ...] = ("chat", "tool", "heartbeat")


def _extract_usage(response) -> tuple[int | None, int | None]:
    """从 API 响应中提取 token 使用量，兼容 Anthropic 和 OpenAI 格式。"""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    # Anthropic 格式: input_tokens / output_tokens
    input_t = getattr(usage, "input_tokens", None)
    output_t = getattr(usage, "output_tokens", None)
    # OpenAI 格式: prompt_tokens / completion_tokens
    if input_t is None:
        input_t = getattr(usage, "prompt_tokens", None)
    if output_t is None:
        output_t = getattr(usage, "completion_tokens", None)
    return input_t, output_t

# Mapping from new slot names to legacy purposes (for AuthManager)
_PURPOSE_TO_DEFAULT_SLOT: dict[str, str] = {
    "chat": "main_conversation",
    "tool": "agent_execution",
    "heartbeat": "heartbeat_proactive",
}
_SLOT_TO_PURPOSE: dict[str, str] = {
    "main_conversation": "chat",
    "persona_expression": "chat",
    "self_reflection": "chat",
    "lightweight_judgment": "tool",
    "memory_processing": "tool",
    "agent_execution": "tool",
    "agent_coder": "tool",
    "agent_team_lead": "tool",
    "agent_researcher": "tool",
    "heartbeat_proactive": "heartbeat",
}




# ChatGPT Codex 代理端点只接受这些参数
_CODEX_ALLOWED_KEYS = {
    "model",
    "input",
    "instructions",
    "stream",
    "store",
    "include",
    "tools",
    "tool_choice",
    "reasoning",
    "previous_response_id",
    "truncation",
}

_CODEX_DEFAULT_INSTRUCTIONS = "你是 Lapwing 的浏览器视觉理解模块。请用中文回复。"


def _sanitize_codex_payload(payload: dict) -> dict:
    """移除 ChatGPT Codex 代理端点不支持的参数。

    Codex 端点 (chatgpt.com/backend-api/codex/responses) 不是标准 OpenAI
    Responses API，只接受有限的参数集。不支持的参数（如 max_output_tokens、
    context_management 等）会导致 400 错误。
    """
    return {k: v for k, v in payload.items() if k in _CODEX_ALLOWED_KEYS}


async def _collect_codex_stream(client, payload: dict) -> tuple[str, list[dict], dict[str, Any]]:
    """从 Codex Responses API SSE 流中收集文本和 output items。

    返回 (text, output_items, response_meta)。
    response_meta 包含 assistant message 的 phase 等元数据。
    """
    text_parts: list[str] = []
    output_items: list[dict] = []
    response_meta: dict[str, Any] = {}

    async for event in client.post_stream(payload):
        event_type = event.get("type", "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta", "")
            if delta:
                text_parts.append(delta)
        elif event_type == "response.output_text.done":
            # done 事件包含完整文本，优先使用
            text_parts = [event.get("text", "")]
        elif event_type == "response.output_item.done":
            item = event.get("item") or {}
            output_items.append(item)
            # 捕获 assistant message 的 phase（gpt-5.3-codex）
            if item.get("type") == "message" and "phase" in item:
                response_meta["phase"] = item["phase"]

    return "".join(text_parts), output_items, response_meta


def _mut_content_blocks_from_anthropic(response: Any) -> list[dict[str, Any]]:
    """Extract typed content_blocks from an Anthropic response for mutation_log."""
    if response is None:
        return []
    blocks: list[dict[str, Any]] = []
    for block in getattr(response, "content", None) or []:
        btype = getattr(block, "type", None)
        if btype == "thinking":
            blocks.append(
                {
                    "type": "thinking",
                    "content": getattr(block, "thinking", None)
                    or getattr(block, "text", None)
                    or "",
                    "signature": getattr(block, "signature", None),
                }
            )
        elif btype == "text":
            blocks.append({"type": "text", "content": getattr(block, "text", "") or ""})
        elif btype == "tool_use":
            raw_input = getattr(block, "input", None) or {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", None),
                    "name": getattr(block, "name", None),
                    "input": dict(raw_input) if isinstance(raw_input, dict) else raw_input,
                }
            )
        else:
            blocks.append({"type": btype or "unknown"})
    return blocks


def _mut_content_blocks_from_openai(response: Any) -> list[dict[str, Any]]:
    if response is None or not getattr(response, "choices", None):
        return []
    msg = response.choices[0].message
    blocks: list[dict[str, Any]] = []
    content = getattr(msg, "content", None) or ""
    if content:
        blocks.append({"type": "text", "content": content})
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        blocks.append(
            {
                "type": "tool_use",
                "id": getattr(tc, "id", None),
                "name": getattr(fn, "name", None),
                "input": _safe_parse_json(getattr(fn, "arguments", "") or ""),
            }
        )
    return blocks


def _mut_content_blocks_from_codex(value: Any) -> list[dict[str, Any]]:
    """Codex returns (text, output_items, meta). Map to content_blocks."""
    if value is None:
        return []
    try:
        text, output_items, _meta = value
    except (TypeError, ValueError):
        return []
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "content": text})
    for item in output_items or []:
        if item.get("type") == "function_call":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": item.get("call_id") or item.get("id"),
                    "name": item.get("name"),
                    "input": _safe_parse_json(item.get("arguments", "") or ""),
                }
            )
    return blocks


_OPENAI_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


def _mut_stop_reason(response: Any, protocol: str) -> str | None:
    if response is None:
        return None
    if protocol == "anthropic":
        return getattr(response, "stop_reason", None)
    if protocol == "openai":
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        raw = getattr(choices[0], "finish_reason", None)
        if raw is None:
            return None
        # Normalise to the Anthropic vocabulary used throughout mutation_log
        # so downstream consumers (Step 1k observability tests, analytics)
        # don't have to branch per provider. Unrecognised values pass through.
        return _OPENAI_FINISH_REASON_MAP.get(raw, raw)
    if protocol == "codex_oauth":
        return "stream_end"
    return None


def _mut_usage_dict(response: Any, protocol: str) -> dict[str, int | None]:
    if response is None or protocol == "codex_oauth":
        return {"input_tokens": None, "output_tokens": None}
    input_t, output_t = _extract_usage(response)
    return {"input_tokens": input_t, "output_tokens": output_t}


def _mut_content_blocks(response: Any, protocol: str) -> list[dict[str, Any]]:
    if protocol == "anthropic":
        return _mut_content_blocks_from_anthropic(response)
    if protocol == "openai":
        return _mut_content_blocks_from_openai(response)
    if protocol == "codex_oauth":
        return _mut_content_blocks_from_codex(response)
    return []


class LLMRouter:
    """按 purpose 路由到对应 LLM client。"""

    def __init__(
        self,
        auth_manager: AuthManager | None = None,
        model_config=None,
        *,
        mutation_log: StateMutationLog | None = None,
    ) -> None:
        self._auth_manager = auth_manager or AuthManager()
        self._model_config = model_config
        self._mutation_log = mutation_log
        self._clients: dict[str, Any] = {}
        self._models: dict[str, str] = {}
        self._api_types: dict[str, str] = {}
        self._base_urls: dict[str, str] = {}
        self._reasoning_effort: dict[str, str | None] = {}
        self._context_compaction: dict[str, bool] = {}
        self._session_model_overrides: dict[tuple[str, str], str] = {}
        self._model_options: list[ModelOption] = []
        self._model_options_by_ref: dict[str, ModelOption] = {}
        self._model_options_by_alias: dict[str, ModelOption] = {}
        self._last_error_ts: dict[str, float] = {}
        self._model_provider_map: dict[str, Any] = {}  # model_id → ProviderInfo
        self._fallback_models: dict[str, list[str]] = {}  # slot → fallback model chain
        self._setup_routing()
        self._setup_model_options()

    @staticmethod
    def _clamp_provider_params(params: dict, is_anthropic_compat: bool) -> dict:
        """Provider-specific 参数边界检查。Anthropic 兼容 API 要求 temperature ∈ (0.0, 1.0]。"""
        temperature = params.get("temperature")
        if temperature is not None and is_anthropic_compat:
            if temperature <= 0:
                params["temperature"] = 0.01
            elif temperature > 1.0:
                params["temperature"] = 1.0
        return params

    def set_mutation_log(self, mutation_log: StateMutationLog | None) -> None:
        """Install the mutation log after construction.

        Allows :class:`AppContainer` to wire the log once it's built, without
        forcing mutation_log to be a __init__ argument in the many test
        sites that construct LLMRouter with no args.
        """
        self._mutation_log = mutation_log

    def _setup_routing(self) -> None:
        """Load routing config from ModelConfigManager or fall back to .env."""
        if self._model_config is None:
            self._setup_clients_legacy()
            return

        from src.core.model_config import SLOT_DEFINITIONS

        for slot_id in SLOT_DEFINITIONS:
            resolved = self._model_config.resolve_slot(slot_id)
            if resolved is None:
                logger.debug(f"Slot '{slot_id}' not configured, will use fallback")
                continue

            base_url, model, api_key, api_type = resolved
            self._base_urls[slot_id] = base_url
            self._models[slot_id] = model
            self._api_types[slot_id] = api_type
            self._clients.setdefault(slot_id, None)
            logger.info(
                f"[{slot_id}] 已注册模型路由: "
                f"{model} ({base_url[:40]}..., {api_type})"
            )
            # Push slot config to AuthManager for per-slot credential resolution
            auth_purpose = _SLOT_TO_PURPOSE.get(slot_id, "chat")
            self._auth_manager.register_slot_config(
                slot_id,
                auth_purpose,
                base_url=base_url,
                model=model,
                api_type=api_type,
                api_key=api_key,
            )

        # Build model_id → ProviderInfo reverse map for cross-provider session overrides
        self._model_provider_map.clear()
        for provider in self._model_config.get_full_config().providers:
            for m in provider.models:
                self._model_provider_map[m.id] = provider

        # Populate per-slot codex params from provider config
        self._reasoning_effort.clear()
        self._context_compaction.clear()
        for slot_id in SLOT_DEFINITIONS:
            model = self._models.get(slot_id)
            if model and model in self._model_provider_map:
                prov = self._model_provider_map[model]
                self._reasoning_effort[slot_id] = getattr(prov, "reasoning_effort", None)
                self._context_compaction[slot_id] = getattr(prov, "context_compaction", False)

        # Load per-slot fallback model chains
        self._fallback_models.clear()
        for slot_id in SLOT_DEFINITIONS:
            fallbacks = self._model_config.resolve_fallback_models(slot_id)
            if fallbacks:
                self._fallback_models[slot_id] = fallbacks
                logger.info(f"[{slot_id}] fallback chain: {' → '.join(fallbacks)}")

        # Register legacy purpose keys for backward compatibility
        for purpose, default_slot in _PURPOSE_TO_DEFAULT_SLOT.items():
            if purpose not in self._models and default_slot in self._models:
                self._models[purpose] = self._models[default_slot]
                self._base_urls[purpose] = self._base_urls[default_slot]
                self._api_types[purpose] = self._api_types[default_slot]
                self._clients.setdefault(purpose, None)

    def reload_routing(self) -> None:
        """Hot-reload routing config. Call after frontend saves new config."""
        self._clients.clear()
        self._models.clear()
        self._api_types.clear()
        self._base_urls.clear()
        self._model_provider_map.clear()
        self._reasoning_effort.clear()
        self._context_compaction.clear()
        self._fallback_models.clear()
        self._setup_routing()
        self._setup_model_options()
        logger.info("Model routing reloaded")

    def _setup_clients_legacy(self) -> None:
        """记录各 purpose 的基础路由配置；credential 改由 auth_manager 按请求解析。"""
        if not LLM_BASE_URL or not LLM_MODEL:
            raise ValueError(
                "LLM 通用配置不完整，请检查 config/.env 中的 "
                "LLM_BASE_URL、LLM_MODEL"
            )

        purpose_configs = {
            "chat": (LLM_CHAT_BASE_URL or LLM_BASE_URL, LLM_CHAT_MODEL or LLM_MODEL),
            "tool": (LLM_TOOL_BASE_URL or LLM_BASE_URL, LLM_TOOL_MODEL or LLM_MODEL),
            "heartbeat": (NIM_BASE_URL or LLM_BASE_URL, NIM_MODEL or LLM_MODEL),
        }

        for purpose, (resolved_base_url, resolved_model) in purpose_configs.items():
            api_type = _detect_api_type(resolved_base_url, resolved_model)
            self._clients.setdefault(purpose, None)
            self._models[purpose] = resolved_model
            self._api_types[purpose] = api_type
            self._base_urls[purpose] = resolved_base_url
            logger.info(
                f"[{purpose}] 已注册模型路由: "
                f"{resolved_model} ({resolved_base_url}, {api_type})"
            )

    def _setup_model_options(self) -> None:
        options: list[ModelOption] = []
        options_by_ref: dict[str, ModelOption] = {}
        options_by_alias: dict[str, ModelOption] = {}

        def _add(ref: str, alias: str | None) -> None:
            if not ref or ref in options_by_ref:
                return
            opt = ModelOption(index=len(options) + 1, ref=ref, alias=alias)
            options.append(opt)
            options_by_ref[ref] = opt
            if alias:
                options_by_alias.setdefault(alias.lower(), opt)

        if self._model_config is not None:
            # 从 model_routing.json 的所有 provider 生成
            for provider in self._model_config.get_full_config().providers:
                for m in provider.models:
                    alias = m.name if m.name != m.id else None
                    _add(m.id, alias)
        else:
            # 无 ModelConfigManager 时从已注册的 slot 模型中生成
            for model_id in dict.fromkeys(self._models.values()):
                _add(model_id, None)

        self._model_options = options
        self._model_options_by_ref = options_by_ref
        self._model_options_by_alias = options_by_alias

    def _effective_model_for_purpose(self, purpose: str, *, session_key: str | None = None) -> str:
        if session_key:
            override = self._session_model_overrides.get((session_key, purpose))
            if override:
                return override
        return self._models.get(purpose, LLM_MODEL)

    def list_model_options(self) -> list[dict[str, Any]]:
        return [
            {"index": option.index, "alias": option.alias, "ref": option.ref}
            for option in self._model_options
        ]

    def _resolve_model_option(self, selector: str) -> ModelOption:
        normalized = str(selector or "").strip()
        if not normalized:
            raise ValueError("模型选择不能为空。")
        if not self._model_options:
            raise ValueError("当前没有可用模型，请检查 model_routing.json 配置。")

        if normalized.isdigit():
            index = int(normalized)
            if 1 <= index <= len(self._model_options):
                return self._model_options[index - 1]
            raise ValueError(f"模型编号超出范围：{index}")

        by_alias = self._model_options_by_alias.get(normalized.lower())
        if by_alias is not None:
            return by_alias

        by_ref = self._model_options_by_ref.get(normalized)
        if by_ref is not None:
            return by_ref

        raise ValueError("模型不在 allowlist 中，请先执行 /model list 查看可选项。")

    def switch_session_model(
        self,
        *,
        session_key: str,
        selector: str,
    ) -> dict[str, Any]:
        if not session_key.strip():
            raise ValueError("session_key 不能为空。")
        option = self._resolve_model_option(selector)
        applied: dict[str, str] = {}

        for purpose in _MODEL_PURPOSES:
            self._session_model_overrides[(session_key, purpose)] = option.ref
            applied[purpose] = option.ref

        return {
            "selected": {"index": option.index, "alias": option.alias, "ref": option.ref},
            "applied": applied,
            "status": self.model_status(session_key=session_key),
        }

    def clear_session_model(self, *, session_key: str) -> dict[str, Any]:
        if not session_key.strip():
            raise ValueError("session_key 不能为空。")

        removed = 0
        for purpose in _MODEL_PURPOSES:
            key = (session_key, purpose)
            if key in self._session_model_overrides:
                removed += 1
                self._session_model_overrides.pop(key, None)

        return {
            "cleared": removed,
            "status": self.model_status(session_key=session_key),
        }

    def model_status(self, *, session_key: str | None = None) -> dict[str, Any]:
        purposes: dict[str, dict[str, Any]] = {}
        overrides: dict[str, str] = {}
        for purpose in _MODEL_PURPOSES:
            default_model = self._models.get(purpose, LLM_MODEL)
            override = (
                self._session_model_overrides.get((session_key, purpose))
                if session_key
                else None
            )
            effective_model = override or default_model
            base_url = self._base_urls.get(purpose, LLM_BASE_URL)
            # 优先从 provider map 获取 api_type（解决跨 provider override 问题）
            if override and self._model_provider_map:
                override_provider = self._model_provider_map.get(override)
                api_type = override_provider.api_type if override_provider else _detect_api_type(base_url, effective_model)
            else:
                api_type = self._api_types.get(purpose, _detect_api_type(base_url, effective_model))
            if override:
                overrides[purpose] = override
            purposes[purpose] = {
                "default": default_model,
                "effective": effective_model,
                "override": override,
                "apiType": api_type,
            }
        return {
            "sessionKey": session_key,
            "overrides": overrides,
            "purposes": purposes,
        }

    def _build_anthropic_client(self, *, api_key: str, base_url: str) -> Any:
        try:
            from anthropic import AsyncAnthropic  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "检测到 Anthropic 兼容 provider，但当前环境缺少 `anthropic` 依赖。"
                "请安装：pip install anthropic"
            ) from exc

        return AsyncAnthropic(
            api_key=api_key,
            base_url=_normalize_anthropic_base_url(base_url),
        )

    def _build_openai_client(self, *, api_key: str, base_url: str) -> Any:
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "当前环境缺少 `openai` 依赖。请安装：pip install openai"
            ) from exc

        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    def _resolve_client(
        self,
        routing_key: str,
        auth_value: str | None = None,
        *,
        model_override: str | None = None,
    ) -> tuple[Any, str, str]:
        client_override = self._clients.get(routing_key)
        model = model_override or self._models.get(routing_key, LLM_MODEL)
        base_url = self._base_urls.get(routing_key, LLM_BASE_URL)
        if model_override is None:
            api_type = self._api_types.get(routing_key, _detect_api_type(base_url, model))
        else:
            api_type = _detect_api_type(base_url, model)

        if client_override is not None:
            return client_override, model, api_type

        if not auth_value:
            raise ValueError(f"[{routing_key}] 当前请求没有可用 credential。")

        if api_type == "anthropic":
            client = self._build_anthropic_client(
                api_key=auth_value,
                base_url=base_url,
            )
        else:
            client = self._build_openai_client(
                api_key=auth_value,
                base_url=base_url,
            )
        return client, model, api_type

    def _debug_log_request(self, label: str, routing_key: str, request_kwargs: dict[str, Any]) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug(
            "[%s][%s] API request: model=%s max_completion_tokens=%s messages=%d has_tools=%s has_response_format=%s",
            routing_key,
            label,
            request_kwargs.get("model"),
            request_kwargs.get("max_completion_tokens") or request_kwargs.get("max_tokens"),
            len(request_kwargs.get("messages", [])),
            bool(request_kwargs.get("tools")),
            bool(request_kwargs.get("response_format")),
        )

    def _inject_codex_params(self, payload: dict[str, Any], effective_key: str) -> None:
        """向 codex_oauth payload 注入 reasoning.effort 和 context_management。"""
        effort = self._reasoning_effort.get(effective_key)
        if effort:
            payload["reasoning"] = {"effort": effort}
        if self._context_compaction.get(effective_key, False):
            payload["context_management"] = [{"type": "compaction"}]

    def model_for(self, purpose: str, *, session_key: str | None = None) -> str:
        """返回指定 purpose 实际使用的模型名。"""
        return self._effective_model_for_purpose(purpose, session_key=session_key)

    async def _with_routing_retry(
        self,
        *,
        purpose: str,
        routing_key: str | None = None,
        session_key: str | None,
        allow_failover: bool,
        origin: str | None,
        runner: Callable[[Any, Any, str, str], Awaitable[Any]],
    ) -> Any:
        # routing_key is used for dict lookups (slot name or purpose)
        # purpose is used for AuthManager (must be chat/tool/heartbeat)
        effective_key = routing_key or purpose
        excluded_profiles: set[str] = set()
        last_exc: Exception | None = None
        # Check both slot-specific and purpose-level overrides
        session_model_override = None
        if session_key:
            session_model_override = (
                self._session_model_overrides.get((session_key, effective_key))
                or self._session_model_overrides.get((session_key, purpose))
            )
        client_override = self._clients.get(effective_key)

        # 检查 session override 是否指向不同 provider（如 MiniMax slot → gpt-5.4）
        override_api_type = None
        if session_model_override and self._model_provider_map:
            override_provider = self._model_provider_map.get(session_model_override)
            if override_provider:
                override_api_type = override_provider.api_type

        # codex_oauth 由 SDK 自管理 token，不走 AuthManager candidate 解析
        slot_api_type = self._api_types.get(effective_key)
        codex_from_override = (
            override_api_type == "codex_oauth"
            and slot_api_type != "codex_oauth"
        )
        if override_api_type == "codex_oauth" or slot_api_type == "codex_oauth":
            from src.core.codex_oauth_client import get_client, reset_client
            from config.settings import CODEX_FALLBACK_MODEL
            model = self._models.get(effective_key, CODEX_FALLBACK_MODEL)
            if session_model_override:
                model = session_model_override
            client = await get_client()
            try:
                return await runner(None, client, model, "codex_oauth")
            except Exception as exc:
                if _classify_provider_exception(exc) == "auth":
                    await reset_client()
                    try:
                        client = await get_client()
                        return await runner(None, client, model, "codex_oauth")
                    except Exception:
                        pass
                status_code = getattr(exc, "status_code", None)
                if status_code is None:
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                if codex_from_override and status_code == 400:
                    # 会话 override 到 codex 时，工具闭环偶发 400。
                    # 对这种场景降级回 slot 默认 provider，避免整轮对话失败。
                    logger.warning(
                        "[%s] codex_oauth override 请求返回 400，回退到 slot `%s` 默认路由重试。",
                        purpose,
                        effective_key,
                    )
                    session_model_override = None
                    override_api_type = None
                else:
                    raise

        while True:
            candidates = self._auth_manager.resolve_candidates(
                purpose=purpose,
                slot=effective_key if effective_key != purpose else None,
                session_key=session_key,
                allow_failover=allow_failover,
                exclude_profiles=excluded_profiles,
                origin=origin,
            )
            if not candidates:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(f"[{effective_key}] 没有可用的 auth candidate。")

            for candidate in candidates:
                refresh_attempted = False
                current_candidate = candidate
                while True:
                    try:
                        candidate_model = str(getattr(current_candidate, "model", "") or "").strip()
                        if session_model_override:
                            model_for_attempt = session_model_override
                            use_model_override = True
                        elif client_override is None and candidate_model:
                            model_for_attempt = candidate_model
                            use_model_override = True
                        else:
                            model_for_attempt = self._models.get(effective_key, LLM_MODEL)
                            use_model_override = False

                        client, model, api_type = self._resolve_client(
                            effective_key,
                            auth_value=current_candidate.auth_value,
                            model_override=model_for_attempt if use_model_override else None,
                        )
                        result = await runner(current_candidate, client, model, api_type)
                        self._auth_manager.mark_success(current_candidate)
                        return result
                    except Exception as exc:
                        failure_kind = _classify_provider_exception(exc)
                        last_exc = exc
                        if failure_kind in ("rate_limit", "other"):
                            self._last_error_ts[effective_key] = time.monotonic()
                        if (
                            failure_kind == "auth"
                            and current_candidate.profile_id
                            and current_candidate.profile_type == "oauth"
                            and not refresh_attempted
                        ):
                            try:
                                current_candidate = self._auth_manager.refresh_candidate(current_candidate)
                                refresh_attempted = True
                                logger.info("[%s] OAuth profile `%s` 已刷新，重试本次请求。", purpose, current_candidate.profile_id)
                                continue
                            except Exception as refresh_exc:
                                logger.warning(
                                    "[%s] OAuth profile `%s` 刷新失败: %s",
                                    purpose,
                                    current_candidate.profile_id,
                                    refresh_exc,
                                )

                        # 同 provider 内模型级 fallback（429/529/timeout 时尝试备选模型）
                        fallback_chain = self._fallback_models.get(effective_key, [])
                        if fallback_chain and failure_kind in ("rate_limit", "timeout"):
                            for fb_model in fallback_chain:
                                try:
                                    logger.warning(
                                        "[%s] 主模型 %s 失败(%s)，尝试 fallback → %s",
                                        effective_key, model, failure_kind, fb_model,
                                    )
                                    fb_client, fb_resolved, fb_api_type = self._resolve_client(
                                        effective_key,
                                        auth_value=current_candidate.auth_value,
                                        model_override=fb_model,
                                    )
                                    result = await runner(current_candidate, fb_client, fb_resolved, fb_api_type)
                                    self._auth_manager.mark_success(current_candidate)
                                    return result
                                except Exception as fb_exc:
                                    fb_kind = _classify_provider_exception(fb_exc)
                                    last_exc = fb_exc
                                    logger.warning(
                                        "[%s] fallback 模型 %s 也失败(%s)，继续尝试下一个",
                                        effective_key, fb_model, fb_kind,
                                    )

                        self._auth_manager.mark_failure(current_candidate, failure_kind)
                        if (
                            allow_failover
                            and current_candidate.profile_id
                            and failure_kind in _RECOVERABLE_FAILURES
                        ):
                            excluded_profiles.add(current_candidate.profile_id)
                            logger.warning(
                                "[%s] auth candidate `%s` 失败(%s)，尝试下一个 profile。",
                                purpose,
                                current_candidate.profile_id,
                                failure_kind,
                            )
                            break
                        logger.warning("LLM 调用失败: %s (slot=%s)", exc, effective_key)
                        raise

            if last_exc is not None:
                raise last_exc

    async def _tracked_call(
        self,
        protocol: str,
        request_snapshot: dict[str, Any],
        call_fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Execute an LLM API call, emitting LLM_REQUEST + LLM_RESPONSE mutations.

        ``protocol`` is one of ``"anthropic"``, ``"openai"``, ``"codex_oauth"``.
        ``request_snapshot`` contains the full observable request (model,
        base_url, purpose, messages, system, tools, max_tokens, temperature).
        ``call_fn`` returns an awaitable producing the raw API response
        (or codex tuple). Failures in logging never abort the underlying call.
        """
        if self._mutation_log is None:
            return await call_fn()

        request_id = new_request_id()
        iid = current_iteration_id()
        cid = current_chat_id()

        try:
            await self._mutation_log.record(
                MutationType.LLM_REQUEST,
                {"request_id": request_id, "protocol": protocol, **request_snapshot},
                iteration_id=iid,
                chat_id=cid,
            )
            # Tool calls spawned by the upcoming response can now claim this
            # request_id as their parent in TOOL_CALLED mutations.
            set_last_llm_request_id(request_id)
        except Exception:
            logger.warning(
                "LLM_REQUEST mutation record failed; continuing call",
                exc_info=True,
            )

        start_mono = time.monotonic()
        response: Any = None
        error: str | None = None
        try:
            response = await call_fn()
            return response
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            latency_ms = (time.monotonic() - start_mono) * 1000
            try:
                await self._mutation_log.record(
                    MutationType.LLM_RESPONSE,
                    {
                        "request_id": request_id,
                        "latency_ms": latency_ms,
                        "stop_reason": _mut_stop_reason(response, protocol),
                        "content_blocks": _mut_content_blocks(response, protocol),
                        "usage": _mut_usage_dict(response, protocol),
                        "error": error,
                    },
                    iteration_id=iid,
                    chat_id=cid,
                )
            except Exception:
                logger.warning("LLM_RESPONSE mutation record failed", exc_info=True)

    def _anthropic_request_snapshot(
        self,
        *,
        effective_key: str,
        model: str,
        request_kwargs: dict[str, Any],
        origin: str | None,
    ) -> dict[str, Any]:
        return {
            "model_slot": effective_key,
            "model_name": model,
            "base_url": self._base_urls.get(effective_key, ""),
            "purpose": origin or "",
            "messages": request_kwargs.get("messages"),
            "system": request_kwargs.get("system"),
            "tools": request_kwargs.get("tools"),
            "tool_choice": request_kwargs.get("tool_choice"),
            "max_tokens": request_kwargs.get("max_tokens"),
            "temperature": request_kwargs.get("temperature"),
        }

    def _openai_request_snapshot(
        self,
        *,
        effective_key: str,
        model: str,
        request_kwargs: dict[str, Any],
        origin: str | None,
    ) -> dict[str, Any]:
        return {
            "model_slot": effective_key,
            "model_name": model,
            "base_url": self._base_urls.get(effective_key, ""),
            "purpose": origin or "",
            "messages": request_kwargs.get("messages"),
            "tools": request_kwargs.get("tools"),
            "tool_choice": request_kwargs.get("tool_choice"),
            "max_tokens": request_kwargs.get("max_tokens"),
            "temperature": request_kwargs.get("temperature"),
        }

    def _codex_request_snapshot(
        self,
        *,
        effective_key: str,
        model: str,
        payload: dict[str, Any],
        origin: str | None,
    ) -> dict[str, Any]:
        return {
            "model_slot": effective_key,
            "model_name": model,
            "base_url": self._base_urls.get(effective_key, ""),
            "purpose": origin or "",
            "input": payload.get("input"),
            "instructions": payload.get("instructions"),
            "tools": payload.get("tools"),
            "tool_choice": payload.get("tool_choice"),
            "reasoning": payload.get("reasoning"),
        }

    async def complete(
        self,
        messages: list[dict],
        purpose: str = "chat",
        max_tokens: int = 1024,
        *,
        slot: str | None = None,
        session_key: str | None = None,
        allow_failover: bool = True,
        origin: str | None = None,
    ) -> str:
        """向对应 purpose/slot 的模型发送请求，返回回复文本。"""
        effective_key = slot or purpose
        auth_purpose = _SLOT_TO_PURPOSE.get(effective_key, effective_key)

        async def _runner(candidate, client, model, api_type):
            if api_type == "codex_oauth":
                instructions, input_items = _convert_messages_to_responses_api(messages)
                payload: dict[str, Any] = {
                    "model": model,
                    "input": input_items,
                    "instructions": instructions or _CODEX_DEFAULT_INSTRUCTIONS,
                    "store": False,
                    "stream": True,
                }
                self._inject_codex_params(payload, effective_key)
                payload = _sanitize_codex_payload(payload)
                text, _, _ = await self._tracked_call(
                    "codex_oauth",
                    self._codex_request_snapshot(
                        effective_key=effective_key, model=model, payload=payload, origin=origin
                    ),
                    lambda: _collect_codex_stream(client, payload),
                )
                return strip_internal_thinking_tags(text).strip()

            if api_type == "anthropic":
                system, anthropic_messages = _split_system_messages(messages)
                request_kwargs: dict[str, Any] = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": anthropic_messages,
                }
                if system is not None:
                    # Anthropic prefix cache: 标记 system prompt 为 ephemeral 缓存
                    request_kwargs["system"] = [
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]

                # 对最后一条 user 消息添加 cache marker（仅原生 Anthropic）
                base_url = self._base_urls.get(effective_key, "")
                if _is_native_anthropic(base_url):
                    _mark_last_user_message_cache(anthropic_messages)

                is_compat = not _is_native_anthropic(base_url)
                self._clamp_provider_params(request_kwargs, is_compat)

                response = await self._tracked_call(
                    "anthropic",
                    self._anthropic_request_snapshot(
                        effective_key=effective_key, model=model,
                        request_kwargs=request_kwargs, origin=origin,
                    ),
                    lambda: client.messages.create(**request_kwargs),
                )
                text = _extract_anthropic_text(response)
                if text:
                    return text

                if not text and _has_anthropic_thinking(response):
                    retry_max_tokens = max(max_tokens * 4, 512)
                    if retry_max_tokens > max_tokens:
                        logger.info(
                            f"[{effective_key}] Anthropic 响应仅返回 thinking，"
                            f"自动重试并提升 max_tokens 到 {retry_max_tokens}"
                        )
                        retry_kwargs = dict(request_kwargs)
                        retry_kwargs["max_tokens"] = retry_max_tokens
                        retry_response = await self._tracked_call(
                            "anthropic",
                            self._anthropic_request_snapshot(
                                effective_key=effective_key, model=model,
                                request_kwargs=retry_kwargs,
                                origin=(origin or "") + "/thinking_retry",
                            ),
                            lambda: client.messages.create(**retry_kwargs),
                        )
                        return _extract_anthropic_text(retry_response)

                return text

            request_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            self._debug_log_request("complete", effective_key, request_kwargs)
            response = await self._tracked_call(
                "openai",
                self._openai_request_snapshot(
                    effective_key=effective_key, model=model,
                    request_kwargs=request_kwargs, origin=origin,
                ),
                lambda: client.chat.completions.create(**request_kwargs),
            )
            if not response.choices:
                return ""
            content = response.choices[0].message.content or ""
            stripped = strip_internal_thinking_tags(content).strip()
            if not stripped and "<think" in content.lower():
                # 模型把所有 tokens 花在思考上，检查冷却期后决定是否重试
                last_err = self._last_error_ts.get(effective_key, 0.0)
                elapsed = time.monotonic() - last_err
                if elapsed < _THINKING_RETRY_COOLDOWN_SECONDS:
                    logger.warning(
                        "[%s] 响应仅含 thinking 内容，但近期有错误 (%.1fs ago)，跳过自动重试",
                        effective_key, elapsed,
                    )
                else:
                    retry_max_tokens = max(max_tokens * 3, 1024)
                    logger.info(
                        "[%s] 响应仅含 thinking 内容，自动重试 max_tokens=%d→%d",
                        effective_key, max_tokens, retry_max_tokens,
                    )
                    retry_kwargs = {
                        "model": model,
                        "max_tokens": retry_max_tokens,
                        "messages": [
                            {"role": "system", "content": "不要使用 <think> 标签，直接输出最终内容。"},
                            *messages,
                        ],
                    }
                    self._debug_log_request("complete/thinking_retry", effective_key, retry_kwargs)
                    retry_response = await self._tracked_call(
                        "openai",
                        self._openai_request_snapshot(
                            effective_key=effective_key, model=model,
                            request_kwargs=retry_kwargs,
                            origin=(origin or "") + "/thinking_retry",
                        ),
                        lambda: client.chat.completions.create(**retry_kwargs),
                    )
                    if retry_response.choices:
                        content = retry_response.choices[0].message.content or ""
                        stripped = strip_internal_thinking_tags(content).strip()
            return stripped

        _llm_start = time.monotonic()
        result = await self._with_routing_retry(
            purpose=auth_purpose,
            routing_key=effective_key if effective_key != auth_purpose else None,
            session_key=session_key,
            allow_failover=allow_failover,
            origin=origin,
            runner=_runner,
        )
        logger.debug("LLM call complete")
        return result

    async def query_lightweight(self, system: str, user: str, *, slot: str | None = None) -> str:
        """用轻量模型做简单任务（分类、提取、判断）。

        使用较低 max_tokens（1000），不需要 tool calling。
        slot 参数可指定具体 slot（如 "memory_processing"），不传则用默认路径。
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return await self.complete(
            messages,
            purpose="chat",
            max_tokens=1000,
            slot=slot,
            origin="query_lightweight",
        )

    async def simple_completion(self, prompt: str, purpose: str = "agent_execution", max_tokens: int = 2048) -> str:
        """简单文本补全，不走工具循环。Agent 内部逻辑使用。"""
        messages = [{"role": "user", "content": prompt}]
        return await self.complete(
            messages,
            purpose=purpose,
            max_tokens=max_tokens,
            origin="simple_completion",
        )

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict[str, Any]],
        purpose: str = "chat",
        max_tokens: int = 1024,
        *,
        slot: str | None = None,
        session_key: str | None = None,
        allow_failover: bool = True,
        origin: str | None = None,
    ) -> ToolTurnResult:
        """向模型发送支持工具的一轮请求，并统一返回 tool call 结构。"""
        effective_key = slot or purpose
        auth_purpose = _SLOT_TO_PURPOSE.get(effective_key, effective_key)

        async def _runner(candidate, client, model, api_type):
            if api_type == "codex_oauth":
                instructions, input_items = _convert_messages_to_responses_api(messages)
                payload: dict[str, Any] = {
                    "model": model,
                    "input": input_items,
                    "tools": _normalize_responses_api_tools(tools),
                    "instructions": instructions or _CODEX_DEFAULT_INSTRUCTIONS,
                    "store": False,
                    "stream": True,
                }
                self._inject_codex_params(payload, effective_key)
                payload = _sanitize_codex_payload(payload)
                text, output_items, _response_meta = await self._tracked_call(
                    "codex_oauth",
                    self._codex_request_snapshot(
                        effective_key=effective_key, model=model, payload=payload, origin=origin
                    ),
                    lambda: _collect_codex_stream(client, payload),
                )
                tool_calls, raw_tool_calls = _extract_responses_api_tool_calls(output_items)
                continuation_message = None
                if tool_calls:
                    # Responses API 格式：assistant message + function_call 项都要进入下轮 input
                    # assistant message 携带 phase（gpt-5.3-codex 需要）
                    assistant_items: list[dict[str, Any]] = [
                        item for item in output_items
                        if item.get("type") == "message" and item.get("role") == "assistant"
                    ]
                    continuation_message = {
                        "_codex_function_calls": assistant_items + raw_tool_calls,
                    }
                return ToolTurnResult(
                    text=text,
                    tool_calls=tool_calls,
                    continuation_message=continuation_message,
                )

            if api_type == "anthropic":
                system, anthropic_messages = _split_system_messages(messages)
                request_kwargs: dict[str, Any] = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": anthropic_messages,
                    "tools": _normalize_anthropic_tools(tools),
                    "tool_choice": {
                        "type": "auto",
                        "disable_parallel_tool_use": True,
                    },
                }
                if system is not None:
                    # Anthropic prefix cache: 标记 system prompt 为 ephemeral 缓存
                    request_kwargs["system"] = [
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]

                # 对最后一条 user 消息添加 cache marker（仅原生 Anthropic）
                base_url = self._base_urls.get(effective_key, "")
                if _is_native_anthropic(base_url):
                    _mark_last_user_message_cache(anthropic_messages)

                is_compat = not _is_native_anthropic(base_url)
                self._clamp_provider_params(request_kwargs, is_compat)

                response = await self._tracked_call(
                    "anthropic",
                    self._anthropic_request_snapshot(
                        effective_key=effective_key, model=model,
                        request_kwargs=request_kwargs, origin=origin,
                    ),
                    lambda: client.messages.create(**request_kwargs),
                )
                tool_calls = _extract_anthropic_tool_calls(response)
                text = _extract_anthropic_text(response)

                # thinking-only 保护：无 tool_calls 且无文本但有 thinking 时自动重试
                if not tool_calls and not text and _has_anthropic_thinking(response):
                    retry_max_tokens = max(max_tokens * 4, 2048)
                    logger.info(
                        "[%s] complete_with_tools: Anthropic 响应仅返回 thinking，"
                        "自动重试 max_tokens=%d→%d",
                        effective_key, max_tokens, retry_max_tokens,
                    )
                    retry_kwargs = dict(request_kwargs)
                    retry_kwargs["max_tokens"] = retry_max_tokens
                    response = await self._tracked_call(
                        "anthropic",
                        self._anthropic_request_snapshot(
                            effective_key=effective_key, model=model,
                            request_kwargs=retry_kwargs,
                            origin=(origin or "") + "/thinking_retry",
                        ),
                        lambda: client.messages.create(**retry_kwargs),
                    )
                    tool_calls = _extract_anthropic_tool_calls(response)
                    text = _extract_anthropic_text(response)

                if not tool_calls and getattr(response, "stop_reason", None) == "tool_use":
                    logger.warning(
                        "[%s] complete_with_tools: stop_reason=tool_use 但未提取到 tool calls, "
                        "content types: %s",
                        effective_key,
                        [getattr(b, "type", "?") for b in getattr(response, "content", []) or []],
                    )

                continuation_message = None
                if tool_calls:
                    continuation_message = {
                        "role": "assistant",
                        "content": list(getattr(response, "content", None) or []),
                    }

                input_tokens, output_tokens = _extract_usage(response)
                return ToolTurnResult(
                    text=text,
                    tool_calls=tool_calls,
                    continuation_message=continuation_message,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            request_kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": _normalize_openai_tools(tools),
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            }
            self._debug_log_request("complete_with_tools", effective_key, request_kwargs)
            response = await self._tracked_call(
                "openai",
                self._openai_request_snapshot(
                    effective_key=effective_key, model=model,
                    request_kwargs=request_kwargs, origin=origin,
                ),
                lambda: client.chat.completions.create(**request_kwargs),
            )
            if not response.choices:
                return ToolTurnResult(text="", tool_calls=[], continuation_message=None)
            message = response.choices[0].message
            tool_calls, raw_tool_calls = _extract_openai_tool_calls(message)
            raw_count = len(getattr(message, "tool_calls", None) or [])
            if raw_count > 0 and not tool_calls:
                logger.warning(
                    "[%s] complete_with_tools: %d 个 raw tool_calls 全部解析失败",
                    effective_key, raw_count,
                )
            continuation_message = None
            if tool_calls:
                continuation_message = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": raw_tool_calls,
                }

            input_tokens, output_tokens = _extract_usage(response)
            return ToolTurnResult(
                text=message.content or "",
                tool_calls=tool_calls,
                continuation_message=continuation_message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        _llm_start = time.monotonic()
        result = await self._with_routing_retry(
            purpose=auth_purpose,
            routing_key=effective_key if effective_key != auth_purpose else None,
            session_key=session_key,
            allow_failover=allow_failover,
            origin=origin,
            runner=_runner,
        )
        logger.debug("LLM tool call complete")
        return result

    async def complete_structured(
        self,
        messages: list[dict],
        *,
        result_schema: dict[str, Any],
        result_tool_name: str = "submit_result",
        result_tool_description: str = "提交结构化结果",
        purpose: str = "chat",
        slot: str | None = None,
        max_tokens: int = 1024,
        session_key: str | None = None,
        allow_failover: bool = True,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """用 forced tool call 获取结构化 JSON 输出。

        将 result_schema 包装为一个 tool，强制模型调用它，
        从 tool call arguments 中提取结构化数据。

        Args:
            messages: 对话消息列表
            result_schema: JSON Schema（OpenAI function parameters 格式）
            result_tool_name: 工具名称
            result_tool_description: 工具描述
            其余参数同 complete_with_tools

        Returns:
            解析后的 dict（tool call 的 arguments）

        Raises:
            ValueError: 模型未返回 tool call 或解析失败
        """
        effective_key = slot or purpose
        auth_purpose = _SLOT_TO_PURPOSE.get(effective_key, effective_key)

        tool_def = {
            "type": "function",
            "function": {
                "name": result_tool_name,
                "description": result_tool_description,
                "parameters": result_schema,
            },
        }

        return await self._complete_structured_inner(
            messages=messages,
            tool_def=tool_def,
            purpose=auth_purpose,
            routing_key=effective_key if effective_key != auth_purpose else None,
            max_tokens=max_tokens,
            session_key=session_key,
            allow_failover=allow_failover,
            origin=origin,
        )

    async def _complete_structured_inner(
        self,
        messages: list[dict],
        tool_def: dict[str, Any],
        purpose: str,
        max_tokens: int,
        *,
        routing_key: str | None = None,
        session_key: str | None = None,
        allow_failover: bool = True,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """内部实现：forced tool call 并提取 arguments。"""
        effective_key = routing_key or purpose

        async def _runner(candidate, client, model, api_type):
            tool_name = tool_def["function"]["name"]

            if api_type == "anthropic":
                system, anthropic_messages = _split_system_messages(messages)
                request_kwargs: dict[str, Any] = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": anthropic_messages,
                    "tools": _normalize_anthropic_tools([tool_def]),
                    "tool_choice": {"type": "tool", "name": tool_name},
                }
                if system is not None:
                    request_kwargs["system"] = system

                response = await self._tracked_call(
                    "anthropic",
                    self._anthropic_request_snapshot(
                        effective_key=effective_key, model=model,
                        request_kwargs=request_kwargs, origin=origin,
                    ),
                    lambda: client.messages.create(**request_kwargs),
                )
                tool_calls = _extract_anthropic_tool_calls(response)
                if not tool_calls:
                    raise ValueError("Anthropic 未返回 tool call")
                return tool_calls[0].arguments

            if api_type == "codex_oauth":
                # Responses API 不支持 forced tool_choice，提供 tool 但不强制
                instructions, input_items = _convert_messages_to_responses_api(messages)
                payload: dict[str, Any] = {
                    "model": model,
                    "input": input_items,
                    "tools": _normalize_responses_api_tools([tool_def]),
                    "instructions": instructions or _CODEX_DEFAULT_INSTRUCTIONS,
                    "store": False,
                    "stream": True,
                }
                self._inject_codex_params(payload, effective_key)
                payload = _sanitize_codex_payload(payload)
                text, output_items, _ = await self._tracked_call(
                    "codex_oauth",
                    self._codex_request_snapshot(
                        effective_key=effective_key, model=model, payload=payload, origin=origin
                    ),
                    lambda: _collect_codex_stream(client, payload),
                )
                tool_calls, _ = _extract_responses_api_tool_calls(output_items)
                if tool_calls:
                    return tool_calls[0].arguments
                if not text:
                    raise ValueError("Codex OAuth 未返回 tool call 且无文本输出")
                return _extract_json_from_text(text)

            # OpenAI-compatible (GLM, etc.)
            request_kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": _normalize_openai_tools([tool_def]),
                "tool_choice": {
                    "type": "function",
                    "function": {"name": tool_name},
                },
            }
            self._debug_log_request("complete_structured", effective_key, request_kwargs)
            response = await self._tracked_call(
                "openai",
                self._openai_request_snapshot(
                    effective_key=effective_key, model=model,
                    request_kwargs=request_kwargs, origin=origin,
                ),
                lambda: client.chat.completions.create(**request_kwargs),
            )

            if not response.choices:
                raise ValueError("OpenAI-compatible 未返回 choices")

            message = response.choices[0].message
            tool_calls, _ = _extract_openai_tool_calls(message)
            if tool_calls:
                return tool_calls[0].arguments

            # Fallback：部分 OpenAI-compatible 模型不支持 forced tool_choice
            text = _normalize_openai_message_content(message.content)
            if not text:
                raise ValueError("模型未返回 tool call 且无文本输出")

            return _extract_json_from_text(text)

        return await self._with_routing_retry(
            purpose=purpose,
            routing_key=routing_key,
            session_key=session_key,
            allow_failover=allow_failover,
            origin=origin,
            runner=_runner,
        )

    def build_tool_result_message(
        self,
        tool_results: list[tuple[ToolCallRequest, str]],
        *,
        purpose: str = "chat",
        slot: str | None = None,
        session_key: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """根据 provider 生成下一轮 continuation message。"""
        if not tool_results:
            raise ValueError("tool_results 不能为空")

        effective_key = slot or purpose
        effective_model = self._effective_model_for_purpose(
            effective_key,
            session_key=session_key,
        )
        base_url = self._base_urls.get(effective_key, LLM_BASE_URL)
        override_model = (
            self._session_model_overrides.get((session_key, effective_key))
            if session_key else None
        )
        if override_model and self._model_provider_map:
            override_provider = self._model_provider_map.get(override_model)
            if override_provider:
                api_type = override_provider.api_type
            else:
                api_type = _detect_api_type(base_url, effective_model)
        elif override_model:
            api_type = _detect_api_type(base_url, effective_model)
        else:
            api_type = self._api_types.get(effective_key, _detect_api_type(base_url, effective_model))

        if api_type == "anthropic":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": output,
                    }
                    for tool_call, output in tool_results
                ],
            }

        if api_type == "codex_oauth":
            # Responses API: function_call_output 项（无 role，直接进 input）
            items = [
                {
                    "type": "function_call_output",
                    "call_id": tool_call.id,
                    "output": output,
                }
                for tool_call, output in tool_results
            ]
            if len(items) == 1:
                return items[0]
            return items

        messages = [
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "content": output,
            }
            for tool_call, output in tool_results
        ]
        if len(messages) == 1:
            return messages[0]
        return messages


def _classify_provider_exception(exc: Exception) -> str:
    class_name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)

    if status_code in {401, 403} or "authentication" in class_name or "unauthorized" in message:
        return "auth"
    if (
        status_code in {429, 529}
        or "ratelimit" in class_name
        or "rate limit" in message
        or "stop reason: error" in message
        or "unhandled stop reason: error" in message
    ):
        return "rate_limit"
    if (
        status_code == 402
        or "insufficient credits" in message
        or "credit balance" in message
        or "billing" in message
        or "quota" in message
    ):
        return "billing"
    if "timeout" in class_name or "timed out" in message or "reason: error" in message:
        return "timeout"
    return "other"
