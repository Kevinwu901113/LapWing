"""LLMRouter 单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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
            import sys
            for mod in list(sys.modules.keys()):
                if "llm_router" in mod or "settings" in mod:
                    del sys.modules[mod]
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
            import importlib
            import sys
            for mod in list(sys.modules.keys()):
                if "llm_router" in mod or "settings" in mod:
                    del sys.modules[mod]

            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("chat") == "glm-4-flash"
            assert router.model_for("tool") == "glm-4-flash"


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
            import sys
            for mod in list(sys.modules.keys()):
                if "llm_router" in mod or "settings" in mod:
                    del sys.modules[mod]

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
