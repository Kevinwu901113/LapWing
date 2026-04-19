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
        assert "execute_shell" in whitelist

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
