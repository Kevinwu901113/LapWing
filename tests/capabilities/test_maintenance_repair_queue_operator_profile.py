"""Maintenance C tests: repair queue operator profile and permission gating.

Verifies CAPABILITY_REPAIR_OPERATOR_PROFILE grants access to repair queue tools
and that no other profile (standard/default/chat/local_execution/browser/
identity/import/lifecycle/curator/candidate/trust) grants access.
"""

from __future__ import annotations

import pytest

from src.core.runtime_profiles import (
    CAPABILITY_REPAIR_OPERATOR_PROFILE,
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
def registry_with_repair_tools():
    """Fake registry with 6 repair queue tools + some other tools."""
    from src.tools.repair_queue_tools import register_repair_queue_tools
    from src.capabilities.repair_queue import RepairQueueStore
    from tempfile import mkdtemp
    from pathlib import Path

    reg = _FakeRegistry()

    # Register a few non-repair tools first
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

    # Register repair queue tools
    store_dir = Path(mkdtemp())
    queue_store = RepairQueueStore(data_dir=store_dir)
    register_repair_queue_tools(reg, queue_store)
    return reg


# ── Profile structure ─────────────────────────────────────────────────

REPAIR_QUEUE_TOOL_NAMES = {
    "list_repair_queue_items",
    "view_repair_queue_item",
    "create_repair_queue_from_health",
    "acknowledge_repair_queue_item",
    "resolve_repair_queue_item",
    "dismiss_repair_queue_item",
}


class TestRepairOperatorProfile:
    def test_profile_exists(self):
        profile = get_runtime_profile("capability_repair_operator")
        assert profile is not None
        assert profile.name == "capability_repair_operator"

    def test_profile_has_capability_repair_operator_tag(self):
        profile = get_runtime_profile("capability_repair_operator")
        assert "capability_repair_operator" in profile.capabilities

    def test_profile_has_no_tool_names(self):
        profile = get_runtime_profile("capability_repair_operator")
        assert not profile.tool_names

    def test_profile_no_shell_policy(self):
        profile = get_runtime_profile("capability_repair_operator")
        assert profile.shell_policy_enabled is False

    def test_profile_no_internal_tools(self):
        profile = get_runtime_profile("capability_repair_operator")
        assert profile.include_internal is False


# ── Profile grants access to repair queue tools ───────────────────────

class TestRepairOperatorProfileGrants:
    def test_profile_accesses_all_repair_tools(self, registry_with_repair_tools):
        profile = get_runtime_profile("capability_repair_operator")
        specs = registry_with_repair_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        assert REPAIR_QUEUE_TOOL_NAMES.issubset(names)

    def test_profile_does_not_grant_non_repair_tools(self, registry_with_repair_tools):
        profile = get_runtime_profile("capability_repair_operator")
        specs = registry_with_repair_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for name in names:
            assert name in REPAIR_QUEUE_TOOL_NAMES, f"{name} should not be in repair operator profile"


# ── Other profiles denied ─────────────────────────────────────────────

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
    "capability_trust_operator",
]


class TestOtherProfilesDenied:
    @pytest.mark.parametrize("profile_name", STANDARD_PROFILE_NAMES)
    def test_standard_profile_denied(self, registry_with_repair_tools, profile_name):
        profile = get_runtime_profile(profile_name)
        specs = registry_with_repair_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in REPAIR_QUEUE_TOOL_NAMES:
            assert tool_name not in names, f"{tool_name} should not be in {profile_name}"

    @pytest.mark.parametrize("profile_name", OPERATOR_PROFILE_NAMES)
    def test_other_operator_profile_denied(self, registry_with_repair_tools, profile_name):
        profile = get_runtime_profile(profile_name)
        specs = registry_with_repair_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in REPAIR_QUEUE_TOOL_NAMES:
            assert tool_name not in names, f"{tool_name} should not be in {profile_name}"


class TestLocalExecutionDenied:
    def test_local_execution_denied(self, registry_with_repair_tools):
        profile = get_runtime_profile("local_execution")
        specs = registry_with_repair_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in REPAIR_QUEUE_TOOL_NAMES:
            assert tool_name not in names

    def test_task_execution_alias_denied(self, registry_with_repair_tools):
        profile = get_runtime_profile("task_execution")
        specs = registry_with_repair_tools.get_tools_for_profile(profile)
        names = {s.name for s in specs}
        for tool_name in REPAIR_QUEUE_TOOL_NAMES:
            assert tool_name not in names


# ── Feature flag gating ───────────────────────────────────────────────

class TestFeatureFlagGating:
    def test_flag_defaults_false(self):
        from config.settings import _s
        assert _s.capabilities.repair_queue_tools_enabled is False

    def test_flag_present_in_config(self):
        from config.settings import _s
        assert hasattr(_s.capabilities, "repair_queue_tools_enabled")
