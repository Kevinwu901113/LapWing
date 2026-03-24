"""E2E 测试 — 从 brain.think() 到 dispatcher → agent → 回复的完整链路。"""

import pytest
from unittest.mock import AsyncMock, patch

from src.agents.base import AgentRegistry, AgentTask, AgentResult, BaseAgent
from src.core.dispatcher import AgentDispatcher


# ---- 测试用 Mock Agent ----

class MockSearchAgent(BaseAgent):
    # 使用 "researcher" 以匹配 _quick_match 快速路由返回的 agent 名称
    name = "researcher"
    description = "搜索信息"
    capabilities = ["web_search"]

    async def execute(self, task: AgentTask, router) -> AgentResult:
        return AgentResult(content="搜索结果：Python 很棒", needs_persona_formatting=True)


# ---- 辅助：load_prompt mock ----

def _mock_load(name):
    if name == "agent_dispatcher":
        return "mock dispatcher {available_agents} {user_message}"
    return "mock lapwing persona"


# ---- 测试 ----

@pytest.mark.asyncio
class TestE2EDispatch:

    async def test_full_dispatch_pipeline_with_registered_agent(self):
        """完整分发链路：brain.think() → dispatcher → MockSearchAgent → 人格格式化 → 返回结果。"""
        # 构建 registry 并注册 agent
        agent_registry = AgentRegistry()
        agent_registry.register(MockSearchAgent())

        # 搜索类消息走 _quick_match 快速路由，只有人格格式化一次 LLM 调用
        router = AsyncMock()
        router.complete = AsyncMock(return_value="Lapwing润色后的结果")

        # 构建 memory mock
        memory = AsyncMock()
        memory.get = AsyncMock(return_value=[])
        memory.get_user_facts = AsyncMock(return_value=[])

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            dispatcher = AgentDispatcher(
                registry=agent_registry,
                router=router,
                memory=memory,
            )
            result = await dispatcher.try_dispatch("chat1", "帮我搜索Python")

        # 验证结果是经人格格式化后的文本
        assert result == "Lapwing润色后的结果"

        # 验证 router.complete 只调用了一次（_quick_match 跳过 LLM 分类，只有 persona format）
        assert router.complete.call_count == 1

    async def test_empty_registry_bypasses_llm_completely(self):
        """空注册表时，dispatcher 直接返回 None，完全不调用 LLM（零开销验证）。"""
        # 空注册表，未注册任何 agent
        agent_registry = AgentRegistry()

        # router mock（不应被调用）
        router = AsyncMock()

        memory = AsyncMock()

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            dispatcher = AgentDispatcher(
                registry=agent_registry,
                router=router,
                memory=memory,
            )
            result = await dispatcher.try_dispatch("chat1", "你好")

        # 验证返回 None（回退到正常对话）
        assert result is None

        # 验证 router.complete 从未被调用（零开销）
        router.complete.assert_not_called()
