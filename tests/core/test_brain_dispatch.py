"""brain.py AgentDispatcher 集成测试。"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_module_cache():
    """每个测试前后清除 brain 和相关模块的缓存，确保测试隔离。"""
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]


@pytest.mark.asyncio
class TestBrainDispatch:
    """测试 brain.think() 的 AgentDispatcher 集成行为。"""

    async def test_dispatcher_reply_returned_directly(self):
        """dispatcher 返回非 None 时，直接返回 Agent 回复，跳过正常 LLM 流程。"""
        with patch("src.core.brain.load_prompt", return_value="mock persona"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))

            # 配置 memory mocks
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()

            # 配置 router mock（不应被调用）
            brain.router.complete = AsyncMock(return_value="LLM 回复")
            brain.router.complete_with_tools = AsyncMock()

            # 配置 fact_extractor mock
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            # 配置 dispatcher mock，返回 Agent 回复
            mock_dispatcher = MagicMock()
            mock_dispatcher.try_dispatch = AsyncMock(return_value="Agent reply")
            brain.dispatcher = mock_dispatcher

            result = await brain.think("chat1", "user message")

            assert result == "Agent reply"
            brain.memory.append.assert_any_call("chat1", "assistant", "Agent reply")
            brain.router.complete.assert_not_called()

    async def test_dispatcher_none_falls_through_to_normal_chat(self):
        """dispatcher 返回 None 时，继续走正常 LLM 对话流程。"""
        with patch("src.core.brain.load_prompt", return_value="mock persona"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))

            # 配置 memory mocks
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()

            # 配置 router mock
            brain.router.complete_with_tools = AsyncMock(
                return_value=SimpleNamespace(
                    text="LLM 回复",
                    tool_calls=[],
                    continuation_message=None,
                )
            )

            # 配置 fact_extractor mock
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            # 配置 dispatcher mock，返回 None（不处理）
            mock_dispatcher = MagicMock()
            mock_dispatcher.try_dispatch = AsyncMock(return_value=None)
            brain.dispatcher = mock_dispatcher

            result = await brain.think("chat1", "user message")

            assert result == "LLM 回复"
            brain.router.complete_with_tools.assert_called_once()

    async def test_no_dispatcher_runs_normal_chat(self):
        """dispatcher 为 None 时，直接走正常 LLM 对话流程。"""
        with patch("src.core.brain.load_prompt", return_value="mock persona"), \
             patch("src.core.brain.LLMRouter"), \
             patch("src.core.brain.ConversationMemory"):
            from src.core.brain import LapwingBrain
            brain = LapwingBrain(db_path=Path("test.db"))

            # 确认 dispatcher 未设置（为 None）
            assert brain.dispatcher is None

            # 配置 memory mocks
            brain.memory.append = AsyncMock()
            brain.memory.get = AsyncMock(return_value=[])
            brain.memory.get_user_facts = AsyncMock(return_value=[])
            brain.memory.remove_last = AsyncMock()

            # 配置 router mock
            brain.router.complete_with_tools = AsyncMock(
                return_value=SimpleNamespace(
                    text="LLM 回复",
                    tool_calls=[],
                    continuation_message=None,
                )
            )

            # 配置 fact_extractor mock
            brain.fact_extractor = MagicMock()
            brain.fact_extractor.notify = MagicMock()

            result = await brain.think("chat1", "user message")

            assert result == "LLM 回复"
            brain.router.complete_with_tools.assert_called_once()
