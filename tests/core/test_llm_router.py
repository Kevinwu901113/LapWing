"""LLMRouter 单元测试。"""

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def reset_module_cache():
    """每个测试前后清除 llm_router 和 settings 的模块缓存，确保测试隔离。"""
    for mod in list(sys.modules.keys()):
        if "llm_router" in mod or "settings" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "llm_router" in mod or "settings" in mod:
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
