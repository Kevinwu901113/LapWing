"""Researcher Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.researcher import Researcher


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")
    return router, registry, dispatcher


class TestResearcherCreate:
    def test_creates_with_correct_spec(self):
        router, registry, dispatcher = _make_deps()
        r = Researcher.create(router, registry, dispatcher)
        assert r.spec.name == "researcher"
        assert "web_search" in r.spec.tools
        assert "web_fetch" in r.spec.tools
        assert r.spec.model_slot == "agent_execution"

    def test_system_prompt_mentions_sources(self):
        router, registry, dispatcher = _make_deps()
        r = Researcher.create(router, registry, dispatcher)
        assert "来源" in r.spec.system_prompt or "URL" in r.spec.system_prompt
