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
            # system prompt 使用 Anthropic cache_control 格式
            assert call_kwargs["system"] == [
                {"type": "text", "text": "你是助手", "cache_control": {"type": "ephemeral"}}
            ]
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


class TestLLMRouterTools:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
            assert call_kwargs["system"] == [
                {"type": "text", "text": "你是助手", "cache_control": {"type": "ephemeral"}}
            ]
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

    def test_build_tool_result_message_for_openai(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            from src.core.llm_router import LLMRouter, ToolCallRequest

            router = LLMRouter()
            router._api_types["chat"] = "openai"
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
            router._api_types["chat"] = "openai"
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


def _make_mock_model_config(providers_data):
    """构建 mock model_config，providers_data: [(id, name, api_type, base_url, api_key, [(model_id, model_name)])]"""
    from dataclasses import dataclass, field

    @dataclass
    class _ModelInfo:
        id: str
        name: str

    @dataclass
    class _ProviderInfo:
        id: str
        name: str
        api_type: str
        base_url: str
        api_key: str
        models: list = field(default_factory=list)

    @dataclass
    class _Config:
        providers: list = field(default_factory=list)
        slots: dict = field(default_factory=dict)

    providers = []
    for pid, pname, api_type, base_url, api_key, models in providers_data:
        p = _ProviderInfo(id=pid, name=pname, api_type=api_type, base_url=base_url, api_key=api_key)
        p.models = [_ModelInfo(id=mid, name=mname) for mid, mname in models]
        providers.append(p)

    config = _Config(providers=providers)
    mock_cfg = MagicMock()
    mock_cfg.get_full_config.return_value = config
    mock_cfg.resolve_slot.return_value = None
    return mock_cfg


@pytest.mark.asyncio
class TestLLMRouterModelSwitch:
    async def test_model_list_and_selector_support_index_alias_ref(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
        }, clear=True):
            from src.core.llm_router import LLMRouter

            mock_cfg = _make_mock_model_config([
                ("codex", "Codex", "codex_oauth", "", "", [
                    ("openai-codex/gpt-5.4", "Codex GPT-5.4"),
                ]),
                ("minimax", "MiniMax", "anthropic", "https://api.minimaxi.com/anthropic", "sk-test", [
                    ("MiniMax-M2.7", "MiniMax-M2.7"),
                ]),
            ])
            router = LLMRouter(model_config=mock_cfg)
            options = router.list_model_options()
            assert options == [
                {"index": 1, "alias": "Codex GPT-5.4", "ref": "openai-codex/gpt-5.4"},
                {"index": 2, "alias": None, "ref": "MiniMax-M2.7"},
            ]

            assert router._resolve_model_option("1").ref == "openai-codex/gpt-5.4"
            assert router._resolve_model_option("codex gpt-5.4").ref == "openai-codex/gpt-5.4"
            assert router._resolve_model_option("MiniMax-M2.7").ref == "MiniMax-M2.7"

    async def test_session_override_only_applies_to_matching_session_key(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
            "LLM_CHAT_MODEL": "MiniMax-M2.7",
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
            mock_cfg = _make_mock_model_config([
                ("minimax", "MiniMax", "anthropic", "https://api.minimaxi.com/anthropic", "sk-test", [
                    ("MiniMax-M2.7", "MiniMax-M2.7"),
                ]),
                ("openai", "OpenAI", "openai", "https://api.openai.com/v1", "sk-test", [
                    ("openai/gpt-4.1", "GPT-4.1"),
                ]),
            ])
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth, model_config=mock_cfg)

            mock_response = MagicMock()
            mock_response.choices[0].message.content = "ok"
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client
            router._api_types["chat"] = "openai"
            router._base_urls["chat"] = "https://generic.api.com/v1"

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

    async def test_model_default_clears_session_overrides(self):
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "MiniMax-M2.7",
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
            mock_cfg = _make_mock_model_config([
                ("minimax", "MiniMax", "anthropic", "https://api.minimaxi.com/anthropic", "sk-test", [
                    ("MiniMax-M2.7", "MiniMax-M2.7"),
                ]),
                ("openai", "OpenAI", "openai", "https://api.openai.com/v1", "sk-test", [
                    ("openai/gpt-4.1", "GPT-4.1"),
                ]),
            ])
            fake_auth = _FakeAuthManager([candidate])
            router = LLMRouter(auth_manager=fake_auth, model_config=mock_cfg)

            router.switch_session_model(session_key="chat:99", selector="2")
            assert router.model_for("chat", session_key="chat:99") == "openai/gpt-4.1"

            reset_result = router.clear_session_model(session_key="chat:99")
            assert reset_result["cleared"] == 3
            assert router.model_for("chat", session_key="chat:99") == "MiniMax-M2.7"


