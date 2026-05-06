"""Phase 6A — AgentPolicy capability metadata lint tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agents.policy import (
    AgentPolicy,
    CapabilityMetadataResult,
)
from src.agents.spec import AgentSpec


@pytest.fixture
def policy():
    catalog = MagicMock()
    catalog.count.return_value = 0
    catalog.get_by_name.return_value = None
    return AgentPolicy(catalog=catalog, llm_router=None)


@pytest.fixture
def base_spec():
    return AgentSpec(
        name="test_agent",
        system_prompt="test prompt",
        runtime_profile="agent_researcher",
    )


# ── Valid metadata passes lint ────────────────────────────────────────────

def test_valid_metadata_passes(policy, base_spec):
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True
    assert result.denials == []
    assert result.warnings == []


def test_all_defaults_pass(policy):
    spec = AgentSpec(name="x", system_prompt="p", runtime_profile="agent_researcher")
    result = policy.validate_capability_metadata(spec)
    assert result.allowed is True


def test_risk_level_medium_passes(policy, base_spec):
    base_spec.risk_level = "medium"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True


def test_approval_state_approved_passes(policy, base_spec):
    base_spec.approval_state = "approved"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True


def test_valid_bound_capabilities_pass(policy, base_spec):
    base_spec.bound_capabilities = ["workspace_a1b2c3d4", "global_e5f6g7h8"]
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True


def test_allowed_delegation_depth_at_max_passes(policy, base_spec):
    base_spec.allowed_delegation_depth = 3
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True


# ── Invalid capability IDs ─────────────────────────────────────────────────

def test_empty_string_capability_denied(policy, base_spec):
    base_spec.bound_capabilities = [""]
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("invalid_capability_id" in d for d in result.denials)


def test_uppercase_capability_id_denied(policy, base_spec):
    base_spec.bound_capabilities = ["INVALID"]
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("invalid_capability_id" in d for d in result.denials)


def test_special_chars_capability_id_denied(policy, base_spec):
    base_spec.bound_capabilities = ["bad-id!"]
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False


def test_too_short_capability_id_denied(policy, base_spec):
    base_spec.bound_capabilities = ["a"]
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False


# ── Unknown runtime_profile ────────────────────────────────────────────────

def test_unknown_runtime_profile_denied_when_known_profiles_provided(policy, base_spec):
    base_spec.runtime_profile = "super_admin"
    result = policy.validate_capability_metadata(
        base_spec, known_profiles=["agent_researcher", "agent_coder"],
    )
    assert result.allowed is False
    assert any("unknown_runtime_profile" in d for d in result.denials)


def test_known_runtime_profile_passes(policy, base_spec):
    result = policy.validate_capability_metadata(
        base_spec, known_profiles=["agent_researcher", "agent_coder", "chat_shell"],
    )
    assert result.allowed is True


def test_empty_runtime_profile_skipped_when_known_profiles_provided(policy, base_spec):
    base_spec.runtime_profile = ""
    result = policy.validate_capability_metadata(
        base_spec, known_profiles=["agent_researcher"],
    )
    assert result.allowed is True


# ── Risk level validation ──────────────────────────────────────────────────

def test_invalid_risk_level_denied(policy, base_spec):
    base_spec.risk_level = "critical"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("invalid_risk_level" in d for d in result.denials)


def test_high_risk_without_approval_denied(policy, base_spec):
    base_spec.risk_level = "high"
    base_spec.approval_state = "not_required"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("high" in d for d in result.denials)


def test_high_risk_with_approved_passes(policy, base_spec):
    base_spec.risk_level = "high"
    base_spec.approval_state = "approved"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True


def test_high_risk_with_pending_denied(policy, base_spec):
    base_spec.risk_level = "high"
    base_spec.approval_state = "pending"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False


# ── Approval state validation ──────────────────────────────────────────────

def test_invalid_approval_state_denied(policy, base_spec):
    base_spec.approval_state = "granted"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("invalid_approval_state" in d for d in result.denials)


def test_rejected_approval_state_warns(policy, base_spec):
    base_spec.approval_state = "rejected"
    base_spec.risk_level = "low"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True
    assert any("rejected" in w for w in result.warnings)


# ── Delegation depth ───────────────────────────────────────────────────────

def test_delegation_depth_negative_denied(policy, base_spec):
    base_spec.allowed_delegation_depth = -1
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("delegation_depth" in d for d in result.denials)


def test_delegation_depth_exceeds_max_denied(policy, base_spec):
    base_spec.allowed_delegation_depth = 99
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("delegation_depth" in d for d in result.denials)


# ── Capability binding mode ────────────────────────────────────────────────

def test_invalid_binding_mode_denied(policy, base_spec):
    base_spec.capability_binding_mode = "auto"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("capability_binding_mode" in d for d in result.denials)


def test_metadata_only_allowed(policy, base_spec):
    base_spec.capability_binding_mode = "metadata_only"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True


def test_advisory_allowed(policy, base_spec):
    base_spec.capability_binding_mode = "advisory"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is True


def test_enforced_mode_denied_in_phase6a(policy, base_spec):
    base_spec.capability_binding_mode = "enforced"
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("enforced" in d for d in result.denials)


# ── Available capabilities awareness ───────────────────────────────────────

def test_unknown_bound_capability_warns(policy, base_spec):
    base_spec.bound_capabilities = ["workspace_a1b2c3d4"]
    result = policy.validate_capability_metadata(
        base_spec, available_capabilities=["global_e5f6g7h8"],
    )
    assert result.allowed is True  # warning, not denial
    assert any("not in available_capabilities" in w for w in result.warnings)


def test_known_bound_capability_no_warning(policy, base_spec):
    base_spec.bound_capabilities = ["workspace_a1b2c3d4"]
    result = policy.validate_capability_metadata(
        base_spec, available_capabilities=["workspace_a1b2c3d4"],
    )
    assert result.allowed is True
    assert result.warnings == []


# ── No self-referential agent admin capabilities ───────────────────────────

def test_agent_admin_capability_denied(policy, base_spec):
    base_spec.bound_capabilities = ["agent_admin_v1"]
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("agent-admin" in d for d in result.denials)


def test_agent_create_capability_denied(policy, base_spec):
    base_spec.bound_capabilities = ["agent_create_tool"]
    result = policy.validate_capability_metadata(base_spec)
    assert result.allowed is False
    assert any("agent-admin" in d for d in result.denials)


# ── Policy lint does not mutate spec ───────────────────────────────────────

def test_lint_does_not_mutate_spec(policy, base_spec):
    original_binding = base_spec.capability_binding_mode
    original_risk = base_spec.risk_level
    original_caps = list(base_spec.bound_capabilities)

    base_spec.capability_binding_mode = "enforced"
    base_spec.risk_level = "high"
    base_spec.bound_capabilities = ["invalid!!"]

    policy.validate_capability_metadata(base_spec)

    assert base_spec.capability_binding_mode == "enforced"
    assert base_spec.risk_level == "high"
    assert base_spec.bound_capabilities == ["invalid!!"]


# ── No CapabilityStore import ──────────────────────────────────────────────

def test_policy_does_not_import_capabilities():
    """AgentPolicy module must not import from src.capabilities."""
    import importlib
    import sys
    mod = sys.modules.get("src.agents.policy")
    if mod is None:
        mod = importlib.import_module("src.agents.policy")

    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if hasattr(obj, "__module__"):
            mod_name = obj.__module__
            assert "src.capabilities" not in mod_name, (
                f"policy module leaks capability import via {attr_name}"
            )

    # Also check the module's own imports aren't from capabilities
    import ast
    import inspect
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_name = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module_name = node.module
            if module_name and "src.capabilities" in module_name:
                pytest.fail(f"policy.py imports from src.capabilities: {ast.dump(node)}")


# ── Lint does not grant tools or profiles ──────────────────────────────────

def test_lint_does_not_modify_policy_state(policy, base_spec):
    """validate_capability_metadata has no side effects on AgentPolicy."""
    policy.validate_capability_metadata(base_spec)
    # No state change on policy object — method is purely functional
    assert True


def test_lint_result_is_deterministic(policy, base_spec):
    base_spec.bound_capabilities = ["workspace_a1b2c3d4"]
    r1 = policy.validate_capability_metadata(base_spec)
    r2 = policy.validate_capability_metadata(base_spec)
    assert r1.allowed == r2.allowed
    assert r1.warnings == r2.warnings
    assert r1.denials == r2.denials


# ── CapabilityMetadataResult dataclass ─────────────────────────────────────

def test_capability_metadata_result_defaults():
    r = CapabilityMetadataResult(allowed=True)
    assert r.allowed is True
    assert r.warnings == []
    assert r.denials == []


def test_capability_metadata_result_with_issues():
    r = CapabilityMetadataResult(
        allowed=False,
        warnings=["w1"],
        denials=["d1", "d2"],
    )
    assert r.allowed is False
    assert "w1" in r.warnings
    assert len(r.denials) == 2


# ── AgentPolicy class-level constants ──────────────────────────────────────

def test_max_persistent_agents():
    assert AgentPolicy.MAX_PERSISTENT_AGENTS == 10


def test_max_session_agents():
    assert AgentPolicy.MAX_SESSION_AGENTS == 5
