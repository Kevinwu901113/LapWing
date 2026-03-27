from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from config.settings import (
    OPENAI_CODEX_RUNTIME_BASE_URL,
    OPENAI_CODEX_RUNTIME_CLIENT_VERSION,
    OPENAI_CODEX_RUNTIME_PROXY_URL,
    OPENAI_CODEX_RUNTIME_TIMEOUT_SECONDS,
)

_DEFAULT_INSTRUCTIONS = "You are a helpful assistant."


@dataclass(frozen=True)
class CodexRuntimeToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass(frozen=True)
class CodexRuntimeTurnResult:
    text: str
    tool_calls: list[CodexRuntimeToolCall]


class OpenAICodexRuntimeError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenAICodexRuntime:
    def __init__(
        self,
        *,
        base_url: str = OPENAI_CODEX_RUNTIME_BASE_URL,
        proxy_url: str = OPENAI_CODEX_RUNTIME_PROXY_URL,
        client_version: str = OPENAI_CODEX_RUNTIME_CLIENT_VERSION,
        timeout_seconds: int = OPENAI_CODEX_RUNTIME_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._proxy_url = proxy_url.strip()
        self._client_version = client_version.strip()
        self._timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        access_token: str,
        account_id: str | None = None,
    ) -> CodexRuntimeTurnResult:
        if not access_token.strip():
            raise OpenAICodexRuntimeError("OpenAI Codex runtime 缺少 OAuth access token。")

        instructions, input_items = _map_messages_to_codex_input(messages)
        payload = {
            "model": model,
            "instructions": instructions,
            "input": input_items,
            "tools": _map_tools(tools),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "reasoning": None,
            "store": False,
            "stream": True,
            "include": [],
        }
        return await self._stream_responses(
            payload=payload,
            access_token=access_token,
            account_id=account_id,
        )

    async def _stream_responses(
        self,
        *,
        payload: dict[str, Any],
        access_token: str,
        account_id: str | None,
    ) -> CodexRuntimeTurnResult:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        normalized_account_id = str(account_id or "").strip()
        if normalized_account_id:
            headers["ChatGPT-Account-ID"] = normalized_account_id

        url = f"{self._base_url}/responses"
        params: dict[str, str] = {}
        if self._client_version:
            params["client_version"] = self._client_version

        client_kwargs: dict[str, Any] = {"timeout": self._timeout_seconds}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream(
                    "POST",
                    url,
                    params=params or None,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        detail = await _extract_error_detail(response)
                        raise OpenAICodexRuntimeError(
                            f"OpenAI Codex runtime endpoint 返回 {response.status_code}: {detail}",
                            status_code=response.status_code,
                        )
                    return await _parse_sse_response(response)
        except OpenAICodexRuntimeError:
            raise
        except httpx.TimeoutException as exc:
            raise OpenAICodexRuntimeError(f"OpenAI Codex runtime 请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or "network error"
            raise OpenAICodexRuntimeError(f"OpenAI Codex runtime 请求失败: {detail}") from exc


async def _parse_sse_response(response: httpx.Response) -> CodexRuntimeTurnResult:
    text_parts: list[str] = []
    tool_calls: list[CodexRuntimeToolCall] = []
    saw_text_delta = False
    buffered_data_lines: list[str] = []

    def flush_payload(data_lines: list[str]) -> bool:
        nonlocal saw_text_delta
        if not data_lines:
            return False
        payload = "\n".join(data_lines).strip()
        if not payload:
            return False
        if payload == "[DONE]":
            return True
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return False
        if not isinstance(event, dict):
            return False

        event_type = str(event.get("type") or "").strip()
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                text_parts.append(delta)
                saw_text_delta = True
            return False

        if event_type != "response.output_item.done":
            return False

        item = event.get("item")
        if not isinstance(item, dict):
            return False

        item_type = str(item.get("type") or "").strip()
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            raw_arguments = item.get("arguments")
            if isinstance(raw_arguments, dict):
                raw_arguments_text = json.dumps(raw_arguments, ensure_ascii=False)
            elif isinstance(raw_arguments, str):
                raw_arguments_text = raw_arguments
            else:
                raw_arguments_text = ""
            parsed_arguments = _safe_parse_json(raw_arguments_text)
            if call_id and name:
                tool_calls.append(
                    CodexRuntimeToolCall(
                        id=call_id,
                        name=name,
                        arguments=parsed_arguments,
                        raw_arguments=raw_arguments_text,
                    )
                )
            return False

        if item_type == "message" and not saw_text_delta:
            fallback_text = _extract_message_text(item)
            if fallback_text:
                text_parts.append(fallback_text)
            return False

        return False

    async for raw_line in response.aiter_lines():
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", "replace")
        else:
            line = raw_line

        if line is None:
            continue
        if not line:
            if flush_payload(buffered_data_lines):
                break
            buffered_data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buffered_data_lines.append(line[5:].lstrip())

    if buffered_data_lines:
        flush_payload(buffered_data_lines)

    return CodexRuntimeTurnResult(text="".join(text_parts), tool_calls=tool_calls)


def _map_messages_to_codex_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        role = str(message.get("role") or "user").strip().lower()
        content_text = _normalize_message_content(message.get("content", ""))

        if role == "system":
            if content_text:
                instructions_parts.append(content_text)
            continue

        if role == "tool":
            call_id = str(message.get("tool_call_id") or "").strip()
            if call_id:
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": content_text,
                    }
                )
            continue

        if role == "assistant":
            if content_text:
                input_items.append(_message_item(role="assistant", text=content_text))
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_index, tool_call in enumerate(tool_calls):
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function")
                    function_payload = function if isinstance(function, dict) else {}
                    name = str(function_payload.get("name") or "").strip()
                    call_id = str(
                        tool_call.get("id")
                        or f"call_{message_index}_{tool_index}"
                    ).strip()
                    raw_arguments = function_payload.get("arguments")
                    if isinstance(raw_arguments, dict):
                        raw_arguments_text = json.dumps(raw_arguments, ensure_ascii=False)
                    elif isinstance(raw_arguments, str):
                        raw_arguments_text = raw_arguments
                    else:
                        raw_arguments_text = "{}"
                    if name and call_id:
                        input_items.append(
                            {
                                "type": "function_call",
                                "call_id": call_id,
                                "name": name,
                                "arguments": raw_arguments_text,
                            }
                        )
            continue

        # 未知 role 统一当作 user 输入。
        input_items.append(_message_item(role="user", text=content_text))

    if not input_items:
        input_items.append(_message_item(role="user", text=""))

    instructions = "\n\n".join(part for part in instructions_parts if part).strip()
    if not instructions:
        instructions = _DEFAULT_INSTRUCTIONS
    return instructions, input_items


def _message_item(*, role: str, text: str) -> dict[str, Any]:
    content_type = "output_text" if role == "assistant" else "input_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": content_type, "text": text}],
    }


def _map_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function")
        payload = function if isinstance(function, dict) else {}
        name = str(payload.get("name") or "").strip()
        if not name:
            continue
        description = str(payload.get("description") or "")
        parameters = payload.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        mapped.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        )
    return mapped


def _normalize_message_content(content: Any) -> str:
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
            part_text = part.get("text")
            if isinstance(part_text, str):
                text_parts.append(part_text)
                continue
            if isinstance(part_text, dict):
                nested = part_text.get("value") or part_text.get("content")
                if isinstance(nested, str):
                    text_parts.append(nested)
        return "\n".join(piece for piece in text_parts if piece)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _extract_message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for chunk in content:
        if not isinstance(chunk, dict):
            continue
        text = chunk.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _safe_parse_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _extract_error_detail(response: httpx.Response) -> str:
    body = (await response.aread()).decode("utf-8", "replace").strip()
    if not body:
        return "unknown error"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return body
