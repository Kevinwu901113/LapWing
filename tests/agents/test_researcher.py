"""Researcher Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.researcher import Researcher
from src.core.runtime_profiles import AGENT_RESEARCHER_PROFILE


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock(return_value=1)
    return router, registry, mutation_log


class TestResearcherCreate:
    def test_creates_with_correct_spec(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert r.spec.name == "researcher"
        assert r.spec.runtime_profile is AGENT_RESEARCHER_PROFILE
        assert "research" in r.spec.runtime_profile.tool_names
        assert "browse" in r.spec.runtime_profile.tool_names
        assert r.spec.model_slot == "agent_execution"

    def test_system_prompt_mentions_sources(self):
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert "来源" in r.spec.system_prompt or "URL" in r.spec.system_prompt

    def test_no_tell_user_capability(self):
        """Step 6 改动 3：Researcher profile 不含 communication 能力。"""
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert "communication" not in r.spec.runtime_profile.capabilities
        assert "tell_user" not in r.spec.runtime_profile.tool_names
