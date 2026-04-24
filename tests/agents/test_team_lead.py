"""TeamLead Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.team_lead import TeamLead
from src.core.runtime_profiles import AGENT_TEAM_LEAD_PROFILE


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock(return_value=1)
    return router, registry, mutation_log


class TestTeamLeadCreate:
    def test_creates_with_correct_spec(self):
        router, registry, mutation_log = _make_deps()
        tl = TeamLead.create(router, registry, mutation_log)
        assert tl.spec.name == "team_lead"
        assert tl.spec.runtime_profile is AGENT_TEAM_LEAD_PROFILE
        assert "delegate_to_agent" in tl.spec.runtime_profile.tool_names
        assert tl.spec.model_slot == "agent_team_lead"

    def test_system_prompt_mentions_agents(self):
        router, registry, mutation_log = _make_deps()
        tl = TeamLead.create(router, registry, mutation_log)
        assert "researcher" in tl.spec.system_prompt.lower()
        assert "coder" in tl.spec.system_prompt.lower()

    def test_no_tell_user_capability(self):
        """Step 6 改动 3：TeamLead profile 不含 communication 能力。"""
        router, registry, mutation_log = _make_deps()
        tl = TeamLead.create(router, registry, mutation_log)
        assert "communication" not in tl.spec.runtime_profile.capabilities
        assert "tell_user" not in tl.spec.runtime_profile.tool_names
