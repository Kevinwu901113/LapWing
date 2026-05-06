"""Phase 8B-3 tests: trust root operator profile and permission gating.

Verifies CAPABILITY_TRUST_OPERATOR_PROFILE grants access to trust root tools
and that no other profile (standard/default/chat/local_execution/browser/
identity/import/lifecycle/curator/candidate) grants access.
"""

from __future__ import annotations

import pytest

from src.core.runtime_profiles import (
    CAPABILITY_TRUST_OPERATOR_PROFILE,
    RuntimeProfile,
    get_runtime_profile,
)


class _FakeRegistry:
    def __init__(self):
        self._t: dict[str, object] = {}

    def register(self, spec):
        self._t[spec.name] = spec

    def get(self, name: str):
        return self._t.get(name)

    def list_tools(self, *, capabilities=None, include_internal=False):
        if capabilities is None:
            return list(self._t.values())
        result = []
        for spec in self._t.values():
            if spec.capability in capabilities or any(
                c in capabilities for c in getattr(spec, "capabilities", ())
            ):
                result.append(spec)
        return result

    def get_tools_for_profile(self, profile, *, include_internal=False):
        if profile.tool_names:
            return [self._t[n] for n in profile.tool_names if n in self._t]
        if profile.capabilities:
            return self.list_tools(capabilities=set(profile.capabilities), include_internal=include_internal)
        return []

    def function_tools_for_profile(self, profile):
        return [
            {"function": {"name": s.name}}
            for s in self.get_tools_for_profile(profile)
        ]


@pytest.fixture
def registry_with_trust_tools():
    """Fake registry with 5 trust root tools + some other tools."""
    from src.tools.capability_tools import register_capability_trust_root_tools
    from src.capabilities.trust_roots import TrustRootStore
    from tempfile import mkdtemp
    from pathlib import Path

    reg = _FakeRegistry()

    # Register a few non-trust tools first
    from src.tools.types import ToolSpec
    reg.register(ToolSpec(
        name="list_capabilities",
        description="List capabilities",
        json_schema={},
        executor=lambda r, c: None,
        capability="capability_read",
    ))
    reg.register(ToolSpec(
        name="delegate_to_researcher",
        description="Delegate to researcher",
        json_schema={},
        executor=lambda r, c: None,
        capability="agent",
    ))

    # Register trust root tools
    import tempfile
    store_dir = Path(tempfile.mkdtemp())
    store = TrustRootStore(data_dir=store_dir)
    register_capability_trust_root_tools(reg, store)
    return reg


# ── Profile structure ─────────────────────────────────────────────────


class TestTrustOperatorProfile:
    def test_profile_exists(self):
        profile = get_runtime_profile("capability_trust_operator")
        assert profile is not None
        assert profile.name == "capability_trust_operator"

    def test_profile_has_capability_trust_operator_tag(self):
        profile = get_runtime_profile("capability_trust_operator")
        assert "capability_trust_operator" in profile.capabilities

    def test_profile_has_no_tool_names(self):
        """Operator profiles use capability tags, not explicit tool names."""
        profile = get_runtime_profile("capability_trust_operator")
        assert not profile.tool_names

    def test_profile_no_shell_policy(self):
        profile = get_runtime_profile("capability_trust_operator")
        assert profile.shell_policy_enabled is False

    def test_profile_no_internal_tools(self):
        profile = get_runtime_profile("capability_trust_operator")
        assert profile.include_internal is False


# ── Profile grants access to trust root tools ─────────────────────────


TRUST_ROOT_TOOL_NAMES = {
    "list_capability_trust_roots",
    "view_capability_trust_root",
    "add_capability_trust_root",
    "disable_capability_trust_root",
    "revoke_capability_trust_root",
}


class TestTrustOperatorProfileGrants:
    def test_profile_accesses_all_trust_tools(self, registry_with_trust_tools):
        profile = get_runtime_profile("capability_trust_operator")
        specs = registry_with_trust_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        assert TRUST_ROOT_TOOL_NAMES.issubset(names)

    def test_profile_does_not_grant_non_trust_tools(self, registry_with_trust_tools):
        profile = get_runtime_profile("capability_trust_operator")
        specs = registry_with_trust_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        # Only trust operator tools should be in the result
        for name in names:
            assert name in TRUST_ROOT_TOOL_NAMES, f"{name} should not be in trust operator profile"


# ── Other profiles do NOT grant access ────────────────────────────────


STANDARD_PROFILE_NAMES = [
    "standard",
    "chat_shell",
    "zero_tools",
    "inner_tick",
    "local_execution",
    "compose_proactive",
]

OPERATOR_PROFILE_NAMES = [
    "agent_admin_operator",
    "capability_lifecycle_operator",
    "capability_curator_operator",
    "identity_operator",
    "browser_operator",
    "skill_operator",
    "agent_candidate_operator",
    "capability_import_operator",
]


class TestOtherProfilesDenied:
    @pytest.mark.parametrize("profile_name", STANDARD_PROFILE_NAMES)
    def test_standard_profile_denied(self, registry_with_trust_tools, profile_name):
        profile = get_runtime_profile(profile_name)
        specs = registry_with_trust_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in TRUST_ROOT_TOOL_NAMES:
            assert tool_name not in names, f"{tool_name} should not be in {profile_name}"

    @pytest.mark.parametrize("profile_name", OPERATOR_PROFILE_NAMES)
    def test_other_operator_profile_denied(self, registry_with_trust_tools, profile_name):
        profile = get_runtime_profile(profile_name)
        specs = registry_with_trust_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in TRUST_ROOT_TOOL_NAMES:
            assert tool_name not in names, f"{tool_name} should not be in {profile_name}"


class TestLocalExecutionDenied:
    def test_local_execution_denied(self, registry_with_trust_tools):
        profile = get_runtime_profile("local_execution")
        specs = registry_with_trust_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in TRUST_ROOT_TOOL_NAMES:
            assert tool_name not in names

    def test_task_execution_alias_denied(self, registry_with_trust_tools):
        profile = get_runtime_profile("task_execution")
        specs = registry_with_trust_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in TRUST_ROOT_TOOL_NAMES:
            assert tool_name not in names
