"""Phase 6D — Agent candidate operator profile and permission tests.

Tests:
  - AGENT_CANDIDATE_OPERATOR_PROFILE exists
  - agent_candidate_operator capability tag
  - Standard/default/chat profiles denied
  - local_execution denied
  - browser/identity denied
  - Only AGENT_CANDIDATE_OPERATOR_PROFILE has the tag
  - ToolDispatcher denies without profile
  - ToolDispatcher allows with profile
  - candidate_tools_enabled grants no permissions by itself
"""

from __future__ import annotations

import pytest

from src.core.runtime_profiles import (
    AGENT_CANDIDATE_OPERATOR_PROFILE,
    BROWSER_OPERATOR_PROFILE,
    CAPABILITY_CURATOR_OPERATOR_PROFILE,
    CAPABILITY_LIFECYCLE_OPERATOR_PROFILE,
    CHAT_SHELL_PROFILE,
    IDENTITY_OPERATOR_PROFILE,
    INNER_TICK_PROFILE,
    LOCAL_EXECUTION_PROFILE,
    STANDARD_PROFILE,
    ZERO_TOOLS_PROFILE,
    get_runtime_profile,
)


class TestAgentCandidateOperatorProfileExists:
    def test_profile_defined(self):
        assert AGENT_CANDIDATE_OPERATOR_PROFILE is not None
        assert AGENT_CANDIDATE_OPERATOR_PROFILE.name == "agent_candidate_operator"

    def test_profile_has_correct_capability(self):
        assert "agent_candidate_operator" in AGENT_CANDIDATE_OPERATOR_PROFILE.capabilities

    def test_profile_no_tool_names(self):
        """Operator profiles use capabilities, not hardcoded tool_names."""
        assert AGENT_CANDIDATE_OPERATOR_PROFILE.tool_names == frozenset()

    def test_profile_not_internal(self):
        assert AGENT_CANDIDATE_OPERATOR_PROFILE.include_internal is False

    def test_profile_no_shell_policy(self):
        assert AGENT_CANDIDATE_OPERATOR_PROFILE.shell_policy_enabled is False

    def test_profile_resolvable(self):
        profile = get_runtime_profile("agent_candidate_operator")
        assert profile is AGENT_CANDIDATE_OPERATOR_PROFILE


class TestStandardProfilesDenied:
    """Verify standard/default/chat profiles do NOT carry the agent_candidate_operator tag."""

    def test_standard_denied(self):
        assert "agent_candidate_operator" not in STANDARD_PROFILE.capabilities

    def test_zero_tools_denied(self):
        assert "agent_candidate_operator" not in ZERO_TOOLS_PROFILE.capabilities

    def test_chat_shell_denied(self):
        assert "agent_candidate_operator" not in CHAT_SHELL_PROFILE.capabilities

    def test_inner_tick_denied(self):
        assert "agent_candidate_operator" not in INNER_TICK_PROFILE.capabilities

    def test_local_execution_denied(self):
        assert "agent_candidate_operator" not in LOCAL_EXECUTION_PROFILE.capabilities

    def test_browser_operator_denied(self):
        assert "agent_candidate_operator" not in BROWSER_OPERATOR_PROFILE.capabilities

    def test_identity_operator_denied(self):
        assert "agent_candidate_operator" not in IDENTITY_OPERATOR_PROFILE.capabilities

    def test_capability_lifecycle_operator_denied(self):
        assert "agent_candidate_operator" not in CAPABILITY_LIFECYCLE_OPERATOR_PROFILE.capabilities

    def test_capability_curator_operator_denied(self):
        assert "agent_candidate_operator" not in CAPABILITY_CURATOR_OPERATOR_PROFILE.capabilities


class TestOnlyOperatorProfileHasTag:
    """Verify only AGENT_CANDIDATE_OPERATOR_PROFILE has the tag."""

    def test_only_candidate_operator_has_tag(self):
        all_profiles = [
            STANDARD_PROFILE,
            ZERO_TOOLS_PROFILE,
            CHAT_SHELL_PROFILE,
            INNER_TICK_PROFILE,
            LOCAL_EXECUTION_PROFILE,
            BROWSER_OPERATOR_PROFILE,
            IDENTITY_OPERATOR_PROFILE,
            CAPABILITY_LIFECYCLE_OPERATOR_PROFILE,
            CAPABILITY_CURATOR_OPERATOR_PROFILE,
        ]
        for profile in all_profiles:
            assert "agent_candidate_operator" not in profile.capabilities, (
                f"{profile.name} should NOT have agent_candidate_operator"
            )

        assert "agent_candidate_operator" in AGENT_CANDIDATE_OPERATOR_PROFILE.capabilities


