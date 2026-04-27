"""Coder Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.coder import Coder
from src.core.runtime_profiles import AGENT_CODER_PROFILE


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    mutation_log = AsyncMock()
    mutation_log.record = AsyncMock(return_value=1)
    return router, registry, mutation_log


class TestCoderCreate:
    def test_creates_with_correct_spec(self):
        router, registry, mutation_log = _make_deps()
        c = Coder.create(router, registry, mutation_log)
        assert c.spec.name == "coder"
        assert c.spec.runtime_profile is AGENT_CODER_PROFILE
        whitelist = c.spec.runtime_profile.tool_names
        assert "ws_file_write" in whitelist
        assert "ws_file_read" in whitelist
        assert "ws_file_list" in whitelist
        assert "run_python_code" in whitelist
        assert "execute_shell" not in whitelist

    def test_system_prompt_mentions_workspace(self):
        router, registry, mutation_log = _make_deps()
        c = Coder.create(router, registry, mutation_log)
        assert "agent_workspace" in c.spec.system_prompt

    def test_no_tell_user_capability(self):
        """Step 6 改动 3：Coder profile 不含 communication 能力。"""
        router, registry, mutation_log = _make_deps()
        c = Coder.create(router, registry, mutation_log)
        assert "communication" not in c.spec.runtime_profile.capabilities
        assert "tell_user" not in c.spec.runtime_profile.tool_names


class TestCoderConfig:
    """修复 C：max_rounds / timeout / tokens 来自 config，不再硬编码。"""

    def test_reads_overrides_from_settings(self, monkeypatch):
        from src.agents import coder as coder_module
        from src.config.settings import (
            AgentRoleConfig, AgentTeamConfig, LapwingSettings,
        )

        def _fake_get_settings():
            base = LapwingSettings()
            base.agent_team = AgentTeamConfig(
                enabled=True,
                coder=AgentRoleConfig(
                    max_rounds=7, timeout_seconds=99, max_tokens=2048,
                ),
            )
            return base

        monkeypatch.setattr(coder_module, "get_settings", _fake_get_settings)

        router, registry, mutation_log = _make_deps()
        c = Coder.create(router, registry, mutation_log)
        assert c.spec.max_rounds == 7
        assert c.spec.timeout_seconds == 99
        assert c.spec.max_tokens == 2048

    def test_defaults_when_no_override(self):
        router, registry, mutation_log = _make_deps()
        c = Coder.create(router, registry, mutation_log)
        # 与 config.toml [agent_team.coder] 默认值一致
        assert c.spec.max_rounds == 20
        assert c.spec.timeout_seconds == 600
        assert c.spec.max_tokens == 50000
