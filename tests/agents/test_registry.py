"""AgentRegistry 测试。"""

from unittest.mock import MagicMock

from src.agents.registry import AgentRegistry


def _make_agent(name="test"):
    agent = MagicMock()
    agent.spec = MagicMock()
    agent.spec.name = name
    agent.spec.description = f"{name} agent"
    return agent


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = _make_agent("researcher")
        reg.register("researcher", agent)
        assert reg.get("researcher") is agent

    def test_get_nonexistent(self):
        reg = AgentRegistry()
        assert reg.get("nope") is None

    def test_list_names(self):
        reg = AgentRegistry()
        reg.register("researcher", _make_agent("researcher"))
        reg.register("coder", _make_agent("coder"))
        names = reg.list_names()
        assert "researcher" in names
        assert "coder" in names

    def test_list_specs(self):
        reg = AgentRegistry()
        reg.register("researcher", _make_agent("researcher"))
        reg.register("coder", _make_agent("coder"))
        specs = reg.list_specs()
        assert len(specs) == 2
        spec_names = {s["name"] for s in specs}
        assert spec_names == {"researcher", "coder"}
