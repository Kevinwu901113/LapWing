"""tests/agents/test_agent_candidate_policy.py — Phase 6B policy tests."""

import pytest

from src.agents.candidate import (
    AgentCandidate,
    AgentCandidateFinding,
)
from src.agents.policy import (
    AgentPolicy,
    CandidateValidationResult,
)
from src.agents.spec import AgentSpec


def _make_policy():
    """Create a minimal AgentPolicy with no catalog/router needed for candidate lint."""
    policy = AgentPolicy.__new__(AgentPolicy)
    policy._catalog = None
    policy._llm_router = None
    return policy


def _make_candidate(**overrides):
    spec = AgentSpec(name="test", description="test")
    defaults = {
        "candidate_id": "cand_pol_test",
        "name": "test",
        "description": "test candidate",
        "proposed_spec": spec,
        "reason": "policy testing",
    }
    defaults.update(overrides)
    return AgentCandidate(**defaults)


class TestValidCandidatePassesLint:
    def test_minimal_candidate_passes(self):
        policy = _make_policy()
        cand = _make_candidate()
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is True
        assert result.denials == []
        assert result.warnings == []

    def test_candidate_with_valid_profile_passes(self):
        policy = _make_policy()
        cand = _make_candidate(requested_runtime_profile="agent_researcher")
        result = policy.validate_agent_candidate(
            cand,
            known_profiles=["agent_researcher", "agent_coder"],
        )
        assert result.allowed is True

    def test_candidate_with_valid_bound_capabilities(self):
        policy = _make_policy()
        cand = _make_candidate(
            bound_capabilities=["workspace_a1b2c3d4", "global_e5f6g7h8"],
        )
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is True

    def test_candidate_with_all_optional_fields_set(self):
        policy = _make_policy()
        cand = _make_candidate(
            created_by="testbot",
            source_trace_id="trace_abc",
            source_task_summary="did something",
            requested_runtime_profile="agent_researcher",
            requested_tools=["bash", "read"],
            bound_capabilities=["workspace_a1b2c3d4"],
            risk_level="medium",
            approval_state="approved",
        )
        result = policy.validate_agent_candidate(
            cand,
            known_profiles=["agent_researcher", "agent_coder"],
            available_tools=["bash", "read", "write", "think"],
        )
        assert result.allowed is True


class TestUnknownRuntimeProfile:
    def test_unknown_profile_denied(self):
        policy = _make_policy()
        cand = _make_candidate(requested_runtime_profile="super_admin")
        result = policy.validate_agent_candidate(
            cand,
            known_profiles=["agent_researcher", "agent_coder"],
        )
        assert result.allowed is False
        assert any("unknown requested_runtime_profile" in d for d in result.denials)
        assert len(result.denials) == 1

    def test_unknown_profile_not_checked_when_no_known_profiles(self):
        policy = _make_policy()
        cand = _make_candidate(requested_runtime_profile="super_admin")
        result = policy.validate_agent_candidate(cand)  # No known_profiles
        assert result.allowed is True  # Not checked

    def test_empty_profile_skipped_when_known_profiles_provided(self):
        policy = _make_policy()
        cand = _make_candidate(requested_runtime_profile=None)
        result = policy.validate_agent_candidate(
            cand,
            known_profiles=["agent_researcher"],
        )
        assert result.allowed is True


class TestRequestedTools:
    def test_unknown_tool_warned(self):
        policy = _make_policy()
        cand = _make_candidate(requested_tools=["bash", "sudo"])
        result = policy.validate_agent_candidate(
            cand,
            available_tools=["bash", "read", "write"],
        )
        assert result.allowed is True  # Warnings don't block
        assert any("sudo" in w for w in result.warnings)

    def test_all_known_tools_no_warning(self):
        policy = _make_policy()
        cand = _make_candidate(requested_tools=["bash", "read"])
        result = policy.validate_agent_candidate(
            cand,
            available_tools=["bash", "read", "write"],
        )
        assert len(result.warnings) == 0

    def test_no_tools_check_when_no_available_tools(self):
        policy = _make_policy()
        cand = _make_candidate(requested_tools=["bash"])
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is True


class TestHighRiskRequiresApproval:
    def test_high_risk_without_approval_warned(self):
        policy = _make_policy()
        cand = _make_candidate(risk_level="high", approval_state="pending")
        result = policy.validate_agent_candidate(cand)
        assert any("high" in w.lower() and "approval" in w.lower() for w in result.warnings)

    def test_high_risk_with_approval_no_warning(self):
        policy = _make_policy()
        cand = _make_candidate(risk_level="high", approval_state="approved")
        result = policy.validate_agent_candidate(cand)
        warnings_lower = [w.lower() for w in result.warnings]
        assert not any("high" in w and "require approval" in w for w in warnings_lower)

    def test_low_risk_no_approval_warning(self):
        policy = _make_policy()
        cand = _make_candidate(risk_level="low", approval_state="pending")
        result = policy.validate_agent_candidate(cand)
        warnings_lower = [w.lower() for w in result.warnings]
        assert not any("high" in w for w in warnings_lower)


