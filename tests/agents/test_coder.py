"""Coder Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

from src.agents.coder import Coder


def _make_deps():
    router = MagicMock()
    registry = MagicMock()
    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value="evt_001")
    return router, registry, dispatcher


class TestCoderCreate:
    def test_creates_with_correct_spec(self):
        router, registry, dispatcher = _make_deps()
        c = Coder.create(router, registry, dispatcher)
        assert c.spec.name == "coder"
        assert "ws_file_write" in c.spec.tools
        assert "ws_file_read" in c.spec.tools
        assert "ws_file_list" in c.spec.tools
        assert "execute_shell" in c.spec.tools

    def test_system_prompt_mentions_workspace(self):
        router, registry, dispatcher = _make_deps()
        c = Coder.create(router, registry, dispatcher)
        assert "agent_workspace" in c.spec.system_prompt
