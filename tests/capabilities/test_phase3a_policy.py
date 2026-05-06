"""Phase 3A tests: CapabilityPolicy deterministic allow/deny decisions."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.capabilities.document import parse_capability
from src.capabilities.policy import CapabilityPolicy, PolicyDecision, PolicySeverity
from src.capabilities.schema import (
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)


def _make_manifest(**overrides) -> CapabilityManifest:
    defaults = {
        "id": "test_skill_01",
        "name": "Test Skill",
        "description": "A test capability.",
        "type": CapabilityType.SKILL,
        "scope": CapabilityScope.WORKSPACE,
        "version": "0.1.0",
        "maturity": CapabilityMaturity.DRAFT,
        "status": CapabilityStatus.ACTIVE,
        "risk_level": CapabilityRiskLevel.LOW,
    }
    defaults.update(overrides)
    return CapabilityManifest(**defaults)


def _mock_manifest(**overrides) -> SimpleNamespace:
    """Create a mock manifest that bypasses Pydantic enum validation for testing invalid values."""
    defaults = {
        "id": "test_skill_01",
        "name": "Test Skill",
        "description": "A test capability.",
        "type": SimpleNamespace(value="skill"),
        "scope": SimpleNamespace(value="workspace"),
        "version": "0.1.0",
        "maturity": SimpleNamespace(value="draft"),
        "status": SimpleNamespace(value="active"),
        "risk_level": SimpleNamespace(value="low"),
        "required_tools": [],
        "required_permissions": [],
    }
    merged = {**defaults, **overrides}
    return SimpleNamespace(**merged)


# ── Fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
def policy():
    return CapabilityPolicy()


# ── validate_create ─────────────────────────────────────────────────────

class TestValidateCreate:
    def test_low_risk_valid_allowed(self, policy):
        m = _make_manifest()
        result = policy.validate_create(m)
        assert result.allowed

    def test_invalid_scope_denied(self, policy):
        m = _mock_manifest(scope=SimpleNamespace(value="invalid_scope_value"))
        result = policy.validate_create(m)
        assert not result.allowed
        assert "unknown_scope" in result.code

    def test_invalid_type_denied(self, policy):
        m = _mock_manifest(type=SimpleNamespace(value="nonexistent_type"))
        result = policy.validate_create(m)
        assert not result.allowed
        assert "unknown_type" in result.code

    def test_invalid_maturity_denied(self, policy):
        m = _mock_manifest(maturity=SimpleNamespace(value="super_stable"))
        result = policy.validate_create(m)
        assert not result.allowed
        assert "invalid_maturity" in result.code

    def test_invalid_status_denied(self, policy):
        m = _mock_manifest(status=SimpleNamespace(value="suspended"))
        result = policy.validate_create(m)
        assert not result.allowed
        assert "invalid_status" in result.code

    def test_invalid_risk_level_denied(self, policy):
        m = _mock_manifest(risk_level=SimpleNamespace(value="extreme"))
        result = policy.validate_create(m)
        assert not result.allowed
        assert "invalid_risk_level" in result.code

    def test_unknown_required_tool_denied_when_available_provided(self, policy):
        m = _mock_manifest(required_tools=["nonexistent_tool"])
        result = policy.validate_create(m, context={"available_tools": ["read", "write"]})
        assert not result.allowed
        assert result.code == "unknown_required_tools"

    def test_known_required_tool_allowed_when_available_provided(self, policy):
        m = _mock_manifest(required_tools=["read"])
        result = policy.validate_create(m, context={"available_tools": ["read", "write"]})
        assert result.allowed

    def test_missing_required_tool_allowed_when_available_tools_omitted(self, policy):
        m = _mock_manifest(required_tools=["nonexistent_tool"])
        result = policy.validate_required_tools(m, available_tools=None)
        assert result.allowed
        assert "not_validated" in result.code

    def test_policy_does_not_mutate_manifest(self, policy):
        m = _make_manifest()
        original = m.model_dump()
        policy.validate_create(m)
        assert m.model_dump() == original


# ── validate_promote ────────────────────────────────────────────────────

class TestValidatePromote:
    def test_high_risk_requires_approval(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.HIGH)
        result = policy.validate_promote(m, approval=None)
        assert not result.allowed
        assert "high_risk" in result.code

    def test_high_risk_with_approval_allowed(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.HIGH)
        result = policy.validate_promote(m, approval={"approved": True})
        assert result.allowed

    def test_medium_risk_requires_approval_or_eval(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.MEDIUM)
        result = policy.validate_promote(m, eval_record=None, approval=None)
        assert not result.allowed
        assert "medium_risk" in result.code

    def test_medium_risk_with_eval_passed_allowed(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.MEDIUM)
        result = policy.validate_promote(m, eval_record={"passed": True}, approval=None)
        assert result.allowed

    def test_medium_risk_with_eval_failed_denied(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.MEDIUM)
        result = policy.validate_promote(m, eval_record={"passed": False}, approval=None)
        assert not result.allowed

    def test_quarantined_cannot_promote(self, policy):
        m = _make_manifest(status=CapabilityStatus.QUARANTINED)
        result = policy.validate_promote(m)
        assert not result.allowed
        assert "quarantined" in result.code.lower()

    def test_archived_cannot_promote(self, policy):
        m = _make_manifest(status=CapabilityStatus.ARCHIVED)
        result = policy.validate_promote(m)
        assert not result.allowed
        assert "archived" in result.code.lower()

    def test_low_risk_no_eval_allowed(self, policy):
        m = _make_manifest(risk_level=CapabilityRiskLevel.LOW)
        result = policy.validate_promote(m, eval_record=None, approval=None)
        assert result.allowed


# ── validate_run ────────────────────────────────────────────────────────

class TestValidateRun:
    def test_active_allowed(self, policy):
        m = _make_manifest(status=CapabilityStatus.ACTIVE)
        result = policy.validate_run(m)
        assert result.allowed

    def test_disabled_cannot_run(self, policy):
        m = _make_manifest(status=CapabilityStatus.DISABLED)
        result = policy.validate_run(m)
        assert not result.allowed

    def test_archived_cannot_run(self, policy):
        m = _make_manifest(status=CapabilityStatus.ARCHIVED)
        result = policy.validate_run(m)
        assert not result.allowed

    def test_quarantined_cannot_run(self, policy):
        m = _make_manifest(status=CapabilityStatus.QUARANTINED)
        result = policy.validate_run(m)
        assert not result.allowed


# ── validate_scope ──────────────────────────────────────────────────────

class TestValidateScope:
    def test_global_valid(self, policy):
        m = _make_manifest(scope=CapabilityScope.GLOBAL)
        assert policy.validate_scope(m).allowed

    def test_user_valid(self, policy):
        m = _make_manifest(scope=CapabilityScope.USER)
        assert policy.validate_scope(m).allowed

    def test_workspace_valid(self, policy):
        m = _make_manifest(scope=CapabilityScope.WORKSPACE)
        assert policy.validate_scope(m).allowed

    def test_session_valid(self, policy):
        m = _make_manifest(scope=CapabilityScope.SESSION)
        assert policy.validate_scope(m).allowed


# ── validate_required_tools ─────────────────────────────────────────────

class TestValidateRequiredTools:
    def test_empty_required_tools_always_ok(self, policy):
        m = _make_manifest(required_tools=[])
        result = policy.validate_required_tools(m, available_tools=["read"])
        assert result.allowed

    def test_all_tools_known(self, policy):
        m = _make_manifest(required_tools=["read", "write"])
        result = policy.validate_required_tools(m, available_tools=["read", "write", "bash"])
        assert result.allowed

    def test_unknown_tool_denied(self, policy):
        m = _make_manifest(required_tools=["super_tool"])
        result = policy.validate_required_tools(m, available_tools=["read"])
        assert not result.allowed

    def test_no_available_tools_returns_allow(self, policy):
        m = _make_manifest(required_tools=["any_tool"])
        result = policy.validate_required_tools(m, available_tools=None)
        assert result.allowed
        assert "not_validated" in result.code


# ── validate_risk ───────────────────────────────────────────────────────

class TestValidateRisk:
    def test_no_permissions_ok(self, policy):
        m = _make_manifest(required_permissions=[])
        result = policy.validate_risk(m)
        assert result.allowed

    def test_low_risk_with_sensitive_permissions_warns(self, policy):
        m = _make_manifest(
            risk_level=CapabilityRiskLevel.LOW,
            required_permissions=["write"],
        )
        result = policy.validate_risk(m)
        assert result.allowed
        assert result.severity == PolicySeverity.WARNING


# ── validate_install ────────────────────────────────────────────────────

class TestValidateInstall:
    def test_local_source_allowed(self, policy):
        m = _make_manifest()
        result = policy.validate_install(m, source="local")
        assert result.allowed

    def test_external_source_warns(self, policy):
        m = _make_manifest()
        result = policy.validate_install(m, source="https://example.com/cap.tar.gz")
        assert result.allowed
        assert result.severity == PolicySeverity.WARNING

    def test_trusted_source_allowed(self, policy):
        m = _make_manifest()
        result = policy.validate_install(m, source="trusted-repo")
        assert result.allowed
        assert result.severity == PolicySeverity.INFO


# ── validate_patch ──────────────────────────────────────────────────────

class TestValidatePatch:
    def test_id_change_denied(self, policy):
        old = _make_manifest(id="cap_a")
        new = _make_manifest(id="cap_b")
        result = policy.validate_patch(old, new)
        assert not result.allowed

    def test_scope_change_denied(self, policy):
        old = _make_manifest(scope=CapabilityScope.WORKSPACE)
        new = _make_manifest(scope=CapabilityScope.GLOBAL)
        result = policy.validate_patch(old, new)
        assert not result.allowed