class TestCodexResponsesAPI:
    """Codex Responses API 协议适配测试。"""

    def test_convert_messages_adds_type_message(self):
        """user/assistant 消息转换后应包含 type: 'message'。"""
        from src.core.llm_protocols import _convert_messages_to_responses_api

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        instructions, items = _convert_messages_to_responses_api(messages)
        assert instructions == "You are helpful."
        assert len(items) == 2
        assert items[0] == {"type": "message", "role": "user", "content": "Hello"}
        assert items[1] == {"type": "message", "role": "assistant", "content": "Hi there"}

    def test_convert_messages_preserves_phase(self):
        """assistant 消息的 phase 字段应被保留。"""
        from src.core.llm_protocols import _convert_messages_to_responses_api

        messages = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "Looking at it...", "phase": "commentary"},
        ]
        _, items = _convert_messages_to_responses_api(messages)
        assert items[1]["phase"] == "commentary"

    def test_convert_messages_no_phase_when_absent(self):
        """没有 phase 字段时不应出现在转换结果中。"""
        from src.core.llm_protocols import _convert_messages_to_responses_api

        messages = [
            {"role": "assistant", "content": "Done."},
        ]
        _, items = _convert_messages_to_responses_api(messages)
        assert "phase" not in items[0]

    def test_convert_messages_tool_to_function_call_output(self):
        """tool 角色消息应转为 function_call_output 格式。"""
        from src.core.llm_protocols import _convert_messages_to_responses_api

        messages = [
            {"role": "tool", "tool_call_id": "call_abc", "content": "result data"},
        ]
        _, items = _convert_messages_to_responses_api(messages)
        assert items[0] == {
            "type": "function_call_output",
            "call_id": "call_abc",
            "output": "result data",
        }

    def test_codex_function_calls_wrapper_expansion(self):
        """_codex_function_calls wrapper 应正确展开，包含 assistant message 和 function_call。"""
        from src.core.llm_protocols import _convert_messages_to_responses_api

        messages = [
            {"role": "user", "content": "Do something"},
            {
                "_codex_function_calls": [
                    {"type": "message", "role": "assistant", "content": "I'll search.", "phase": "commentary"},
                    {"type": "function_call", "name": "web_search", "arguments": '{"q":"test"}', "call_id": "fc_1"},
                ],
            },
            {"type": "function_call_output", "call_id": "fc_1", "output": "results"},
        ]
        instructions, items = _convert_messages_to_responses_api(messages)
        assert instructions is None
        assert len(items) == 4
        # user message
        assert items[0]["role"] == "user"
        assert items[0]["type"] == "message"
        # assistant message from wrapper (re-normalized with phase)
        assert items[1]["role"] == "assistant"
        assert items[1]["type"] == "message"
        assert items[1]["phase"] == "commentary"
        # function_call passthrough
        assert items[2]["type"] == "function_call"
        assert items[2]["name"] == "web_search"
        # function_call_output passthrough
        assert items[3]["type"] == "function_call_output"

    def test_normalize_responses_api_tools_flattens(self):
        """Chat Completions 格式的 tools 应被扁平化为 Responses API 格式。"""
        from src.core.llm_protocols import _normalize_responses_api_tools

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
            }
        ]
        result = _normalize_responses_api_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["name"] == "web_search"
        assert result[0]["description"] == "Search the web"
        assert "function" not in result[0]

    def test_extract_responses_api_tool_calls_basic(self):
        """应从 output items 中正确提取 function_call。"""
        from src.core.llm_protocols import _extract_responses_api_tool_calls

        output_items = [
            {"type": "message", "role": "assistant", "content": "Let me search."},
            {"type": "function_call", "call_id": "fc_1", "name": "web_search",
             "arguments": '{"q": "test"}'},
        ]
        tool_calls, raw = _extract_responses_api_tool_calls(output_items)
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "web_search"
        assert tool_calls[0].id == "fc_1"
        assert tool_calls[0].arguments == {"q": "test"}

    def test_extract_responses_api_tool_calls_preserves_phase(self):
        """function_call 上的 phase 应保留到 raw_tool_calls 中。"""
        from src.core.llm_protocols import _extract_responses_api_tool_calls

        output_items = [
            {"type": "function_call", "call_id": "fc_1", "name": "search",
             "arguments": '{}', "phase": "commentary"},
        ]
        _, raw = _extract_responses_api_tool_calls(output_items)
        assert raw[0]["phase"] == "commentary"

    def test_extract_responses_api_tool_calls_no_phase(self):
        """无 phase 时 raw_tool_calls 中不应有 phase 字段。"""
        from src.core.llm_protocols import _extract_responses_api_tool_calls

        output_items = [
            {"type": "function_call", "call_id": "fc_1", "name": "search",
             "arguments": '{}'},
        ]
        _, raw = _extract_responses_api_tool_calls(output_items)
        assert "phase" not in raw[0]


class TestSanitizeCodexPayload:
    """测试 _sanitize_codex_payload 过滤不支持的参数。"""

    def test_removes_unsupported_params(self):
        from src.core.llm_router import _sanitize_codex_payload
        payload = {
            "model": "gpt-5.4",
            "input": [{"role": "user", "content": "hi"}],
            "instructions": "test",
            "max_output_tokens": 4096,
            "store": False,
            "stream": True,
            "context_management": [{"type": "compaction"}],
        }
        result = _sanitize_codex_payload(payload)
        assert "max_output_tokens" not in result
        assert "context_management" not in result
        assert result["model"] == "gpt-5.4"
        assert result["instructions"] == "test"
        assert result["stream"] is True
        assert result["store"] is False

    def test_keeps_all_allowed_keys(self):
        from src.core.llm_router import _sanitize_codex_payload
        payload = {
            "model": "gpt-5.4",
            "input": [],
            "instructions": "test",
            "tools": [],
            "reasoning": {"effort": "medium"},
            "stream": True,
            "store": False,
            "tool_choice": "auto",
            "previous_response_id": "resp_123",
            "truncation": "auto",
            "include": [],
        }
        result = _sanitize_codex_payload(payload)
        assert result == payload

    def test_empty_payload(self):
        from src.core.llm_router import _sanitize_codex_payload
        assert _sanitize_codex_payload({}) == {}

    def test_all_unsupported(self):
        from src.core.llm_router import _sanitize_codex_payload
        payload = {
            "max_output_tokens": 4096,
            "temperature": 0.7,
            "context_management": [{"type": "compaction"}],
        }
        assert _sanitize_codex_payload(payload) == {}

