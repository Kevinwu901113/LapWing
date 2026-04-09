"""LLM 协议适配函数：OpenAI/Anthropic 消息格式转换、工具调用提取。"""

import json
import logging
from typing import Any

from src.core.llm_types import ToolCallRequest

logger = logging.getLogger("lapwing.core.llm_protocols")


def _detect_api_type(base_url: str, model: str | None = None) -> str:
    """根据 base_url 判断当前 provider 走哪种兼容协议。"""
    return "anthropic" if "/anthropic" in base_url.lower() else "openai"


def _is_native_anthropic(base_url: str) -> bool:
    """仅原生 Anthropic API（非 MiniMax 等代理 provider）。"""
    return "api.anthropic.com" in base_url.lower()


def _mark_last_user_message_cache(messages: list[dict]) -> None:
    """对最后一条 user 消息添加 cache_control（用于 Anthropic prefix cache）。"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = [{
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }]
            elif isinstance(content, list) and content and isinstance(content[-1], dict):
                content[-1]["cache_control"] = {"type": "ephemeral"}
            break


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
    cleaned = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    cleaned = _re.sub(r"<think>.*$", "", cleaned, flags=_re.DOTALL)
    cleaned = cleaned.strip()
    # 2. 剥离 markdown code fence
    cleaned = _re.sub(r"^```(?:json)?\s*", "", cleaned, flags=_re.MULTILINE)
    cleaned = _re.sub(r"\s*```$", "", cleaned, flags=_re.MULTILINE).strip()
    # 3. 直接尝试 json.loads
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            logger.debug("_extract_json_from_text: 直接解析成功")
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # 4. 正则提取第一个 JSON object
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
