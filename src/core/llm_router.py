"""LLM 路由器 - 按用途（purpose）选择对应的模型和 client。"""

import logging
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from config.settings import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL,
    LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL,
    NIM_API_KEY, NIM_BASE_URL, NIM_MODEL,
)

logger = logging.getLogger("lapwing.llm_router")

# purpose -> (api_key, base_url, model) 的映射配置
_PURPOSE_ENV: dict[str, tuple[str, str, str]] = {
    "chat": (LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL),
    "tool": (LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL),
    "heartbeat": (NIM_API_KEY, NIM_BASE_URL, NIM_MODEL),
}


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
        anthropic_messages.append({
            "role": anthropic_role,
            "content": content if content is not None else "",
        })

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


class LLMRouter:
    """按 purpose 路由到对应 LLM client。

    用法：
        router = LLMRouter()
        reply = await router.complete(messages, purpose="chat")
    """

    def __init__(self) -> None:
        self._clients: dict[str, AsyncOpenAI | AsyncAnthropic] = {}
        self._models: dict[str, str] = {}
        self._api_types: dict[str, str] = {}
        self._setup_clients()

    def _setup_clients(self) -> None:
        """根据配置初始化各 purpose 的 client，未配置时回退到通用 LLM_*。"""
        # 校验通用配置（所有 purpose 的最终回退）
        if not LLM_API_KEY or not LLM_BASE_URL or not LLM_MODEL:
            raise ValueError(
                "LLM 通用配置不完整，请检查 config/.env 中的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL"
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
                client = AsyncOpenAI(api_key=resolved_api_key, base_url=resolved_base_url)

            self._clients[purpose] = client
            self._models[purpose] = resolved_model
            self._api_types[purpose] = api_type
            logger.info(f"[{purpose}] 使用{source}模型: {resolved_model} ({resolved_base_url}, {api_type})")

    def model_for(self, purpose: str) -> str:
        """返回指定 purpose 实际使用的模型名。"""
        return self._models.get(purpose, LLM_MODEL)

    async def complete(
        self,
        messages: list[dict],
        purpose: str = "chat",
        max_tokens: int = 1024,
    ) -> str:
        """向对应 purpose 的模型发送请求，返回回复文本。

        Args:
            messages: OpenAI 格式的消息列表
            purpose: 用途标识（"chat" 或 "tool"），未知值回退到 chat 模型
            max_tokens: 最大生成 token 数

        Returns:
            模型回复的文本内容

        Raises:
            Exception: LLM API 调用失败时向上抛出，由调用方处理
        """
        client = self._clients.get(purpose)
        if client is None:
            logger.warning(f"[{purpose}] 未知的 purpose，回退到 chat 模型")
            client = self._clients["chat"]
            model = self._models.get("chat", LLM_MODEL)
            api_type = self._api_types.get("chat", _detect_api_type(LLM_BASE_URL))
        else:
            model = self._models.get(purpose, LLM_MODEL)
            api_type = self._api_types.get(purpose, _detect_api_type(LLM_BASE_URL))

        if api_type == "anthropic":
            system, anthropic_messages = _split_system_messages(messages)
            request_kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": anthropic_messages,
            }
            if system is not None:
                request_kwargs["system"] = system

            response = await client.messages.create(
                **request_kwargs,
            )
            text = _extract_anthropic_text(response)
            if text:
                return text

            if _has_anthropic_thinking(response) and getattr(response, "stop_reason", None) == "max_tokens":
                retry_max_tokens = max(max_tokens * 4, 256)
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

        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content or ""
