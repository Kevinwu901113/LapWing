"""LLM 路由器 - 按用途（purpose）选择对应的模型和 client。"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.auth.service import AuthManager
from src.core.openai_codex_runtime import OpenAICodexRuntime
from config.settings import (
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_MODEL_ALLOWLIST,
    LLM_CHAT_BASE_URL,
    LLM_CHAT_MODEL,
    LLM_TOOL_BASE_URL,
    LLM_TOOL_MODEL,
    MINIMAX_MAX_COMPLETION_TOKENS,
    NIM_BASE_URL,
    NIM_MODEL,
)

logger = logging.getLogger("lapwing.llm_router")

_RECOVERABLE_FAILURES = {"auth", "rate_limit", "timeout", "billing"}

_MINIMAX_MAX_COMPLETION_TOKENS = MINIMAX_MAX_COMPLETION_TOKENS
_MINIMAX_DEFAULT_TEMPERATURE = 1.0
_MINIMAX_DEFAULT_TOP_P = 0.95
_MODEL_PURPOSES: tuple[str, ...] = ("chat", "tool", "heartbeat")


@dataclass
class ToolCallRequest:
    """统一后的工具调用请求。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolTurnResult:
    """带工具能力的一轮模型响应。"""

    text: str
    tool_calls: list[ToolCallRequest]
    continuation_message: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelOption:
    index: int
    ref: str
    alias: str | None = None


def _extract_openai_codex_model(model: str) -> str | None:
    normalized = str(model or "").strip()
    if not normalized:
        return None
    prefix = "openai-codex/"
    if not normalized.lower().startswith(prefix):
        return None
    resolved = normalized[len(prefix):].strip()
    return resolved or None


def _detect_api_type(base_url: str, model: str | None = None) -> str:
    """根据 base_url 判断当前 provider 走哪种兼容协议。"""
    if _extract_openai_codex_model(model or "") is not None:
        return "openai_codex"
    return "anthropic" if "/anthropic" in base_url.lower() else "openai"


def _normalize_anthropic_base_url(base_url: str) -> str:
    """Anthropic SDK 期望的 base_url 不包含 /v1。"""
    normalized = base_url.rstrip("/")
    if normalized.lower().endswith("/v1"):
        return normalized[:-3]
    return normalized


def _split_system_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """将 OpenAI 风格消息拆成 Anthropic 需要的 system + messages。"""
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        if role == "system":
            if content:
                system_parts.append(str(content))
            continue

        anthropic_role = role if role in {"user", "assistant"} else "user"
        anthropic_messages.append(
            {
                "role": anthropic_role,
                "content": content if content is not None else "",
            }
        )

    system = "\n\n".join(system_parts).strip() or None
    return system, anthropic_messages


def _extract_anthropic_text(response: Any) -> str:
    """从 Anthropic 响应中提取最终文本。"""
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content or []:
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            parts.append(block.text)
    return "".join(parts)


def _has_anthropic_thinking(response: Any) -> bool:
    """判断响应中是否包含 thinking block。"""
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return False
    return any(getattr(block, "type", None) == "thinking" for block in content or [])


def _safe_parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _extract_json_from_text(text: str) -> dict[str, Any]:
    """Fallback：从 LLM 自由文本中提取 JSON。

    处理 <think> 块、markdown code fence、多余前缀等干扰。
    用于 MiniMax 等不支持 forced tool_choice 的模型。

    Raises:
        ValueError: 所有解析尝试均失败
    """
    import re as _re

    # 1. 剥离 <think>...</think> 推理块
    cleaned = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
    # 2. 剥离 markdown code fence
    cleaned = _re.sub(r"^```(?:json)?\s*", "", cleaned, flags=_re.MULTILINE)
    cleaned = _re.sub(r"\s*```$", "", cleaned, flags=_re.MULTILINE).strip()
    # 3. 直接尝试 json.loads（最理想情况）
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            logger.debug("_extract_json_from_text: 直接解析成功")
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # 4. 正则提取第一个 JSON object（处理前后有多余文字的情况）
    json_match = _re.search(r"\{.*\}", cleaned, _re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if isinstance(data, dict):
                logger.debug("_extract_json_from_text: 正则提取成功")
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(f"_extract_json_from_text 解析失败: {text[:200]}")


def _is_minimax_base_url(base_url: str) -> bool:
    return "minimax" in base_url.lower()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_openai_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
                continue

            if not isinstance(part, dict):
                continue

            part_type = str(part.get("type", "")).lower()
            if part_type in {"text", "input_text", "output_text"}:
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
                elif isinstance(text, dict):
                    nested = text.get("value") or text.get("content")
                    if isinstance(nested, str):
                        text_parts.append(nested)
            else:
                # OpenAI 兼容里会混入 image/audio part，这里只保留纯文本。
                fallback_text = part.get("text")
                if isinstance(fallback_text, str):
                    text_parts.append(fallback_text)
        return "\n".join(part for part in text_parts if part)

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False)

    return str(content)