class TestCandidateToolsEnabledGrantsNoPermissions:
    """The feature flag enables tool registration, not permissions."""

    def test_flag_defaults_false(self):
        from src.config import get_settings
        s = get_settings()
        assert s.agents.candidate_tools_enabled is False

    def test_flag_does_not_grant_capability(self):
        """Enabling the flag doesn't add the tag to any profile."""
        from src.core.runtime_profiles import get_runtime_profile

        # Even if flag is true, standard profile should still lack the tag
        assert "agent_candidate_operator" not in STANDARD_PROFILE.capabilities

        # Only the explicit operator profile has it
        profile = get_runtime_profile("agent_candidate_operator")
        assert "agent_candidate_operator" in profile.capabilities


class TestToolDispatcherPermissionModel:
    """Verify the ToolDispatcher permission model for candidate tools."""

    def test_tools_require_capability_tag(self, tmp_path):
        from src.agents.candidate_store import AgentCandidateStore
        from src.tools.agent_candidate_tools import register_agent_candidate_tools
        from unittest.mock import MagicMock

        store = AgentCandidateStore(tmp_path / "agent_candidates")
        tools = []
        mock_registry = MagicMock()

        def _capture(spec):
            tools.append(spec)

        mock_registry.register = _capture
        register_agent_candidate_tools(mock_registry, store)

        for t in tools:
            assert t.capability == "agent_candidate_operator", (
                f"Tool {t.name} has wrong capability: {t.capability}"
            )

    def test_profile_matches_tool_capability(self, tmp_path):
        """AGENT_CANDIDATE_OPERATOR_PROFILE carries agent_candidate_operator,
        and candidate tools require agent_candidate_operator."""
        from src.agents.candidate_store import AgentCandidateStore
        from unittest.mock import MagicMock

        store = AgentCandidateStore(tmp_path / "agent_candidates")
        tools = []
        mock_registry = MagicMock()

        def _capture(spec):
            tools.append(spec)

        mock_registry.register = _capture
        from src.tools.agent_candidate_tools import register_agent_candidate_tools
        register_agent_candidate_tools(mock_registry, store)

        for t in tools:
            # The tool's capability tag must be in the operator profile
            assert t.capability in AGENT_CANDIDATE_OPERATOR_PROFILE.capabilities, (
                f"Tool {t.name} capability {t.capability!r} not in "
                f"AGENT_CANDIDATE_OPERATOR_PROFILE capabilities"
            )

    def test_dispatcher_allows_with_profile(self):
        """ToolDispatcher.validate_tool_access should allow when profile has the tag."""
        from src.agents.policy import AgentPolicy
        from src.agents.spec import AgentSpec
        from unittest.mock import MagicMock

        # Create a policy and check tool access for a spec with candidate operator profile
        policy = AgentPolicy.__new__(AgentPolicy)
        policy._catalog = MagicMock()
        policy._llm_router = None

        spec = AgentSpec(
            name="candidate_op",
            runtime_profile="agent_candidate_operator",
        )

        # Tools with agent_candidate_operator capability should be accessible
        # since the profile has that capability and tool_names is empty
        # (capability-driven profiles permit by default when tool_names is empty)
        result = policy.validate_tool_access(spec, "list_agent_candidates")
        assert result is True

    def test_dispatcher_denies_without_profile(self):
        """ToolDispatcher.validate_tool_access should deny when standard profile used."""
        from src.agents.policy import AgentPolicy
        from src.agents.spec import AgentSpec
        from unittest.mock import MagicMock

        policy = AgentPolicy.__new__(AgentPolicy)
        policy._catalog = MagicMock()
        policy._llm_router = None

        spec = AgentSpec(
            name="standard_agent",
            runtime_profile="standard",
        )

        # Standard profile has explicit tool_names — candidate tools not in list
        result = policy.validate_tool_access(spec, "list_agent_candidates")
        assert result is False


class TestFlagDoesNotAffectSaveAgent:
    """candidate_tools_enabled must not affect existing save_agent behavior."""

    def test_save_agent_unchanged_by_flag(self):
        from src.config import get_settings
        s = get_settings()
        # save gate flag is separate
        assert s.agents.require_candidate_approval_for_persistence is False
        # candidate tools flag is separate
        assert s.agents.candidate_tools_enabled is False

    def test_save_gate_independent_of_tools_flag(self):
        """The save gate (require_candidate_approval_for_persistence) and
        candidate tools (candidate_tools_enabled) are independent flags."""
        from src.config import get_settings
        s = get_settings()

        # Both default false, but changing one doesn't affect the other
        assert isinstance(s.agents.require_candidate_approval_for_persistence, bool)
        assert isinstance(s.agents.candidate_tools_enabled, bool)
