"""chat_tools is now a wrapper around COMPOSE_PROACTIVE_PROFILE.

Locks in the centralization contract from commit 7:
- The always-on tool surface lives in COMPOSE_PROACTIVE_PROFILE.tool_names
- TaskRuntime.chat_tools() resolves through that profile + adds dynamic
  capabilities (shell / web / browser / ambient) on top.
- Removing a name from the profile removes it from chat_tools output.
- The profile is registered in _PROFILES so other systems can resolve it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.runtime_profiles import (
    COMPOSE_PROACTIVE_PROFILE,
    _PROFILES,
    get_runtime_profile,
)
from src.tools.types import ToolExecutionRequest, ToolExecutionResult, ToolSpec


async def _noop_executor(req: ToolExecutionRequest, ctx) -> ToolExecutionResult:
    return ToolExecutionResult(success=True, payload={})


def _registry_with_compose_proactive_tools():
    """Register every tool the COMPOSE_PROACTIVE_PROFILE references plus
    enough extras to exercise the conditional add paths."""
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for name in COMPOSE_PROACTIVE_PROFILE.tool_names:
        registry.register(ToolSpec(
            name=name,
            description=name,
            json_schema={"type": "object", "properties": {}},
            executor=_noop_executor,
            capability="general",
        ))
    # Dynamic additions — shell + web + browser
    for name in ("execute_shell", "read_file", "write_file"):
        registry.register(ToolSpec(
            name=name, description=name,
            json_schema={"type": "object", "properties": {}},
            executor=_noop_executor, capability="shell",
        ))
    for name in ("research", "browse"):
        registry.register(ToolSpec(
            name=name, description=name,
            json_schema={"type": "object", "properties": {}},
            executor=_noop_executor, capability="web",
        ))
    return registry


class TestProfileExists:
    def test_compose_proactive_profile_registered(self):
        assert get_runtime_profile("compose_proactive") is COMPOSE_PROACTIVE_PROFILE
        assert "compose_proactive" in _PROFILES

    def test_profile_lists_companion_surface_tools(self):
        names = COMPOSE_PROACTIVE_PROFILE.tool_names
        # Talking surface
        assert "send_message" in names
        # Reminders
        assert "set_reminder" in names
        assert "view_reminders" in names
        assert "cancel_reminder" in names
        # Delegation — agents-as-tools refactor: chat surface uses
        # specific delegates, not the generic delegate_to_agent
        assert "delegate_to_researcher" in names
        assert "delegate_to_coder" in names
        assert "delegate_to_agent" not in names
        assert "list_agents" not in names
        # Commitments
        assert "commit_promise" in names
        assert "fulfill_promise" in names
        assert "abandon_promise" in names
        # Planning
        assert "plan_task" in names
        assert "update_plan" in names
        # Focus + corrections
        assert "close_focus" in names
        assert "recall_focus" in names
        assert "add_correction" in names

    def test_profile_excludes_shell_and_raw_research(self):
        """Shell + raw research are dynamic capabilities, layered on by
        chat_tools() based on caller flags. They must not be hard-coded
        into the profile."""
        names = COMPOSE_PROACTIVE_PROFILE.tool_names
        assert "execute_shell" not in names
        assert "research" not in names
        assert "browse" not in names


class TestChatToolsResolvesViaProfile:
    @pytest.mark.asyncio
    async def test_chat_tools_returns_profile_names_when_no_flags(self):
        """With shell+web disabled, chat_tools output is exactly the
        profile names that are registered."""
        from src.core.task_runtime import TaskRuntime

        registry = _registry_with_compose_proactive_tools()
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        tools = runtime.chat_tools(shell_enabled=False, web_enabled=False)
        names = {item["function"]["name"] for item in tools}
        # Subset of profile names that survived registration
        assert names == set(COMPOSE_PROACTIVE_PROFILE.tool_names)

    @pytest.mark.asyncio
    async def test_chat_tools_layers_shell_on_top(self):
        from src.core.task_runtime import TaskRuntime

        registry = _registry_with_compose_proactive_tools()
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        tools = runtime.chat_tools(shell_enabled=True, web_enabled=False)
        names = {item["function"]["name"] for item in tools}
        # Profile names + execute_shell + read/write_file
        for n in ("execute_shell", "read_file", "write_file"):
            assert n in names
        # And the profile names are still there
        assert "send_message" in names
        assert "delegate_to_researcher" in names

    @pytest.mark.asyncio
    async def test_chat_tools_layers_web_on_top(self):
        from src.core.task_runtime import TaskRuntime

        registry = _registry_with_compose_proactive_tools()
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)
        tools = runtime.chat_tools(shell_enabled=False, web_enabled=True)
        names = {item["function"]["name"] for item in tools}
        assert "research" in names
        assert "browse" in names

    @pytest.mark.asyncio
    async def test_removing_a_name_from_profile_removes_it_from_chat_tools(
        self, monkeypatch
    ):
        """Source of truth is the profile — so monkeypatching the
        profile's tool_names changes chat_tools output."""
        from src.core import runtime_profiles
        from src.core.runtime_profiles import RuntimeProfile
        from src.core.task_runtime import TaskRuntime

        registry = _registry_with_compose_proactive_tools()
        runtime = TaskRuntime(router=MagicMock(), tool_registry=registry)

        # Drop send_message from the profile and confirm it disappears.
        smaller = RuntimeProfile(
            name="compose_proactive",
            capabilities=frozenset(),
            tool_names=COMPOSE_PROACTIVE_PROFILE.tool_names - {"send_message"},
        )
        monkeypatch.setattr(runtime_profiles, "COMPOSE_PROACTIVE_PROFILE", smaller)
        # task_runtime imports the symbol at module level, so patch there too.
        from src.core import task_runtime as tr
        monkeypatch.setattr(tr, "COMPOSE_PROACTIVE_PROFILE", smaller)

        tools = runtime.chat_tools(shell_enabled=False, web_enabled=False)
        names = {item["function"]["name"] for item in tools}
        assert "send_message" not in names
        # Sibling tools are still present
        assert "set_reminder" in names
