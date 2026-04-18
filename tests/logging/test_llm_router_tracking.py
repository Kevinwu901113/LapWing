"""Verify LLMRouter._tracked_call records LLM_REQUEST + LLM_RESPONSE.

Step 1b of Blueprint v2.0 — every LLM call path must be instrumented.
"""

from __future__ import annotations

import pytest

from src.core.llm_router import LLMRouter
from src.logging.state_mutation_log import (
    MutationType,
    StateMutationLog,
    iteration_context,
)


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeAnthropicBlock:
    def __init__(self, btype: str, **kwargs) -> None:
        self.type = btype
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeAnthropicResponse:
    def __init__(self, content, stop_reason="end_turn", usage=None) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or FakeUsage(10, 20)


class FakeOpenAIMessage:
    def __init__(self, content: str, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class FakeOpenAIChoice:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.message = FakeOpenAIMessage(content)
        self.finish_reason = finish_reason


class FakeOpenAIResponse:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.choices = [FakeOpenAIChoice(content, finish_reason)]
        self.usage = FakeUsage(5, 15)


@pytest.fixture
async def log(tmp_path):
    store = StateMutationLog(tmp_path / "ml.db", logs_dir=tmp_path / "logs")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def router(log):
    r = LLMRouter()  # no mutation_log via constructor
    r.set_mutation_log(log)
    # Stub base_urls so snapshots aren't empty
    r._base_urls = {"chat": "https://api.minimaxi.com/anthropic"}
    return r


class TestTrackedCall:
    async def test_anthropic_success_records_pair(self, router, log):
        response = FakeAnthropicResponse(
            content=[
                FakeAnthropicBlock("thinking", thinking="…内心…", signature="sig1"),
                FakeAnthropicBlock("text", text="你好"),
            ],
            stop_reason="end_turn",
        )

        async def call_fn():
            return response

        with iteration_context("iter-1", chat_id="chat-A"):
            result = await router._tracked_call(
                "anthropic",
                {
                    "model_slot": "chat",
                    "model_name": "MiniMax-M2.7",
                    "base_url": "https://api.minimaxi.com/anthropic",
                    "purpose": "main_conversation",
                    "messages": [{"role": "user", "content": "hi"}],
                    "system": None,
                    "tools": None,
                    "max_tokens": 1024,
                    "temperature": None,
                },
                call_fn,
            )
        assert result is response

        reqs = await log.query_by_type(MutationType.LLM_REQUEST)
        resps = await log.query_by_type(MutationType.LLM_RESPONSE)
        assert len(reqs) == 1
        assert len(resps) == 1
        assert reqs[0].iteration_id == "iter-1"
        assert reqs[0].chat_id == "chat-A"
        assert reqs[0].payload["protocol"] == "anthropic"
        assert reqs[0].payload["request_id"] == resps[0].payload["request_id"]
        # messages preserved verbatim
        assert reqs[0].payload["messages"] == [{"role": "user", "content": "hi"}]

        resp_payload = resps[0].payload
        assert resp_payload["stop_reason"] == "end_turn"
        block_types = [b["type"] for b in resp_payload["content_blocks"]]
        assert block_types == ["thinking", "text"]
        assert resp_payload["content_blocks"][0]["signature"] == "sig1"
        assert resp_payload["content_blocks"][1]["content"] == "你好"
        assert resp_payload["usage"] == {"input_tokens": 10, "output_tokens": 20}
        assert resp_payload["error"] is None
        assert resp_payload["latency_ms"] >= 0

    async def test_openai_success_maps_blocks(self, router, log):
        response = FakeOpenAIResponse(content="hello", finish_reason="stop")

        async def call_fn():
            return response

        await router._tracked_call(
            "openai",
            {
                "model_slot": "tool",
                "model_name": "glm-4.5",
                "base_url": "https://openai-compatible",
                "purpose": "lightweight_judgment",
                "messages": [{"role": "user", "content": "ping"}],
                "tools": None,
                "max_tokens": 256,
                "temperature": 0.2,
            },
            call_fn,
        )
        resps = await log.query_by_type(MutationType.LLM_RESPONSE)
        assert len(resps) == 1
        payload = resps[0].payload
        assert payload["stop_reason"] == "stop"
        assert payload["content_blocks"] == [{"type": "text", "content": "hello"}]
        assert payload["usage"] == {"input_tokens": 5, "output_tokens": 15}

    async def test_exception_still_records_response(self, router, log):
        async def call_fn():
            raise RuntimeError("API boom")

        with pytest.raises(RuntimeError, match="API boom"):
            await router._tracked_call(
                "anthropic",
                {"model_slot": "chat", "messages": [], "model_name": "m", "base_url": "", "purpose": ""},
                call_fn,
            )

        reqs = await log.query_by_type(MutationType.LLM_REQUEST)
        resps = await log.query_by_type(MutationType.LLM_RESPONSE)
        assert len(reqs) == 1
        assert len(resps) == 1
        assert resps[0].payload["error"] == "RuntimeError: API boom"
        assert resps[0].payload["content_blocks"] == []
        assert resps[0].payload["stop_reason"] is None

    async def test_no_mutation_log_is_passthrough(self, log):
        bare = LLMRouter()
        # _mutation_log stays None — calls should pass through with no records

        async def call_fn():
            return "ok"

        result = await bare._tracked_call("anthropic", {}, call_fn)
        assert result == "ok"
        # And no records anywhere on our shared log (because bare isn't connected to it)
        assert await log.query_by_type(MutationType.LLM_REQUEST) == []

    async def test_codex_tuple_handled(self, router, log):
        async def call_fn():
            text = "the answer"
            items = [
                {"type": "message", "role": "assistant", "content": "the answer"},
                {
                    "type": "function_call",
                    "name": "search",
                    "arguments": '{"q":"a"}',
                    "call_id": "call_1",
                },
            ]
            return (text, items, {})

        await router._tracked_call(
            "codex_oauth",
            {"model_slot": "heartbeat", "model_name": "gpt", "base_url": "", "purpose": "hb"},
            call_fn,
        )

        resps = await log.query_by_type(MutationType.LLM_RESPONSE)
        blocks = resps[0].payload["content_blocks"]
        types = [b["type"] for b in blocks]
        assert types == ["text", "tool_use"]
        assert blocks[1]["name"] == "search"
        assert blocks[1]["input"] == {"q": "a"}
        assert blocks[1]["id"] == "call_1"
