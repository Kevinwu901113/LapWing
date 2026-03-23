"""BaseAgent, AgentTask, AgentResult, AgentRegistry 测试。"""
import pytest
from src.agents.base import BaseAgent, AgentTask, AgentResult, AgentRegistry


class FakeSearchAgent(BaseAgent):
    name = "researcher"
    description = "联网搜索"
    capabilities = ["搜索信息", "查找新闻"]
    async def execute(self, task, router):
        return AgentResult(content="搜索结果")


class FakeCoderAgent(BaseAgent):
    name = "coder"
    description = "写代码"
    capabilities = ["代码生成", "代码调试"]
    async def execute(self, task, router):
        return AgentResult(content="代码结果", needs_persona_formatting=False)


class TestAgentTask:
    def test_create_with_required_fields(self):
        task = AgentTask(chat_id="c1", user_message="帮我搜索Python教程")
        assert task.chat_id == "c1"
        assert task.user_message == "帮我搜索Python教程"
        assert task.history == []
        assert task.user_facts == []

    def test_create_with_all_fields(self):
        task = AgentTask(
            chat_id="c1",
            user_message="搜索",
            history=[{"role": "user", "content": "hi"}],
            user_facts=[{"fact_key": "k", "fact_value": "v"}],
        )
        assert len(task.history) == 1
        assert len(task.user_facts) == 1


class TestAgentResult:
    def test_defaults(self):
        result = AgentResult(content="结果")
        assert result.content == "结果"
        assert result.needs_persona_formatting is True
        assert result.metadata == {}

    def test_custom_formatting_flag(self):
        result = AgentResult(content="x", needs_persona_formatting=False)
        assert result.needs_persona_formatting is False

    def test_metadata(self):
        result = AgentResult(content="x", metadata={"sources": ["url1"]})
        assert result.metadata["sources"] == ["url1"]


class TestBaseAgent:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BaseAgent()

    def test_concrete_agent_has_required_attributes(self):
        agent = FakeSearchAgent()
        assert agent.name == "researcher"
        assert agent.description == "联网搜索"
        assert len(agent.capabilities) == 2

    async def test_execute_returns_agent_result(self):
        agent = FakeSearchAgent()
        task = AgentTask(chat_id="c1", user_message="搜索")
        result = await agent.execute(task, router=None)
        assert isinstance(result, AgentResult)
        assert result.content == "搜索结果"


class TestAgentRegistry:
    @pytest.fixture
    def registry(self):
        r = AgentRegistry()
        r.register(FakeSearchAgent())
        r.register(FakeCoderAgent())
        return r

    def test_register_and_get_by_name(self, registry):
        agent = registry.get_by_name("researcher")
        assert agent is not None
        assert agent.name == "researcher"

    def test_get_by_name_not_found(self, registry):
        assert registry.get_by_name("nonexistent") is None

    def test_list_all(self, registry):
        agents = registry.list_all()
        assert len(agents) == 2

    def test_as_descriptions(self, registry):
        descs = registry.as_descriptions()
        assert len(descs) == 2
        assert all("name" in d and "description" in d and "capabilities" in d for d in descs)

    def test_is_empty_true_when_no_agents(self):
        r = AgentRegistry()
        assert r.is_empty() is True

    def test_is_empty_false_when_agents_exist(self, registry):
        assert registry.is_empty() is False

    def test_duplicate_register_overwrites(self):
        r = AgentRegistry()
        r.register(FakeSearchAgent())
        r.register(FakeSearchAgent())
        assert len(r.list_all()) == 1
