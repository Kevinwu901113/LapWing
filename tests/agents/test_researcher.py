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
        assert r.spec.model_slot == "agent_researcher"

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


class TestResearcherConfig:
    """修复 C：max_rounds / timeout / tokens 来自 config，不再硬编码。"""

    def test_reads_overrides_from_settings(self, monkeypatch):
        from src.agents import researcher as researcher_module
        from src.config.settings import (
            AgentRoleConfig, AgentTeamConfig, LapwingSettings,
        )

        def _fake_get_settings():
            base = LapwingSettings()
            base.agent_team = AgentTeamConfig(
                enabled=True,
                researcher=AgentRoleConfig(
                    max_rounds=5, timeout_seconds=42, max_tokens=1024,
                ),
            )
            return base

        monkeypatch.setattr(researcher_module, "get_settings", _fake_get_settings)

        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        assert r.spec.max_rounds == 5
        assert r.spec.timeout_seconds == 42
        assert r.spec.max_tokens == 1024

    def test_defaults_when_no_override(self):
        """没有覆盖时，spec 的值与 AgentRoleConfig 默认值一致。"""
        router, registry, mutation_log = _make_deps()
        r = Researcher.create(router, registry, mutation_log)
        # 与 config.toml [agent_team.researcher] 默认值一致
        assert r.spec.max_rounds == 15
        assert r.spec.timeout_seconds == 300
        assert r.spec.max_tokens == 40000
