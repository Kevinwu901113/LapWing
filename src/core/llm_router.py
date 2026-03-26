"""LLM 路由器 - 按用途（purpose）选择对应的模型和 client。"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_CHAT_API_KEY,
    LLM_CHAT_BASE_URL,
    LLM_CHAT_MODEL,
    LLM_TOOL_API_KEY,
    LLM_TOOL_BASE_URL,
    LLM_TOOL_MODEL,
    NIM_API_KEY,
    NIM_BASE_URL,
    NIM_MODEL,
)

logger = logging.getLogger("lapwing.llm_router")

# purpose -> (api_key, base_url, model) 的映射配置
_PURPOSE_ENV: dict[str, tuple[str, str, str]] = {
    "chat": (LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL),
    "tool": (LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL),
    "heartbeat": (NIM_API_KEY, NIM_BASE_URL, NIM_MODEL),
}

_MINIMAX_MAX_COMPLETION_TOKENS = 2048
_MINIMAX_DEFAULT_TEMPERATURE = 1.0
_MINIMAX_DEFAULT_TOP_P = 0.95


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


def _detect_api_type(base_url: str) -> str:
    """根据 base_url 判断当前 provider 走哪种兼容协议。"""
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

    def __init__(self) -> None:
        self._clients: dict[str, AsyncOpenAI | AsyncAnthropic] = {}
        self._models: dict[str, str] = {}
        self._api_types: dict[str, str] = {}
        self._base_urls: dict[str, str] = {}
        self._setup_clients()

    def _setup_clients(self) -> None:
        """根据配置初始化各 purpose 的 client，未配置时回退到通用 LLM_*。"""
        if not LLM_API_KEY or not LLM_BASE_URL or not LLM_MODEL:
            raise ValueError(
                "LLM 通用配置不完整，请检查 config/.env 中的 "
                "LLM_API_KEY、LLM_BASE_URL、LLM_MODEL"
            )

        for purpose, (api_key, base_url, model) in _PURPOSE_ENV.items():
            if api_key and base_url and model:
                resolved_api_key = api_key
                resolved_base_url = base_url
                resolved_model = model
                source = "专用"
            else:
                resolved_api_key = LLM_API_KEY
                resolved_base_url = LLM_BASE_URL
                resolved_model = LLM_MODEL
                source = "通用回退"

            api_type = _detect_api_type(resolved_base_url)
            if api_type == "anthropic":
                client = AsyncAnthropic(
                    api_key=resolved_api_key,
                    base_url=_normalize_anthropic_base_url(resolved_base_url),
                )
            else:
                client = AsyncOpenAI(
                    api_key=resolved_api_key,
                    base_url=resolved_base_url,
                )

            self._clients[purpose] = client
            self._models[purpose] = resolved_model
            self._api_types[purpose] = api_type
            self._base_urls[purpose] = resolved_base_url
            logger.info(
                f"[{purpose}] 使用{source}模型: "
                f"{resolved_model} ({resolved_base_url}, {api_type})"
            )

    def _resolve_client(self, purpose: str) -> tuple[AsyncOpenAI | AsyncAnthropic, str, str]:
        client = self._clients.get(purpose)
        if client is None:
            logger.warning(f"[{purpose}] 未知的 purpose，回退到 chat 模型")
            return (
                self._clients["chat"],
                self._models.get("chat", LLM_MODEL),
                self._api_types.get("chat", _detect_api_type(LLM_BASE_URL)),
            )

        return (
            client,
            self._models.get(purpose, LLM_MODEL),
            self._api_types.get(purpose, _detect_api_type(LLM_BASE_URL)),
        )

    def _is_minimax_openai(self, purpose: str) -> bool:
        api_type = self._api_types.get(purpose, _detect_api_type(LLM_BASE_URL))
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
            normalized["messages"] = _normalize_openai_messages_for_text_only(
                [message for message in messages if isinstance(message, dict)]
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

    def model_for(self, purpose: str) -> str:
        """返回指定 purpose 实际使用的模型名。"""
        return self._models.get(purpose, LLM_MODEL)

    async def complete(
        self,
        messages: list[dict],
        purpose: str = "chat",
        max_tokens: int = 1024,
    ) -> str:
        """向对应 purpose 的模型发送请求，返回回复文本。"""
        client, model, api_type = self._resolve_client(purpose)

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
        return response.choices[0].message.content or ""

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict[str, Any]],
        purpose: str = "chat",
        max_tokens: int = 1024,
    ) -> ToolTurnResult:
        """向模型发送支持工具的一轮请求，并统一返回 tool call 结构。"""
        client, model, api_type = self._resolve_client(purpose)

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

    def build_tool_result_message(
        self,
        purpose: str,
        tool_results: list[tuple[ToolCallRequest, str]],
    ) -> dict[str, Any]:
        """根据 provider 生成下一轮 continuation message。"""
        if not tool_results:
            raise ValueError("tool_results 不能为空")

        _, _, api_type = self._resolve_client(purpose)

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

        tool_call, output = tool_results[0]
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": tool_call.name,
            "content": output,
        }
