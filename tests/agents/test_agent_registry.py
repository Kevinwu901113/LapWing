"""Agent 定义注册表测试。"""

from src.agents.registry import (
    AGENT_DEFINITIONS,
    AgentDefinition,
    agent_descriptions_for_prompt,
    agent_names,
    get_agent_definition,
    list_agent_definitions,
)


class TestGetAgentDefinition:
    def test_researcher_exists(self):
        agent = get_agent_definition("researcher")
        assert agent is not None
        assert agent.name == "researcher"
        assert "web" in agent.capabilities

    def test_coder_exists(self):
        agent = get_agent_definition("coder")
        assert agent is not None
        assert "shell" in agent.capabilities
        assert "code" in agent.capabilities

    def test_browser_exists(self):
        agent = get_agent_definition("browser")
        assert agent is not None
        assert "browser" in agent.capabilities

    def test_unknown_agent_returns_none(self):
        assert get_agent_definition("nonexistent") is None

    def test_empty_string_returns_none(self):
        assert get_agent_definition("") is None


class TestListAgentDefinitions:
    def test_returns_all(self):
        agents = list_agent_definitions()
        assert len(agents) == len(AGENT_DEFINITIONS)
        assert all(isinstance(a, AgentDefinition) for a in agents)


class TestAgentNames:
    def test_contains_core_agents(self):
        names = agent_names()
        assert "researcher" in names
        assert "coder" in names
        assert "browser" in names


class TestAgentDescriptionsForPrompt:
    def test_contains_all_agents(self):
        desc = agent_descriptions_for_prompt()
        for name in AGENT_DEFINITIONS:
            assert name in desc

    def test_markdown_format(self):
        desc = agent_descriptions_for_prompt()
        assert "**researcher**" in desc


class TestAgentDefinitionIntegrity:
    def test_all_have_prompt_files(self):
        """每个 Agent 定义都指向一个 prompt 文件名。"""
        for agent in AGENT_DEFINITIONS.values():
            assert agent.system_prompt_file, f"{agent.name} 缺少 system_prompt_file"

    def test_capabilities_not_empty(self):
        """每个 Agent 至少有一个 capability。"""
        for agent in AGENT_DEFINITIONS.values():
            assert len(agent.capabilities) > 0, f"{agent.name} 没有 capabilities"

    def test_prompt_files_exist(self):
        """对应的 prompt 文件必须存在。"""
        from src.core.prompt_loader import load_prompt
        for agent in AGENT_DEFINITIONS.values():
            content = load_prompt(agent.system_prompt_file)
            assert len(content) > 50, f"{agent.name} 的 prompt 文件太短"

    def test_frozen(self):
        """AgentDefinition 是 frozen dataclass，不可修改。"""
        agent = get_agent_definition("researcher")
        try:
            agent.name = "hacked"
            assert False, "应该抛出 FrozenInstanceError"
        except AttributeError:
            pass
