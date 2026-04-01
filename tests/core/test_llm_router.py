"""LLMRouter 单元测试。"""

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def reset_module_cache():
    """每个测试前后清除 llm_router 和 settings 的模块缓存，确保测试隔离。"""
    for mod in list(sys.modules.keys()):
        if "llm_router" in mod or "settings" in mod or "openai_codex_runtime" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "llm_router" in mod or "settings" in mod or "openai_codex_runtime" in mod:
            del sys.modules[mod]


class TestLLMRouterInit:
    """测试路由器初始化和配置加载。"""

    def test_chat_purpose_uses_chat_model_when_configured(self):
        """当 CHAT 模型单独配置时，chat purpose 使用专用模型。"""
        with patch.dict("os.environ", {
            "LLM_CHAT_MODEL": "glm-4-plus",
            "LLM_CHAT_BASE_URL": "https://chat.api.com/v1",
            "LLM_CHAT_API_KEY": "chat-key",
            "LLM_TOOL_MODEL": "glm-4-flash",
            "LLM_TOOL_BASE_URL": "https://tool.api.com/v1",
            "LLM_TOOL_API_KEY": "tool-key",
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("chat") == "glm-4-plus"
            assert router.model_for("tool") == "glm-4-flash"

    def test_fallback_to_generic_when_chat_not_configured(self):
        """当专用 CHAT 模型未配置时，回退到通用 LLM_MODEL。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
            "LLM_CHAT_API_KEY": "",
            "LLM_CHAT_BASE_URL": "",
            "LLM_CHAT_MODEL": "",
            "LLM_TOOL_API_KEY": "",
            "LLM_TOOL_BASE_URL": "",
            "LLM_TOOL_MODEL": "",
            "NIM_API_KEY": "",
            "NIM_BASE_URL": "",
            "NIM_MODEL": "",
        }, clear=True):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("chat") == "glm-4-flash"
            assert router.model_for("tool") == "glm-4-flash"

    def test_heartbeat_uses_nim_when_configured(self):
        """NIM 配置存在时，heartbeat purpose 使用 NIM 模型。"""
        with patch.dict("os.environ", {
            "NIM_API_KEY": "nvapi-test",
            "NIM_BASE_URL": "https://integrate.api.nvidia.com/v1",
            "NIM_MODEL": "meta/llama-3.1-8b-instruct",
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("heartbeat") == "meta/llama-3.1-8b-instruct"

    def test_heartbeat_falls_back_when_nim_not_configured(self):
        """NIM 未配置时，heartbeat purpose 回退到通用模型。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
            "LLM_CHAT_API_KEY": "",
            "LLM_CHAT_BASE_URL": "",
            "LLM_CHAT_MODEL": "",
            "LLM_TOOL_API_KEY": "",
            "LLM_TOOL_BASE_URL": "",
            "LLM_TOOL_MODEL": "",
            "NIM_API_KEY": "",
            "NIM_BASE_URL": "",
            "NIM_MODEL": "",
        }, clear=True):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("heartbeat") == "glm-4-flash"

    def test_anthropic_base_url_uses_anthropic_client(self):
        """Anthropic 兼容地址应自动切到 Anthropic SDK。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/anthropic/v1",
            "LLM_MODEL": "MiniMax-M2.7",
            "LLM_CHAT_API_KEY": "",
            "LLM_CHAT_BASE_URL": "",
            "LLM_CHAT_MODEL": "",
            "LLM_TOOL_API_KEY": "",
            "LLM_TOOL_BASE_URL": "",
            "LLM_TOOL_MODEL": "",
            "NIM_API_KEY": "",
            "NIM_BASE_URL": "",
            "NIM_MODEL": "",
        }, clear=True):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("chat") == "MiniMax-M2.7"
            assert router._api_types["chat"] == "anthropic"


@pytest.mark.asyncio
class TestLLMRouterComplete:
    """测试 complete() 接口。"""

    async def test_complete_calls_correct_client(self):
        """complete() 用正确的 purpose 调用对应的 client。"""
        with patch.dict("os.environ", {
            "LLM_CHAT_MODEL": "glm-4-plus",
            "LLM_CHAT_BASE_URL": "https://chat.api.com/v1",
            "LLM_CHAT_API_KEY": "chat-key",
            "LLM_TOOL_MODEL": "glm-4-flash",
            "LLM_TOOL_BASE_URL": "https://tool.api.com/v1",
            "LLM_TOOL_API_KEY": "tool-key",
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()

            mock_response = MagicMock()
            mock_response.choices[0].message.content = "测试回复"

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client

            messages = [{"role": "user", "content": "你好"}]
            result = await router.complete(messages, purpose="chat")

            assert result == "测试回复"
            mock_client.chat.completions.create.assert_called_once()
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "glm-4-plus"
            assert call_kwargs["messages"] == messages

    async def test_complete_converts_messages_for_anthropic(self):
        """Anthropic 兼容 provider 需要拆分 system prompt 并走 messages.create。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/anthropic/v1",
            "LLM_MODEL": "MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()

            mock_block = MagicMock()
            mock_block.type = "text"
            mock_block.text = "测试回复"

            mock_response = MagicMock()
            mock_response.content = [mock_block]

            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client
            router._api_types["chat"] = "anthropic"

            messages = [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "你好"},
            ]
            result = await router.complete(messages, purpose="chat")

            assert result == "测试回复"
            mock_client.messages.create.assert_called_once()
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == "MiniMax-M2.7"
            assert call_kwargs["system"] == "你是助手"
            assert call_kwargs["messages"] == [{"role": "user", "content": "你好"}]

    async def test_complete_retries_anthropic_when_first_response_has_only_thinking(self):
        """Anthropic 首轮仅返回 thinking 且被截断时，应自动补一次更大的 token 预算。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/anthropic/v1",
            "LLM_MODEL": "MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()

            thinking_block = MagicMock()
            thinking_block.type = "thinking"
            thinking_block.thinking = "先想一想"

            first_response = MagicMock()
            first_response.content = [thinking_block]
            first_response.stop_reason = "max_tokens"

            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = "{\"agent\": null}"

            second_response = MagicMock()
            second_response.content = [text_block]
            second_response.stop_reason = "end_turn"

            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])
            router._clients["tool"] = mock_client
            router._api_types["tool"] = "anthropic"

            result = await router.complete(
                [{"role": "user", "content": "请只输出严格 JSON：{\"agent\": null}"}],
                purpose="tool",
                max_tokens=64,
            )

            assert result == "{\"agent\": null}"
            assert mock_client.messages.create.call_count == 2
            first_call = mock_client.messages.create.call_args_list[0].kwargs
            second_call = mock_client.messages.create.call_args_list[1].kwargs
            assert first_call["max_tokens"] == 64
            assert second_call["max_tokens"] == 512


@pytest.mark.asyncio
class TestLLMRouterTools:
    async def test_complete_with_tools_normalizes_openai_tool_calls(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            router = LLMRouter()

            tool_call = MagicMock()
            tool_call.id = "call_1"
            tool_call.function.name = "execute_shell"
            tool_call.function.arguments = '{"command": "pwd"}'

            message = MagicMock()
            message.content = ""
            message.tool_calls = [tool_call]

            mock_response = MagicMock()
            mock_response.choices[0].message = message

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client
            router._api_types["chat"] = "openai"
            router._base_urls["chat"] = "https://generic.api.com/v1"

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell",
                        "description": "执行命令",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

            result = await router.complete_with_tools(
                [{"role": "user", "content": "看看当前目录"}],
                tools=tools,
                purpose="chat",
            )

            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].name == "execute_shell"
            assert result.tool_calls[0].arguments == {"command": "pwd"}
            assert result.continuation_message == {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "execute_shell",
                            "arguments": '{"command": "pwd"}',
                        },
                    }
                ],
            }

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["tools"] == tools
            assert call_kwargs["tool_choice"] == "auto"
            assert call_kwargs["parallel_tool_calls"] is False

    async def test_complete_with_tools_normalizes_anthropic_tool_use(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/anthropic/v1",
            "LLM_MODEL": "MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            router = LLMRouter()

            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = "我来看看。"

            tool_block = MagicMock()
            tool_block.type = "tool_use"
            tool_block.id = "toolu_1"
            tool_block.name = "execute_shell"
            tool_block.input = {"command": "pwd"}

            mock_response = MagicMock()
            mock_response.content = [text_block, tool_block]

            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client
            router._api_types["chat"] = "anthropic"

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell",
                        "description": "执行命令",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

            result = await router.complete_with_tools(
                [
                    {"role": "system", "content": "你是助手"},
                    {"role": "user", "content": "看看当前目录"},
                ],
                tools=tools,
                purpose="chat",
            )

            assert result.text == "我来看看。"
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].id == "toolu_1"
            assert result.tool_calls[0].name == "execute_shell"
            assert result.tool_calls[0].arguments == {"command": "pwd"}
            assert result.continuation_message == {
                "role": "assistant",
                "content": [text_block, tool_block],
            }

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["system"] == "你是助手"
            assert call_kwargs["tools"] == [
                {
                    "name": "execute_shell",
                    "description": "执行命令",
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ]
            assert call_kwargs["tool_choice"] == {
                "type": "auto",
                "disable_parallel_tool_use": True,
            }

    async def test_complete_with_tools_applies_minimax_openai_compat(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            router = LLMRouter()

            message = MagicMock()
            message.content = "ok"
            message.tool_calls = []

            mock_response = MagicMock()
            mock_response.choices[0].message = message

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client
            router._api_types["chat"] = "openai"
            router._base_urls["chat"] = "https://api.minimaxi.com/v1"

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell",
                        "description": "执行命令",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

            await router.complete_with_tools(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "你好"},
                            {"type": "image_url", "image_url": {"url": "https://x"}},
                        ],
                    }
                ],
                tools=tools,
                purpose="chat",
            )

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert "max_tokens" not in call_kwargs
            assert call_kwargs["max_completion_tokens"] == 1024
            assert call_kwargs["temperature"] == 1.0
            assert call_kwargs["n"] == 1
            assert "parallel_tool_calls" not in call_kwargs
            assert call_kwargs["messages"] == [{"role": "user", "content": "你好"}]

    def test_normalize_minimax_openai_request_clamps_invalid_values(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            router = LLMRouter()

            normalized = router._normalize_minimax_openai_request(
                "chat",
                {
                    "model": "MiniMax-M2.7",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 99999,
                    "temperature": 0,
                    "top_p": 0,
                    "n": 3,
                    "function_call": "auto",
                    "parallel_tool_calls": False,
                },
            )

            assert normalized["max_completion_tokens"] == 2048
            assert normalized["temperature"] == 1.0
            assert normalized["top_p"] == 0.95
            assert normalized["n"] == 1
            assert "max_tokens" not in normalized
            assert "function_call" not in normalized
            assert "parallel_tool_calls" not in normalized

    def test_build_tool_result_message_for_openai(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            from src.core.llm_router import LLMRouter, ToolCallRequest

            router = LLMRouter()
            result = router.build_tool_result_message(
                purpose="chat",
                tool_results=[
                    (
                        ToolCallRequest(
                            id="call_1",
                            name="execute_shell",
                            arguments={"command": "pwd"},
                        ),
                        '{"stdout": "/tmp"}',
                    )
                ],
            )

            assert result == {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "execute_shell",
                "content": '{"stdout": "/tmp"}',
            }

    def test_build_tool_result_message_for_openai_multiple_results(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            from src.core.llm_router import LLMRouter, ToolCallRequest

            router = LLMRouter()
            result = router.build_tool_result_message(
                purpose="chat",
                tool_results=[
                    (
                        ToolCallRequest(
                            id="call_1",
                            name="execute_shell",
                            arguments={"command": "pwd"},
                        ),
                        '{"stdout": "/tmp"}',
                    ),
                    (
                        ToolCallRequest(
                            id="call_2",
                            name="read_file",
                            arguments={"path": "/tmp/a.txt"},
                        ),
                        '{"path": "/tmp/a.txt", "content": "hello"}',
                    ),
                ],
            )

            assert result == [
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "execute_shell",
                    "content": '{"stdout": "/tmp"}',
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_2",
                    "name": "read_file",
                    "content": '{"path": "/tmp/a.txt", "content": "hello"}',
                },
            ]

    def test_build_tool_result_message_for_anthropic(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://api.minimaxi.com/anthropic/v1",
            "LLM_MODEL": "MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter, ToolCallRequest

            router = LLMRouter()
            router._api_types["chat"] = "anthropic"
            result = router.build_tool_result_message(
                purpose="chat",
                tool_results=[
                    (
                        ToolCallRequest(
                            id="toolu_1",
                            name="execute_shell",
                            arguments={"command": "pwd"},
                        ),
                        '{"stdout": "/tmp"}',
                    )
                ],
            )

            assert result == {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": '{"stdout": "/tmp"}',
                    }
                ],
            }


@pytest.mark.asyncio
class TestCompleteStructuredSlot:
    """验证 complete_structured 的 slot 参数正确路由。"""

    async def test_complete_structured_slot_routes_to_correct_model(self):
        """complete_structured(slot=...) 应使用 slot 对应的模型，而非 purpose 默认模型。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            router = LLMRouter()

            tool_call = MagicMock()
            tool_call.id = "call_1"
            tool_call.function.name = "submit_result"
            tool_call.function.arguments = '{"approved": true, "violations": []}'

            message = MagicMock()
            message.content = ""
            message.tool_calls = [tool_call]

            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=message)]

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            # persona_expression maps to "chat" purpose
            router._clients["persona_expression"] = mock_client
            router._api_types["persona_expression"] = "openai"
            router._base_urls["persona_expression"] = "https://generic.api.com/v1"

            schema = {
                "type": "object",
                "properties": {
                    "approved": {"type": "boolean"},
                    "violations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["approved", "violations"],
            }

            result = await router.complete_structured(
                [{"role": "user", "content": "检查宪法"}],
                result_schema=schema,
                result_tool_name="submit_result",
                slot="persona_expression",
                max_tokens=512,
                session_key="system:test",
                origin="test",
            )

            assert result == {"approved": True, "violations": []}
            mock_client.chat.completions.create.assert_called_once()

    async def test_complete_structured_without_slot_falls_back_to_purpose(self):
        """不传 slot 时应回退到 purpose 路由（向后兼容）。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            router = LLMRouter()

            tool_call = MagicMock()
            tool_call.id = "call_1"
            tool_call.function.name = "submit_result"
            tool_call.function.arguments = '{"result": "ok"}'

            message = MagicMock()
            message.content = ""
            message.tool_calls = [tool_call]

            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=message)]

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            router._clients["chat"] = mock_client
            router._api_types["chat"] = "openai"
            router._base_urls["chat"] = "https://generic.api.com/v1"

            result = await router.complete_structured(
                [{"role": "user", "content": "test"}],
                result_schema={"type": "object", "properties": {"result": {"type": "string"}}},
                result_tool_name="submit_result",
                # no slot= passed
                purpose="chat",
                max_tokens=256,
            )

            assert result == {"result": "ok"}
            mock_client.chat.completions.create.assert_called_once()


class _FakeAuthManager:
    def __init__(self, candidates):
        self._candidates = list(candidates)
        self.successes = []
        self.failures = []

    def resolve_candidates(
        self,
        *,
        purpose,
        slot=None,
        session_key=None,
        allow_failover=True,
        exclude_profiles=None,
        origin=None,
    ):
        excluded = exclude_profiles or set()
        return [
            candidate
            for candidate in self._candidates
            if not candidate.profile_id or candidate.profile_id not in excluded
        ]

    def mark_success(self, candidate):
        self.successes.append(candidate)

    def mark_failure(self, candidate, kind):
        self.failures.append((candidate, kind))

    def refresh_candidate(self, candidate):
        raise RuntimeError("refresh should not be called in this test")


class _FakeAuthManagerByPurpose(_FakeAuthManager):
    def __init__(self, purpose_candidates):
        self._purpose_candidates = {
            purpose: list(candidates)
            for purpose, candidates in purpose_candidates.items()
        }
        self.successes = []
        self.failures = []

    def resolve_candidates(
        self,
        *,
        purpose,
        slot=None,
        session_key=None,
        allow_failover=True,
        exclude_profiles=None,
        origin=None,
    ):
        excluded = exclude_profiles or set()
        candidates = self._purpose_candidates.get(purpose, [])
        return [
            candidate
            for candidate in candidates
            if not candidate.profile_id or candidate.profile_id not in excluded
        ]


@pytest.mark.asyncio
class TestLLMRouterOpenAICodex:
    async def test_openai_codex_model_routes_to_runtime_complete(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
            "LLM_CHAT_MODEL": "openai-codex/gpt-5.4",
            "LLM_CHAT_BASE_URL": "https://whatever.invalid/v1",
        }, clear=True):
            from src.auth.models import ResolvedAuthCandidate
            from src.core.llm_router import LLMRouter
            from src.core.openai_codex_runtime import CodexRuntimeTurnResult

            candidate = ResolvedAuthCandidate(
                purpose="chat",
                base_url="https://whatever.invalid/v1",
                model="openai-codex/gpt-5.4",
                api_type="openai_codex",
                auth_value="oauth-token",
                auth_kind="oauth",
                source="auth_profile",
                provider="openai",
                profile_id="openai:tester@example.com",
                profile_type="oauth",
                metadata={"accountId": "acct_123"},
            )
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth)
            router._codex_runtime.complete = AsyncMock(
                return_value=CodexRuntimeTurnResult(text="codex-ok", tool_calls=[])
            )

            result = await router.complete(
                [{"role": "user", "content": "hello"}],
                purpose="chat",
            )

            assert result == "codex-ok"
            router._codex_runtime.complete.assert_awaited_once()
            call_kwargs = router._codex_runtime.complete.await_args.kwargs
            assert call_kwargs["model"] == "gpt-5.4"
            assert call_kwargs["access_token"] == "oauth-token"
            assert call_kwargs["account_id"] == "acct_123"
            assert fake_auth.successes == [candidate]

    async def test_openai_codex_complete_with_tools_keeps_openai_turn_shape(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
            "LLM_CHAT_MODEL": "openai-codex/gpt-5.4",
            "LLM_CHAT_BASE_URL": "https://whatever.invalid/v1",
        }, clear=True):
            from src.auth.models import ResolvedAuthCandidate
            from src.core.llm_router import LLMRouter
            from src.core.openai_codex_runtime import (
                CodexRuntimeToolCall,
                CodexRuntimeTurnResult,
            )

            candidate = ResolvedAuthCandidate(
                purpose="chat",
                base_url="https://whatever.invalid/v1",
                model="openai-codex/gpt-5.4",
                api_type="openai_codex",
                auth_value="oauth-token",
                auth_kind="oauth",
                source="auth_profile",
                provider="openai",
                profile_id="openai:tester@example.com",
                profile_type="oauth",
            )
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth)
            router._codex_runtime.complete = AsyncMock(
                return_value=CodexRuntimeTurnResult(
                    text="",
                    tool_calls=[
                        CodexRuntimeToolCall(
                            id="call_1",
                            name="execute_shell",
                            arguments={"command": "pwd"},
                            raw_arguments='{"command":"pwd"}',
                        )
                    ],
                )
            )

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell",
                        "description": "执行命令",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

            result = await router.complete_with_tools(
                [{"role": "user", "content": "看看目录"}],
                tools=tools,
                purpose="chat",
            )

            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].id == "call_1"
            assert result.tool_calls[0].name == "execute_shell"
            assert result.tool_calls[0].arguments == {"command": "pwd"}
            assert result.continuation_message == {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "execute_shell",
                            "arguments": '{"command":"pwd"}',
                        },
                    }
                ],
            }

    async def test_openai_codex_model_requires_oauth_profile(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "openai-codex/gpt-5.4",
        }, clear=True):
            from src.auth.models import ResolvedAuthCandidate
            from src.core.llm_router import LLMRouter

            candidate = ResolvedAuthCandidate(
                purpose="chat",
                base_url="https://generic.api.com/v1",
                model="openai-codex/gpt-5.4",
                api_type="openai_codex",
                auth_value="api-key",
                auth_kind="api_key",
                source="auth_profile",
                provider="openai",
                profile_id="openai:key",
                profile_type="api_key",
            )
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth)
            router._codex_runtime.complete = AsyncMock()

            with pytest.raises(PermissionError, match="openai-codex"):
                await router.complete(
                    [{"role": "user", "content": "hello"}],
                    purpose="chat",
                )

            router._codex_runtime.complete.assert_not_awaited()
            assert fake_auth.failures
            assert fake_auth.failures[0][1] == "auth"


@pytest.mark.asyncio
class TestLLMRouterModelSwitch:
    async def test_model_list_and_selector_support_index_alias_ref(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
            "LLM_MODEL_ALLOWLIST": "codex=openai-codex/gpt-5.4,minimax=MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            router = LLMRouter()
            options = router.list_model_options()
            assert options == [
                {"index": 1, "alias": "codex", "ref": "openai-codex/gpt-5.4"},
                {"index": 2, "alias": "minimax", "ref": "MiniMax-M2.7"},
            ]

            assert router._resolve_model_option("1").ref == "openai-codex/gpt-5.4"
            assert router._resolve_model_option("codex").ref == "openai-codex/gpt-5.4"
            assert router._resolve_model_option("MiniMax-M2.7").ref == "MiniMax-M2.7"

    async def test_session_override_only_applies_to_matching_session_key(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
            "LLM_CHAT_MODEL": "MiniMax-M2.7",
            "LLM_MODEL_ALLOWLIST": "MiniMax-M2.7,openai/gpt-4.1",
        }, clear=True):
            from src.auth.models import ResolvedAuthCandidate
            from src.core.llm_router import LLMRouter

            candidate = ResolvedAuthCandidate(
                purpose="chat",
                base_url="https://generic.api.com/v1",
                model="MiniMax-M2.7",
                api_type="openai",
                auth_value="test-key",
                auth_kind="env",
                source="env_fallback",
                provider=None,
                profile_id=None,
                profile_type=None,
            )
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth)

            mock_response = MagicMock()
            mock_response.choices[0].message.content = "ok"
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client

            router.switch_session_model(session_key="chat:1", selector="2")
            assert router.model_for("chat", session_key="chat:1") == "openai/gpt-4.1"
            assert router.model_for("chat", session_key="chat:2") == "MiniMax-M2.7"

            await router.complete(
                [{"role": "user", "content": "hello"}],
                purpose="chat",
                session_key="chat:1",
                allow_failover=False,
            )
            first_call = mock_client.chat.completions.create.call_args.kwargs
            assert first_call["model"] == "openai/gpt-4.1"

            await router.complete(
                [{"role": "user", "content": "hello"}],
                purpose="chat",
                session_key="chat:2",
                allow_failover=False,
            )
            second_call = mock_client.chat.completions.create.call_args.kwargs
            assert second_call["model"] == "MiniMax-M2.7"

    async def test_switch_codex_uses_compatibility_first_and_keeps_other_overrides(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
            "LLM_MODEL_ALLOWLIST": "minimax=MiniMax-M2.7,codex=openai-codex/gpt-5.4",
        }, clear=True):
            from src.auth.models import ResolvedAuthCandidate
            from src.core.llm_router import LLMRouter

            chat_candidate = ResolvedAuthCandidate(
                purpose="chat",
                base_url="https://generic.api.com/v1",
                model="MiniMax-M2.7",
                api_type="openai",
                auth_value="oauth-token",
                auth_kind="oauth",
                source="auth_profile",
                provider="openai",
                profile_id="openai:tester@example.com",
                profile_type="oauth",
            )
            tool_candidate = ResolvedAuthCandidate(
                purpose="tool",
                base_url="https://generic.api.com/v1",
                model="MiniMax-M2.7",
                api_type="openai",
                auth_value="test-key",
                auth_kind="env",
                source="env_fallback",
                provider=None,
                profile_id=None,
                profile_type=None,
            )
            heartbeat_candidate = ResolvedAuthCandidate(
                purpose="heartbeat",
                base_url="https://generic.api.com/v1",
                model="MiniMax-M2.7",
                api_type="openai",
                auth_value="api-key",
                auth_kind="api_key",
                source="auth_profile",
                provider="openai",
                profile_id="openai:key",
                profile_type="api_key",
            )
            fake_auth = _FakeAuthManagerByPurpose(
                {
                    "chat": [chat_candidate],
                    "tool": [tool_candidate],
                    "heartbeat": [heartbeat_candidate],
                }
            )
            router = LLMRouter(auth_manager=fake_auth)

            router.switch_session_model(session_key="chat:42", selector="minimax")
            result = router.switch_session_model(session_key="chat:42", selector="codex")

            assert result["applied"] == {"chat": "openai-codex/gpt-5.4"}
            assert result["skipped"] == {
                "tool": "缺少 openai oauth profile",
                "heartbeat": "缺少 openai oauth profile",
            }

            status = router.model_status(session_key="chat:42")
            assert status["purposes"]["chat"]["effective"] == "openai-codex/gpt-5.4"
            assert status["purposes"]["tool"]["effective"] == "MiniMax-M2.7"
            assert status["purposes"]["heartbeat"]["effective"] == "MiniMax-M2.7"

    async def test_model_default_clears_session_overrides(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
            "LLM_MODEL_ALLOWLIST": "MiniMax-M2.7,openai/gpt-4.1",
        }, clear=True):
            from src.auth.models import ResolvedAuthCandidate
            from src.core.llm_router import LLMRouter

            candidate = ResolvedAuthCandidate(
                purpose="chat",
                base_url="https://generic.api.com/v1",
                model="MiniMax-M2.7",
                api_type="openai",
                auth_value="test-key",
                auth_kind="env",
                source="env_fallback",
                provider=None,
                profile_id=None,
                profile_type=None,
            )
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth)

            router.switch_session_model(session_key="chat:99", selector="2")
            assert router.model_for("chat", session_key="chat:99") == "openai/gpt-4.1"

            reset_result = router.clear_session_model(session_key="chat:99")
            assert reset_result["cleared"] == 3
            assert router.model_for("chat", session_key="chat:99") == "MiniMax-M2.7"

    async def test_openai_codex_model_requires_bound_profile_not_env_fallback(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "openai-codex/gpt-5.4",
        }, clear=True):
            from src.auth.models import ResolvedAuthCandidate
            from src.core.llm_router import LLMRouter

            candidate = ResolvedAuthCandidate(
                purpose="chat",
                base_url="https://generic.api.com/v1",
                model="openai-codex/gpt-5.4",
                api_type="openai_codex",
                auth_value="generic-key",
                auth_kind="env",
                source="env_fallback",
                provider=None,
                profile_id=None,
                profile_type=None,
            )
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth)
            router._codex_runtime.complete = AsyncMock()

            with pytest.raises(PermissionError, match="openai-codex"):
                await router.complete(
                    [{"role": "user", "content": "hello"}],
                    purpose="chat",
                )

            router._codex_runtime.complete.assert_not_awaited()
            assert fake_auth.failures
            assert fake_auth.failures[0][1] == "auth"
