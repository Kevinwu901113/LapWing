"""AgentRegistry 测试。"""

from unittest.mock import MagicMock

import pytest

from src.agents.exceptions import AgentSpawnError
from src.agents.registry import AgentRegistry


def _base_services():
    return {
        "dispatcher": object(),
        "tool_registry": object(),
        "llm_router": object(),
    }


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

    @pytest.mark.asyncio
    async def test_legacy_get_or_create_applies_services_override(self):
        reg = AgentRegistry()
        agent = _make_agent("researcher")
        agent._services = {}
        reg.register("researcher", agent)

        services = _base_services()
        resolved = await reg.get_or_create_instance(
            "researcher",
            services_override=services,
        )

        assert resolved is agent
        assert agent._services is services

    @pytest.mark.asyncio
    async def test_legacy_get_or_create_falls_back_to_base_required_services(self):
        reg = AgentRegistry()
        agent = _make_agent("researcher")
        reg.register("researcher", agent)

        with pytest.raises(AgentSpawnError) as exc_info:
            await reg.get_or_create_instance(
                "researcher",
                services_override={"llm_router": object()},
            )

        assert exc_info.value.missing_services == ("dispatcher", "tool_registry")

    @pytest.mark.asyncio
    async def test_legacy_get_or_create_uses_agent_required_services_attr(self):
        reg = AgentRegistry()
        agent = _make_agent("custom")
        agent.REQUIRED_SERVICES = ("custom_service",)
        reg.register("custom", agent)

        missing = await reg.preflight_check("custom", {})
        assert missing == ["custom_service"]

        resolved = await reg.get_or_create_instance(
            "custom",
            services_override={"custom_service": object()},
        )
        assert resolved is agent
