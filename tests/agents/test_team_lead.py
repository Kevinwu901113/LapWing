"""TeamLead Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.team_lead import TeamLead


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")
    return router, registry, dispatcher


class TestTeamLeadCreate:
    def test_creates_with_correct_spec(self):
        router, registry, dispatcher = _make_deps()
        tl = TeamLead.create(router, registry, dispatcher)
        assert tl.spec.name == "team_lead"
        assert "delegate_to_agent" in tl.spec.tools
        assert tl.spec.model_slot == "agent_execution"

    def test_system_prompt_mentions_agents(self):
        router, registry, dispatcher = _make_deps()
        tl = TeamLead.create(router, registry, dispatcher)
        assert "researcher" in tl.spec.system_prompt.lower()
        assert "coder" in tl.spec.system_prompt.lower()
