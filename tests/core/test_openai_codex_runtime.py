"""OpenAI Codex runtime 单元测试。"""

from __future__ import annotations

import json

import pytest

from src.core.openai_codex_runtime import (
    _map_messages_to_codex_input,
    _parse_sse_response,
)


class _FakeSSEResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def test_map_messages_to_codex_input_supports_tool_roundtrip():
    instructions, input_items = _map_messages_to_codex_input(
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "查北京天气"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Beijing"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_weather",
                "content": '{"city":"Beijing","weather":"sunny"}',
            },
        ]
    )

    assert instructions == "你是助手"
    assert input_items[0] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "查北京天气"}],
    }
    assert input_items[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "get_weather",
        "arguments": '{"city":"Beijing"}',
    }
    assert input_items[2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"city":"Beijing","weather":"sunny"}',
    }


@pytest.mark.asyncio
async def test_parse_sse_response_extracts_text_and_function_call():
    function_call_payload = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "get_weather",
            "arguments": '{"city":"Beijing"}',
        },
    }
    response = _FakeSSEResponse(
        [
            'data: {"type":"response.created"}',
            "",
            'data: {"type":"response.output_text.delta","delta":"codex-"}',
            "",
            'data: {"type":"response.output_text.delta","delta":"ok"}',
            "",
            f"data: {json.dumps(function_call_payload, ensure_ascii=False)}",
            "",
            "data: [DONE]",
            "",
        ]
    )

    result = await _parse_sse_response(response)

    assert result.text == "codex-ok"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"city": "Beijing"}
    assert result.tool_calls[0].raw_arguments == '{"city":"Beijing"}'


@pytest.mark.asyncio
async def test_parse_sse_response_falls_back_to_message_done_when_no_delta():
    message_done_payload = {
        "type": "response.output_item.done",
        "item": {
            "type": "message",
            "content": [{"type": "output_text", "text": "fallback-text"}],
        },
    }
    response = _FakeSSEResponse(
        [
            f"data: {json.dumps(message_done_payload, ensure_ascii=False)}",
            "",
            "data: [DONE]",
            "",
        ]
    )

    result = await _parse_sse_response(response)

    assert result.text == "fallback-text"
    assert result.tool_calls == []