def _normalize_openai_messages_for_text_only(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_messages: list[dict[str, Any]] = []
    for message in messages:
        normalized_message = dict(message)
        normalized_message["content"] = _normalize_openai_message_content(
            message.get("content", "")
        )
        normalized_messages.append(normalized_message)
    return normalized_messages


def _merge_messages_for_minimax(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MiniMax 不接受多个 system 消息或连续同 role 消息，在此合并。

    处理顺序：
    1. 提取所有 system 消息，内容合并为一条放在最前
    2. 合并剩余消息中连续同 role 的相邻条目
       — 但 tool 相关消息（role=tool、带 tool_calls 的 assistant）不参与合并，
         且保留 tool_calls / tool_call_id / name 等字段，否则 MiniMax 会返回
         "tool result's tool id() not found" 400 错误。
    """
    system_parts: list[str] = []
    non_system: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            content = str(msg.get("content") or "")
            if content:
                system_parts.append(content)
        else:
            # 保留完整字段（tool_calls, tool_call_id, name 等）
            non_system.append(dict(msg))

    def _is_tool_related(msg: dict[str, Any]) -> bool:
        """判断消息是否属于工具调用链，不能被合并。"""
        return msg.get("role") == "tool" or bool(msg.get("tool_calls"))

    # 合并连续同 role 的消息，但跳过 tool 相关消息
    merged: list[dict[str, Any]] = []
    for msg in non_system:
        if _is_tool_related(msg):
            merged.append(msg)
        elif (
            merged
            and merged[-1]["role"] == msg["role"]
            and not _is_tool_related(merged[-1])
        ):
            merged[-1]["content"] = str(merged[-1].get("content") or "") + "\n\n" + str(msg.get("content") or "")
        else:
            merged.append(dict(msg))

    if system_parts:
        return [{"role": "system", "content": "\n\n".join(system_parts)}] + merged
    return merged


def _normalize_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留 OpenAI 兼容格式的 function tools。"""
    return [tool for tool in tools if tool.get("type") == "function"]


def _normalize_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 OpenAI function schema 转成 Anthropic 的 tools 格式。"""
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue

        function = tool.get("function") or {}
        normalized.append(
            {
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters") or {
                    "type": "object",
                    "properties": {},
                },
            }
        )
    return normalized


def _extract_openai_tool_calls(message: Any) -> tuple[list[ToolCallRequest], list[dict[str, Any]]]:
    tool_calls: list[ToolCallRequest] = []
    raw_tool_calls: list[dict[str, Any]] = []

    for index, tool_call in enumerate(getattr(message, "tool_calls", None) or []):
        function = getattr(tool_call, "function", None)
        name = getattr(function, "name", "") or ""
        arguments_raw = getattr(function, "arguments", "") or ""
        tool_id = getattr(tool_call, "id", None) or f"call_{index}"

        tool_calls.append(
            ToolCallRequest(
                id=tool_id,
                name=name,
                arguments=_safe_parse_json(arguments_raw),
            )
        )
        raw_tool_calls.append(
            {
                "id": tool_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments_raw,
                },
            }
        )

    return tool_calls, raw_tool_calls


def _extract_anthropic_tool_calls(response: Any) -> list[ToolCallRequest]:
    tool_calls: list[ToolCallRequest] = []
    for index, block in enumerate(getattr(response, "content", None) or []):
        if getattr(block, "type", None) != "tool_use":
            continue

        tool_calls.append(
            ToolCallRequest(
                id=getattr(block, "id", None) or f"toolu_{index}",
                name=getattr(block, "name", "") or "",
                arguments=dict(getattr(block, "input", None) or {}),
            )
        )
    return tool_calls


class LLMRouter:
    """按 purpose 路由到对应 LLM client。"""

    def __init__(self, auth_manager: AuthManager | None = None) -> None:
        self._auth_manager = auth_manager or AuthManager()
        self._codex_runtime = OpenAICodexRuntime()
        self._clients: dict[str, Any] = {}
        self._models: dict[str, str] = {}
        self._api_types: dict[str, str] = {}
        self._base_urls: dict[str, str] = {}
        self._session_model_overrides: dict[tuple[str, str], str] = {}
        self._model_options: list[ModelOption] = []
        self._model_options_by_ref: dict[str, ModelOption] = {}
        self._model_options_by_alias: dict[str, ModelOption] = {}
        self._setup_clients()
        self._setup_model_options()

    def _setup_clients(self) -> None:
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

        for alias, ref in LLM_MODEL_ALLOWLIST:
            normalized_ref = str(ref or "").strip()
            if not normalized_ref or normalized_ref in options_by_ref:
                continue

            normalized_alias = str(alias or "").strip() or None
            option = ModelOption(
                index=len(options) + 1,
                ref=normalized_ref,
                alias=normalized_alias,
            )
            options.append(option)
            options_by_ref[normalized_ref] = option
            if normalized_alias:
                options_by_alias.setdefault(normalized_alias.lower(), option)

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
            raise ValueError("当前没有可用模型，请先配置 LLM_MODEL_ALLOWLIST。")

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

    def _codex_compatibility_error(
        self,
        *,
        purpose: str,
        session_key: str | None,
    ) -> str | None:
        try:
            candidates = self._auth_manager.resolve_candidates(
                purpose=purpose,
                session_key=session_key,
                allow_failover=False,
                origin="model.switch.compatibility",
            )
        except Exception:
            return "缺少 openai oauth profile"

        if not candidates:
            return "缺少 openai oauth profile"

        candidate = candidates[0]
        provider = str(getattr(candidate, "provider", "") or "")
        profile_type = str(getattr(candidate, "profile_type", "") or "")
        access_token = str(getattr(candidate, "auth_value", "") or "").strip()
        if provider == "openai" and profile_type == "oauth" and access_token:
            return None
        return "缺少 openai oauth profile"

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
        skipped: dict[str, str] = {}

        for purpose in _MODEL_PURPOSES:
            compatibility_error: str | None = None
            if _extract_openai_codex_model(option.ref) is not None:
                compatibility_error = self._codex_compatibility_error(
                    purpose=purpose,
                    session_key=session_key,
                )
            if compatibility_error is not None:
                skipped[purpose] = compatibility_error
                continue

            self._session_model_overrides[(session_key, purpose)] = option.ref
            applied[purpose] = option.ref

        return {
            "selected": {"index": option.index, "alias": option.alias, "ref": option.ref},
            "applied": applied,
            "skipped": skipped,
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
            api_type = _detect_api_type(base_url, effective_model)
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
        purpose: str,
        auth_value: str | None = None,
        *,
        model_override: str | None = None,
    ) -> tuple[Any, str, str]:
        client_override = self._clients.get(purpose)
        model = model_override or self._models.get(purpose, LLM_MODEL)
        base_url = self._base_urls.get(purpose, LLM_BASE_URL)
        if model_override is None:
            api_type = self._api_types.get(purpose, _detect_api_type(base_url, model))
        else:
            api_type = _detect_api_type(base_url, model)

        if client_override is not None:
            return client_override, model, api_type

        if not auth_value:
            raise ValueError(f"[{purpose}] 当前请求没有可用 credential。")

        if api_type == "openai_codex":
            raise RuntimeError("openai-codex 模型不应走 OpenAI SDK client 分支。")

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

    def _is_minimax_openai(self, purpose: str) -> bool:
        api_type = self._api_types.get(
            purpose,
            _detect_api_type(LLM_BASE_URL, self._models.get(purpose, LLM_MODEL)),
        )
        base_url = self._base_urls.get(purpose, LLM_BASE_URL)
        return api_type == "openai" and _is_minimax_base_url(base_url)

    def _normalize_minimax_openai_request(
        self,
        purpose: str,
        request_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._is_minimax_openai(purpose):
            return request_kwargs

        normalized = dict(request_kwargs)

        messages = normalized.get("messages")
        if isinstance(messages, list):
            normalized["messages"] = _merge_messages_for_minimax(
                _normalize_openai_messages_for_text_only(
                    [message for message in messages if isinstance(message, dict)]
                )
            )

        max_completion_tokens = normalized.get("max_completion_tokens")
        if max_completion_tokens is None and "max_tokens" in normalized:
            max_completion_tokens = normalized.pop("max_tokens")
        parsed_max_tokens = _safe_int(max_completion_tokens)
        if parsed_max_tokens is None:
            parsed_max_tokens = 1024
        normalized["max_completion_tokens"] = max(
            1,
            min(_MINIMAX_MAX_COMPLETION_TOKENS, parsed_max_tokens),
        )

        temperature = _safe_float(normalized.get("temperature"))
        if temperature is None or temperature <= 0 or temperature > 1:
            normalized["temperature"] = _MINIMAX_DEFAULT_TEMPERATURE
        else:
            normalized["temperature"] = temperature

        if "top_p" in normalized:
            top_p = _safe_float(normalized.get("top_p"))
            normalized["top_p"] = (
                top_p
                if top_p is not None and 0 < top_p <= 1
                else _MINIMAX_DEFAULT_TOP_P
            )

        normalized["n"] = 1
        normalized.pop("function_call", None)
        normalized.pop("parallel_tool_calls", None)
        normalized.pop("tool_choice", None)  # MiniMax 不支持 tool_choice 参数
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[%s] MiniMax OpenAI 参数规范化: "
                "max_completion_tokens=%s temperature=%s top_p=%s n=%s has_tools=%s",
                purpose,
                normalized.get("max_completion_tokens"),
                normalized.get("temperature"),
                normalized.get("top_p"),
                normalized.get("n"),
                bool(normalized.get("tools")),
            )
        return normalized

    def model_for(self, purpose: str, *, session_key: str | None = None) -> str:
        """返回指定 purpose 实际使用的模型名。"""
        return self._effective_model_for_purpose(purpose, session_key=session_key)

    def _ensure_openai_codex_candidate(self, candidate: Any, *, purpose: str) -> str | None:
        provider = str(getattr(candidate, "provider", "") or "")
        profile_type = str(getattr(candidate, "profile_type", "") or "")
        access_token = str(getattr(candidate, "auth_value", "") or "").strip()
        metadata = dict(getattr(candidate, "metadata", {}) or {})
        account_id = str(metadata.get("accountId") or "").strip() or None

        if provider != "openai" or profile_type != "oauth" or not access_token:
            raise PermissionError(
                (
                    f"[{purpose}] unauthorized: `openai-codex/*` 仅支持已绑定的 "
                    "OpenAI OAuth profile（provider=openai, type=oauth）。"
                )
            )
        return account_id

    async def _with_routing_retry(
        self,
        *,
        purpose: str,
        session_key: str | None,
        allow_failover: bool,
        origin: str | None,
        runner: Callable[[Any, Any, str, str], Awaitable[Any]],
    ) -> Any:
        excluded_profiles: set[str] = set()
        last_exc: Exception | None = None
        session_model_override = (
            self._session_model_overrides.get((session_key, purpose))
            if session_key
            else None
        )
        client_override = self._clients.get(purpose)

        while True:
            candidates = self._auth_manager.resolve_candidates(
                purpose=purpose,
                session_key=session_key,
                allow_failover=allow_failover,
                exclude_profiles=excluded_profiles,
                origin=origin,
            )
            if not candidates:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(f"[{purpose}] 没有可用的 auth candidate。")

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
                            model_for_attempt = self._models.get(purpose, LLM_MODEL)
                            use_model_override = False

                        resolved_codex_model = _extract_openai_codex_model(model_for_attempt)
                        if resolved_codex_model is not None:
                            client = None
                            model = resolved_codex_model
                            api_type = "openai_codex"
                        else:
                            client, model, api_type = self._resolve_client(
                                purpose,
                                auth_value=current_candidate.auth_value,
                                model_override=model_for_attempt if use_model_override else None,
                            )
                        result = await runner(current_candidate, client, model, api_type)
                        self._auth_manager.mark_success(current_candidate)
                        return result
                    except Exception as exc:
                        failure_kind = _classify_provider_exception(exc)
                        last_exc = exc
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
                        raise

            if last_exc is not None:
                raise last_exc

    async def complete(
        self,
        messages: list[dict],
        purpose: str = "chat",
        max_tokens: int = 1024,
        *,
        session_key: str | None = None,
        allow_failover: bool = True,
        origin: str | None = None,
    ) -> str:
        """向对应 purpose 的模型发送请求，返回回复文本。"""
        async def _runner(candidate, client, model, api_type):
            if api_type == "openai_codex":
                account_id = self._ensure_openai_codex_candidate(candidate, purpose=purpose)
                turn = await self._codex_runtime.complete(
                    model=model,
                    messages=messages,
                    tools=[],
                    access_token=candidate.auth_value,
                    account_id=account_id,
                )
                return turn.text

            if api_type == "anthropic":
                system, anthropic_messages = _split_system_messages(messages)
                request_kwargs: dict[str, Any] = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": anthropic_messages,
                }
                if system is not None:
                    request_kwargs["system"] = system

                response = await client.messages.create(**request_kwargs)
                text = _extract_anthropic_text(response)
                if text:
                    return text

                if not text and _has_anthropic_thinking(response):
                    retry_max_tokens = max(max_tokens * 4, 512)
                    if retry_max_tokens > max_tokens:
                        logger.info(
                            f"[{purpose}] Anthropic 响应仅返回 thinking，"
                            f"自动重试并提升 max_tokens 到 {retry_max_tokens}"
                        )
                        retry_kwargs = dict(request_kwargs)
                        retry_kwargs["max_tokens"] = retry_max_tokens
                        retry_response = await client.messages.create(**retry_kwargs)
                        return _extract_anthropic_text(retry_response)

                return text

            request_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            request_kwargs = self._normalize_minimax_openai_request(purpose, request_kwargs)
            response = await client.chat.completions.create(**request_kwargs)
            if not response.choices:
                return ""
            return response.choices[0].message.content or ""

        return await self._with_routing_retry(
            purpose=purpose,
            session_key=session_key,
            allow_failover=allow_failover,
            origin=origin,
            runner=_runner,
        )

    async def query_lightweight(self, system: str, user: str) -> str:
        """用轻量模型做简单任务（分类、提取、判断）。

        使用较低 max_tokens（1000），不需要 tool calling。
        路由到 chat purpose（通常是最快的模型），温度由底层模型路由决定。
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return await self.complete(
            messages,
            purpose="chat",
            max_tokens=1000,
            origin="query_lightweight",
        )

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict[str, Any]],
        purpose: str = "chat",
        max_tokens: int = 1024,
        *,
        session_key: str | None = None,
        allow_failover: bool = True,
        origin: str | None = None,
    ) -> ToolTurnResult:
        """向模型发送支持工具的一轮请求，并统一返回 tool call 结构。"""
        async def _runner(candidate, client, model, api_type):
            if api_type == "openai_codex":
                account_id = self._ensure_openai_codex_candidate(candidate, purpose=purpose)
                turn = await self._codex_runtime.complete(
                    model=model,
                    messages=messages,
                    tools=tools,
                    access_token=candidate.auth_value,
                    account_id=account_id,
                )
                tool_calls = [
                    ToolCallRequest(
                        id=item.id,
                        name=item.name,
                        arguments=item.arguments,
                    )
                    for item in turn.tool_calls
                ]
                continuation_message = None
                if tool_calls:
                    continuation_message = {
                        "role": "assistant",
                        "content": turn.text,
                        "tool_calls": [
                            {
                                "id": item.id,
                                "type": "function",
                                "function": {
                                    "name": item.name,
                                    "arguments": item.raw_arguments,
                                },
                            }
                            for item in turn.tool_calls
                        ],
                    }
                return ToolTurnResult(
                    text=turn.text,
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
                    request_kwargs["system"] = system

                response = await client.messages.create(**request_kwargs)
                tool_calls = _extract_anthropic_tool_calls(response)
                continuation_message = None
                if tool_calls:
                    continuation_message = {
                        "role": "assistant",
                        "content": list(getattr(response, "content", None) or []),
                    }

                return ToolTurnResult(
                    text=_extract_anthropic_text(response),
                    tool_calls=tool_calls,
                    continuation_message=continuation_message,
                )

            request_kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": _normalize_openai_tools(tools),
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            }
            request_kwargs = self._normalize_minimax_openai_request(purpose, request_kwargs)
            response = await client.chat.completions.create(**request_kwargs)
            if not response.choices:
                return ToolTurnResult(text="", tool_calls=[], continuation_message=None)
            message = response.choices[0].message
            tool_calls, raw_tool_calls = _extract_openai_tool_calls(message)
            continuation_message = None
            if tool_calls:
                continuation_message = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": raw_tool_calls,
                }

            return ToolTurnResult(
                text=message.content or "",
                tool_calls=tool_calls,
                continuation_message=continuation_message,
            )

        return await self._with_routing_retry(
            purpose=purpose,
            session_key=session_key,
            allow_failover=allow_failover,
            origin=origin,
            runner=_runner,
        )

    async def complete_structured(
        self,
        messages: list[dict],
        *,
        result_schema: dict[str, Any],
        result_tool_name: str = "submit_result",
        result_tool_description: str = "提交结构化结果",
        purpose: str = "chat",
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
            purpose=purpose,
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
        session_key: str | None = None,
        allow_failover: bool = True,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """内部实现：forced tool call 并提取 arguments。"""

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

                response = await client.messages.create(**request_kwargs)
                tool_calls = _extract_anthropic_tool_calls(response)
                if not tool_calls:
                    raise ValueError("Anthropic 未返回 tool call")
                return tool_calls[0].arguments

            if api_type == "openai_codex":
                account_id = self._ensure_openai_codex_candidate(candidate, purpose=purpose)
                turn = await self._codex_runtime.complete(
                    model=model,
                    messages=messages,
                    tools=[tool_def],
                    access_token=candidate.auth_value,
                    account_id=account_id,
                )
                if not turn.tool_calls:
                    raise ValueError("Codex 未返回 tool call")
                return turn.tool_calls[0].arguments

            # OpenAI-compatible (MiniMax, GLM, etc.)
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
            request_kwargs = self._normalize_minimax_openai_request(purpose, request_kwargs)
            response = await client.chat.completions.create(**request_kwargs)

            if not response.choices:
                raise ValueError("OpenAI-compatible 未返回 choices")

            message = response.choices[0].message
            tool_calls, _ = _extract_openai_tool_calls(message)
            if tool_calls:
                return tool_calls[0].arguments

            # Fallback：MiniMax 等不支持 forced tool_choice 的模型
            text = _normalize_openai_message_content(message.content)
            if not text:
                raise ValueError("模型未返回 tool call 且无文本输出")

            return _extract_json_from_text(text)

        return await self._with_routing_retry(
            purpose=purpose,
            session_key=session_key,
            allow_failover=allow_failover,
            origin=origin,
            runner=_runner,
        )

    def build_tool_result_message(
        self,
        purpose: str,
        tool_results: list[tuple[ToolCallRequest, str]],
        *,
        session_key: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """根据 provider 生成下一轮 continuation message。"""
        if not tool_results:
            raise ValueError("tool_results 不能为空")

        effective_model = self._effective_model_for_purpose(
            purpose,
            session_key=session_key,
        )
        base_url = self._base_urls.get(purpose, LLM_BASE_URL)
        if session_key and self._session_model_overrides.get((session_key, purpose)):
            api_type = _detect_api_type(base_url, effective_model)
        else:
            api_type = self._api_types.get(purpose, _detect_api_type(base_url, effective_model))

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
        status_code == 429
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