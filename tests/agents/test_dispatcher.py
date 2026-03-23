"""AgentDispatcher 测试。"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.base import AgentRegistry, AgentTask, AgentResult, BaseAgent
from src.core.dispatcher import AgentDispatcher


# ---- 测试用 Fake Agent ----

class FakeResearcherAgent(BaseAgent):
    name = "researcher"
    description = "联网搜索信息"
    capabilities = ["搜索信息", "查找新闻"]

    async def execute(self, task: AgentTask, router) -> AgentResult:
        return AgentResult(content="搜索结果：xxx", needs_persona_formatting=True)


class FakeCoderAgent(BaseAgent):
    name = "coder"
    description = "写代码"
    capabilities = ["代码生成", "代码调试"]

    async def execute(self, task: AgentTask, router) -> AgentResult:
        return AgentResult(content="```python\nprint('hello')\n```", needs_persona_formatting=False)


# ---- 辅助构造器 ----

def _mock_load(name):
    if name == "agent_dispatcher":
        return "mock dispatcher {available_agents} {user_message}"
    return "mock lapwing persona"


def make_dispatcher(registry=None, router=None, memory=None):
    if registry is None:
        registry = AgentRegistry()
    if router is None:
        router = AsyncMock()
    if memory is None:
        memory = AsyncMock()
        memory.get = AsyncMock(return_value=[])
        memory.get_user_facts = AsyncMock(return_value=[])
    with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
        return AgentDispatcher(registry=registry, router=router, memory=memory)


# ---- 测试类 ----

class TestAgentDispatcher:

    # 1. 注册表为空时立即返回 None，不调用 router
    async def test_returns_none_when_registry_empty(self):
        router = AsyncMock()
        dispatcher = make_dispatcher(registry=AgentRegistry(), router=router)
        result = await dispatcher.try_dispatch("chat1", "帮我搜索一下Python教程")
        assert result is None
        router.complete.assert_not_called()

    # 2. _classify 返回 None 时，try_dispatch 返回 None
    async def test_returns_none_when_classify_returns_null(self):
        registry = AgentRegistry()
        registry.register(FakeResearcherAgent())

        router = AsyncMock()
        router.complete = AsyncMock(return_value='{"agent": null}')

        dispatcher = make_dispatcher(registry=registry, router=router)
        result = await dispatcher.try_dispatch("chat1", "今天天气怎么样")
        assert result is None

    # 3. classify 返回 agent name → agent.execute 被调用 → needs_persona_formatting=True → 返回人格格式化结果
    async def test_routes_to_agent_and_returns_formatted_result(self):
        registry = AgentRegistry()
        agent = FakeResearcherAgent()
        registry.register(agent)

        router = AsyncMock()
        # 第一次调用：分类，第二次调用：人格格式化
        router.complete = AsyncMock(side_effect=[
            '{"agent": "researcher", "reason": "用户需要搜索"}',
            "这是经过人格格式化后的搜索结果。",
        ])

        dispatcher = make_dispatcher(registry=registry, router=router)

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            result = await dispatcher.try_dispatch("chat1", "帮我搜索一下Python教程")

        assert result == "这是经过人格格式化后的搜索结果。"
        assert router.complete.call_count == 2

    # 4. needs_persona_formatting=False → 直接返回 content，不调用 _format_with_persona
    async def test_routes_to_agent_without_persona_format(self):
        registry = AgentRegistry()
        registry.register(FakeCoderAgent())

        router = AsyncMock()
        router.complete = AsyncMock(return_value='{"agent": "coder", "reason": "用户要写代码"}')

        dispatcher = make_dispatcher(registry=registry, router=router)

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            result = await dispatcher.try_dispatch("chat1", "帮我写一个Python脚本")

        assert result == "```python\nprint('hello')\n```"
        # 只调用了一次（分类），没有调用人格格式化
        assert router.complete.call_count == 1

    # 5. classify 返回未知 agent 名称 → 返回 None
    async def test_returns_none_when_agent_not_found(self):
        registry = AgentRegistry()
        registry.register(FakeResearcherAgent())

        router = AsyncMock()
        router.complete = AsyncMock(return_value='{"agent": "nonexistent_agent"}')

        dispatcher = make_dispatcher(registry=registry, router=router)

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            result = await dispatcher.try_dispatch("chat1", "帮我做点什么")

        assert result is None

    # 6. router.complete 抛出异常 → try_dispatch 返回 None（不崩溃）
    async def test_returns_none_on_llm_failure(self):
        registry = AgentRegistry()
        registry.register(FakeResearcherAgent())

        router = AsyncMock()
        router.complete = AsyncMock(side_effect=Exception("API timeout"))

        dispatcher = make_dispatcher(registry=registry, router=router)

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            result = await dispatcher.try_dispatch("chat1", "帮我搜索一下")

        assert result is None

    # 7. agent.execute 抛出异常 → try_dispatch 返回 None（不崩溃）
    async def test_returns_none_on_agent_execute_failure(self):
        registry = AgentRegistry()

        failing_agent = MagicMock(spec=BaseAgent)
        failing_agent.name = "researcher"
        failing_agent.description = "联网搜索"
        failing_agent.capabilities = ["搜索"]
        failing_agent.execute = AsyncMock(side_effect=RuntimeError("Agent crashed"))
        registry.register(failing_agent)

        router = AsyncMock()
        router.complete = AsyncMock(return_value='{"agent": "researcher"}')

        dispatcher = make_dispatcher(registry=registry, router=router)

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            result = await dispatcher.try_dispatch("chat1", "帮我搜索一下")

        assert result is None

    # 8. 传递给 agent 的 AgentTask 包含 history 和 user_facts
    async def test_task_includes_history_and_facts(self):
        registry = AgentRegistry()

        captured_task = {}

        class CapturingAgent(BaseAgent):
            name = "researcher"
            description = "搜索"
            capabilities = ["搜索"]

            async def execute(self, task: AgentTask, router) -> AgentResult:
                captured_task["task"] = task
                return AgentResult(content="结果", needs_persona_formatting=False)

        registry.register(CapturingAgent())

        history = [{"role": "user", "content": "之前说过的话"}]
        facts = [{"fact_key": "name", "fact_value": "Kevin"}]

        memory = AsyncMock()
        memory.get = AsyncMock(return_value=history)
        memory.get_user_facts = AsyncMock(return_value=facts)

        router = AsyncMock()
        router.complete = AsyncMock(return_value='{"agent": "researcher"}')

        dispatcher = make_dispatcher(registry=registry, router=router, memory=memory)

        with patch("src.core.dispatcher.load_prompt", side_effect=_mock_load):
            await dispatcher.try_dispatch("chat1", "帮我搜索")

        task = captured_task["task"]
        assert task.history == history
        assert task.user_facts == facts
        assert task.chat_id == "chat1"
        assert task.user_message == "帮我搜索"

    # 9. _parse_decision 正常解析 agent 名称
    def test_parse_decision_returns_agent_name(self):
        dispatcher = make_dispatcher()
        result = dispatcher._parse_decision('{"agent": "researcher"}')
        assert result == "researcher"

    # 10. _parse_decision 返回 None（agent 为 null）
    def test_parse_decision_returns_none_for_null(self):
        dispatcher = make_dispatcher()
        result = dispatcher._parse_decision('{"agent": null}')
        assert result is None

    # 11. _parse_decision 正确处理 markdown 代码块包装
    def test_parse_decision_strips_code_fence(self):
        dispatcher = make_dispatcher()
        raw = '```json\n{"agent": "coder", "reason": "用户要写代码"}\n```'
        result = dispatcher._parse_decision(raw)
        assert result == "coder"

    # 12. _parse_decision 对格式错误的 JSON 返回 None
    def test_parse_decision_returns_none_for_malformed_json(self):
        dispatcher = make_dispatcher()
        result = dispatcher._parse_decision("这不是JSON")
        assert result is None