class TestRejectedCandidate:
    def test_rejected_candidate_warned(self):
        policy = _make_policy()
        cand = _make_candidate(approval_state="rejected")
        result = policy.validate_agent_candidate(cand)
        assert any("rejected" in w.lower() for w in result.warnings)

    def test_rejected_warning_mentions_future_phases(self):
        policy = _make_policy()
        cand = _make_candidate(approval_state="rejected")
        result = policy.validate_agent_candidate(cand)
        assert any("future" in w.lower() for w in result.warnings)


class TestInvalidBoundCapabilityId:
    def test_invalid_syntax_denied(self):
        policy = _make_policy()
        cand = _make_candidate(bound_capabilities=["BAD CAP ID!!!"])
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is False
        assert any("invalid bound_capability id" in d for d in result.denials)

    def test_valid_ids_passes(self):
        policy = _make_policy()
        cand = _make_candidate(
            bound_capabilities=["workspace_a1b2c3d4", "global_e5f6g7h8"],
        )
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is True

    def test_empty_bound_capabilities_passes(self):
        policy = _make_policy()
        cand = _make_candidate(bound_capabilities=[])
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is True


class TestLintDeterministic:
    def test_same_input_same_output(self):
        policy = _make_policy()
        cand = _make_candidate(
            bound_capabilities=["workspace_a1b2c3d4"],
            requested_runtime_profile="agent_researcher",
        )
        r1 = policy.validate_agent_candidate(
            cand,
            known_profiles=["agent_researcher", "agent_coder"],
        )
        r2 = policy.validate_agent_candidate(
            cand,
            known_profiles=["agent_researcher", "agent_coder"],
        )
        assert r1.allowed == r2.allowed
        assert r1.warnings == r2.warnings
        assert r1.denials == r2.denials


class TestLintDoesNotMutateCandidate:
    def test_candidate_unchanged_after_lint(self):
        policy = _make_policy()
        cand = _make_candidate(
            approval_state="pending",
            risk_level="medium",
            bound_capabilities=["workspace_a1b2c3d4"],
        )
        orig_approval = cand.approval_state
        orig_risk = cand.risk_level
        orig_caps = list(cand.bound_capabilities)
        orig_evidence = list(cand.eval_evidence)
        orig_findings = list(cand.policy_findings)
        policy.validate_agent_candidate(cand)
        assert cand.approval_state == orig_approval
        assert cand.risk_level == orig_risk
        assert cand.bound_capabilities == orig_caps
        assert cand.eval_evidence == orig_evidence
        assert cand.policy_findings == orig_findings


class TestLintDoesNotImportSrcCapabilities:
    def test_no_capabilities_import_in_policy_module(self):
        """Verify policy.py doesn't import from src.capabilities."""
        import ast
        import inspect
        from src.agents import policy as policy_module

        source = inspect.getsource(policy_module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, 'module', None) or ''
                if hasattr(node, 'names'):
                    for alias in node.names:
                        full = f"{module}.{alias.name}" if module else alias.name
                        if 'src.capabilities' in module or 'src.capabilities' in full:
                            # Check that this import matches our allowlist
                            import_path = f"{module}.{alias.name}" if module else alias.name
                            raise AssertionError(
                                f"policy.py imports from capabilities: {import_path}"
                            )


class TestLintDoesNotGrantPermissions:
    def test_result_is_plain_data(self):
        """validate_agent_candidate returns a plain dataclass, not side effects."""
        policy = _make_policy()
        cand = _make_candidate()
        result = policy.validate_agent_candidate(cand)
        assert isinstance(result, CandidateValidationResult)
        assert isinstance(result.allowed, bool)
        assert isinstance(result.warnings, list)
        assert isinstance(result.denials, list)

    def test_lint_does_not_change_any_system_state(self):
        policy = _make_policy()
        cand = _make_candidate()
        # The method should not access catalog, router, or any I/O
        result = policy.validate_agent_candidate(cand)
        assert result is not None


class TestCandidateValidationResult:
    def test_defaults(self):
        result = CandidateValidationResult(allowed=True)
        assert result.allowed is True
        assert result.warnings == []
        assert result.denials == []

    def test_with_warnings_and_denials(self):
        result = CandidateValidationResult(
            allowed=False,
            warnings=["w1", "w2"],
            denials=["d1"],
        )
        assert result.allowed is False
        assert len(result.warnings) == 2
        assert len(result.denials) == 1


class TestSelfReferentialCapability:
    def test_agent_admin_in_bound_capabilities_denied(self):
        policy = _make_policy()
        cand = _make_candidate(bound_capabilities=["agent_admin_capability"])
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is False
        assert any("agent-admin" in d or "agent_admin" in d for d in result.denials)

    def test_agent_create_in_bound_capabilities_denied(self):
        policy = _make_policy()
        cand = _make_candidate(bound_capabilities=["agent_create_tool"])
        result = policy.validate_agent_candidate(cand)
        assert result.allowed is False
        assert any("agent-admin" in d or "agent_create" in d for d in result.denials)
