"""Phase 6C — Save gate unit tests for persistent dynamic agents.

Tests:
  - Feature flag behavior (gate disabled by default)
  - is_capability_backed_agent heuristic
  - Candidate matching rules
  - Evidence sufficiency by risk level
  - Atomicity of denied saves
  - SaveGateResult dataclass
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.candidate import (
    AgentCandidate,
    AgentCandidateFinding,
    AgentEvalEvidence,
    validate_candidate_id,
)
from src.agents.candidate_store import AgentCandidateStore
from src.agents.policy import (
    AgentPolicy,
    AgentPolicyViolation,
    SaveGateResult,
)
from src.agents.spec import (
    AgentLifecyclePolicy,
    AgentResourceLimits,
    AgentSpec,
    is_capability_backed_agent,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_policy():
    policy = AgentPolicy.__new__(AgentPolicy)
    policy._catalog = MagicMock()
    policy._catalog.count.return_value = 0
    policy._catalog.get_by_name.return_value = None
    policy._llm_router = None
    policy.evidence_max_age_days = 90
    return policy


def _base_spec(**overrides):
    defaults = {
        "name": "test_agent",
        "description": "test",
        "system_prompt": "test prompt",
        "runtime_profile": "agent_researcher",
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


def _capability_backed_spec(**overrides):
    """Create a spec that is_capability_backed_agent returns True for."""
    defaults = {
        "name": "cap_agent",
        "description": "capability-backed agent",
        "system_prompt": "test prompt",
        "runtime_profile": "agent_researcher",
        "bound_capabilities": ["workspace_a1b2c3d4"],
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


def _make_store(tmp_path):
    return AgentCandidateStore(tmp_path / "agent_candidates")


def _make_candidate(spec=None, **overrides):
    if spec is None:
        spec = _capability_backed_spec()
    defaults = {
        "candidate_id": "cand_test_001",
        "name": spec.name,
        "description": "test candidate",
        "proposed_spec": spec,
        "reason": "testing save gate",
        "approval_state": "approved",
        "risk_level": spec.risk_level,
    }
    defaults.update(overrides)
    return AgentCandidate(**defaults)


def _make_evidence(**overrides):
    defaults = {
        "evidence_type": "task_success",
        "summary": "All checks passed",
        "passed": True,
    }
    defaults.update(overrides)
    return AgentEvalEvidence(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Feature flag tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureFlagDefaults:
    def test_flag_defaults_false(self):
        policy = _make_policy()
        spec = _capability_backed_spec()
        result = policy.validate_persistent_save_gate(spec, require_candidate_approval=False)
        assert result.allowed is True

    def test_flag_false_legacy_spec_saves(self):
        """When flag is false, legacy spec save gate passes (no-op)."""
        policy = _make_policy()
        spec = _base_spec()  # not capability-backed
        result = policy.validate_persistent_save_gate(spec, require_candidate_approval=False)
        assert result.allowed is True

    def test_flag_false_capability_backed_spec_saves(self):
        """When flag is false, capability-backed spec save gate passes."""
        policy = _make_policy()
        spec = _capability_backed_spec()
        result = policy.validate_persistent_save_gate(spec, require_candidate_approval=False)
        assert result.allowed is True

    def test_flag_true_ordinary_spec_saves(self):
        """When flag is true, non-capability-backed spec passes."""
        policy = _make_policy()
        spec = _base_spec()
        result = policy.validate_persistent_save_gate(spec, require_candidate_approval=True)
        assert result.allowed is True

    def test_flag_true_capability_backed_no_candidate_denied(self):
        """When flag is true and spec is capability-backed, missing candidate
        is denied."""
        policy = _make_policy()
        spec = _capability_backed_spec()
        result = policy.validate_persistent_save_gate(spec, require_candidate_approval=True)
        assert result.allowed is False
        assert any("missing_candidate" in d for d in result.denials)


# ══════════════════════════════════════════════════════════════════════════════
# 2. is_capability_backed_agent tests
# ══════════════════════════════════════════════════════════════════════════════

class TestIsCapabilityBackedAgent:
    def test_old_spec_false(self):
        spec = _base_spec()
        assert is_capability_backed_agent(spec) is False

    def test_default_spec_false(self):
        spec = AgentSpec()
        assert is_capability_backed_agent(spec) is False

    def test_bound_capabilities_non_empty_true(self):
        spec = _base_spec(bound_capabilities=["workspace_a1b2c3d4"])
        assert is_capability_backed_agent(spec) is True

    def test_capability_binding_mode_advisory_true(self):
        spec = _base_spec(capability_binding_mode="advisory")
        assert is_capability_backed_agent(spec) is True

    def test_capability_binding_mode_enforced_true(self):
        spec = _base_spec(capability_binding_mode="enforced")
        assert is_capability_backed_agent(spec) is True

    def test_risk_level_medium_true(self):
        spec = _base_spec(risk_level="medium")
        assert is_capability_backed_agent(spec) is True

    def test_risk_level_high_true(self):
        spec = _base_spec(risk_level="high")
        assert is_capability_backed_agent(spec) is True

    def test_eval_tasks_non_empty_true(self):
        spec = _base_spec(eval_tasks=[{"name": "smoke_test"}])
        assert is_capability_backed_agent(spec) is True

    def test_approval_state_pending_true(self):
        spec = _base_spec(approval_state="pending")
        assert is_capability_backed_agent(spec) is True

    def test_approval_state_approved_true(self):
        spec = _base_spec(approval_state="approved")
        assert is_capability_backed_agent(spec) is True

    def test_approval_state_rejected_true(self):
        spec = _base_spec(approval_state="rejected")
        assert is_capability_backed_agent(spec) is True

    def test_delegation_depth_gt_zero_true(self):
        spec = _base_spec(allowed_delegation_depth=1)
        assert is_capability_backed_agent(spec) is True

    def test_low_risk_metadata_only_no_bindings_false(self):
        spec = _base_spec(
            risk_level="low",
            capability_binding_mode="metadata_only",
            bound_capabilities=[],
            eval_tasks=[],
            approval_state="not_required",
            allowed_delegation_depth=0,
        )
        assert is_capability_backed_agent(spec) is False

    def test_low_risk_metadata_only_with_bindings_true(self):
        """bound_capabilities non-empty makes it capability-backed regardless
        of risk level and binding mode."""
        spec = _base_spec(
            risk_level="low",
            capability_binding_mode="metadata_only",
            bound_capabilities=["workspace_a1b2c3d4"],
        )
        assert is_capability_backed_agent(spec) is True

    def test_eval_tasks_empty_list_false(self):
        spec = _base_spec(eval_tasks=[])
        assert is_capability_backed_agent(spec) is False

    def test_bound_capabilities_empty_list_false(self):
        spec = _base_spec(bound_capabilities=[])
        assert is_capability_backed_agent(spec) is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. Candidate matching tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCandidateMatching:
    def test_approved_matching_candidate_allows_save(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)

        retrieved = store.get_candidate(candidate.candidate_id)
        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is True

    def test_missing_candidate_id_denied(self, tmp_path):
        policy = _make_policy()
        spec = _capability_backed_spec()
        result = policy.validate_persistent_save_gate(
            spec, candidate=None, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("missing_candidate" in d for d in result.denials)

    def test_candidate_not_found_in_store_raises(self, tmp_path):
        """CandidateStore.get_candidate raises CandidateStoreError for unknown id."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        with pytest.raises(Exception) as exc_info:
            store.get_candidate("cand_nonexistent")
        assert "not found" in str(exc_info.value) or "not found" in str(exc_info.value).lower()

    def test_pending_candidate_denied(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="pending")
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("candidate_not_approved" in d for d in result.denials)

    def test_rejected_candidate_denied(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="rejected")
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("candidate_not_approved" in d for d in result.denials)

    def test_archived_candidate_denied(self, tmp_path):
        """Phase 6D: archived candidates are denied by the save gate even if
        approval_state is 'approved'."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)

        # Archive it
        store.archive_candidate(candidate.candidate_id, reason="test")
        # Archived candidate still has approval_state='approved' but
        # metadata['archived']=True. Phase 6D blocks archived candidates.
        retrieved = store.get_candidate(candidate.candidate_id)
        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("archived" in d for d in result.denials)

    def test_candidate_spec_hash_mismatch_denied(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec_a = _capability_backed_spec(name="agent_a")
        spec_b = _capability_backed_spec(name="agent_b")
        candidate = _make_candidate(spec=spec_a, approval_state="approved")
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec_b, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("spec_hash_mismatch" in d for d in result.denials)

    def test_candidate_risk_mismatch_denied(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec_low = _capability_backed_spec(risk_level="low", bound_capabilities=["workspace_a1b2c3d4"])
        # Create candidate with matching spec hash but different risk_level
        candidate = _make_candidate(
            spec=spec_low, approval_state="approved", risk_level="medium",
        )
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec_low, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("risk_level_mismatch" in d for d in result.denials)

    def test_invalid_candidate_id_rejected(self):
        with pytest.raises(ValueError):
            validate_candidate_id("../../etc/passwd")

    def test_policy_lint_denied_blocks_save(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(
            bound_capabilities=["agent_admin_v1"],
        )
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        # agent_admin capability is denied by policy lint
        assert result.allowed is False
        assert any("agent-admin" in d for d in result.denials)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Evidence sufficiency tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceSufficiency:
    def test_low_risk_approved_no_evidence_allowed(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="low")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="low")
        # No evidence added
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is True

    def test_medium_risk_no_evidence_denied(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("insufficient_evidence" in d for d in result.denials)

    def test_medium_risk_with_passed_evidence_allowed(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")
        candidate.eval_evidence = [_make_evidence(evidence_type="task_success", passed=True)]
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is True

    def test_medium_risk_failed_evidence_ignored(self, tmp_path):
        """Only passed evidence counts. Failed evidence is ignored."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=False),
        ]
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("insufficient_evidence" in d for d in result.denials)

    def test_high_risk_without_manual_review_denied(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="high", approval_state="approved")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="high")
        candidate.eval_evidence = [
            _make_evidence(evidence_type="policy_lint", passed=True),
            _make_evidence(evidence_type="task_success", passed=True),
        ]
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("manual_review" in d for d in result.denials)

    def test_high_risk_without_policy_lint_denied(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="high", approval_state="approved")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="high")
        candidate.eval_evidence = [
            _make_evidence(evidence_type="manual_review", passed=True),
        ]
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("policy_lint" in d for d in result.denials)

    def test_high_risk_with_both_passed_allowed(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="high", approval_state="approved")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="high")
        candidate.eval_evidence = [
            _make_evidence(evidence_type="manual_review", passed=True),
            _make_evidence(evidence_type="policy_lint", passed=True),
        ]
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is True

    def test_failed_manual_review_ignored(self, tmp_path):
        """Failed manual_review does NOT count — high risk needs passed manual_review."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="high", approval_state="approved")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="high")
        candidate.eval_evidence = [
            _make_evidence(evidence_type="manual_review", passed=False),
            _make_evidence(evidence_type="policy_lint", passed=True),
        ]
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("manual_review" in d for d in result.denials)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Atomicity and non-mutation tests
# ══════════════════════════════════════════════════════════════════════════════

class TestNoMutation:
    def test_save_gate_does_not_mutate_spec(self):
        policy = _make_policy()
        spec = _capability_backed_spec()
        original_hash = spec.spec_hash()
        original_caps = list(spec.bound_capabilities)

        policy.validate_persistent_save_gate(spec, require_candidate_approval=True)

        assert spec.spec_hash() == original_hash
        assert spec.bound_capabilities == original_caps

    def test_save_gate_does_not_mutate_candidate(self, tmp_path):
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        original_state = retrieved.approval_state
        original_evidence = list(retrieved.eval_evidence)

        policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )

        assert retrieved.approval_state == original_state
        assert retrieved.eval_evidence == original_evidence

    def test_validate_persistent_save_gate_is_pure(self, tmp_path):
        """validate_persistent_save_gate is a pure function — no side effects
        on policy state."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        # Call multiple times, same result
        r1 = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        r2 = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert r1.allowed == r2.allowed
        assert r1.denials == r2.denials


# ══════════════════════════════════════════════════════════════════════════════
# 6. SaveGateResult dataclass
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveGateResult:
    def test_default_allowed_true(self):
        r = SaveGateResult(allowed=True)
        assert r.allowed is True
        assert r.reason == ""
        assert r.denials == []

    def test_default_allowed_false(self):
        r = SaveGateResult(allowed=False, reason="denied")
        assert r.allowed is False
        assert r.reason == "denied"

    def test_with_denials(self):
        r = SaveGateResult(allowed=False, reason="denied", denials=["d1", "d2"])
        assert r.allowed is False
        assert len(r.denials) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 7. Candidate lookup failures in save_agent flow
# ══════════════════════════════════════════════════════════════════════════════

class TestCandidateLookupErrors:
    def test_nonexistent_candidate_raises_clean_error(self, tmp_path):
        """CandidateStore.get_candidate raises CandidateStoreError for unknown
        candidate_id — this is caught in save_agent and surfaced as
        AgentPolicyViolation with clean message, not a stack trace."""
        from src.agents.candidate_store import CandidateStoreError

        store = _make_store(tmp_path)
        with pytest.raises(CandidateStoreError, match="not found"):
            store.get_candidate("cand_does_not_exist")

    def test_corrupt_candidate_json_raises_clean_error(self, tmp_path):
        """Corrupt candidate JSON raises CandidateStoreError, not raw JSONDecodeError."""
        from src.agents.candidate_store import CandidateStoreError

        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)

        # Corrupt the JSON file
        candidate_file = store._candidate_file(candidate.candidate_id)
        candidate_file.write_text("this is not valid json")

        with pytest.raises(CandidateStoreError, match="corrupt JSON"):
            store.get_candidate(candidate.candidate_id)


# ══════════════════════════════════════════════════════════════════════════════
# 8. Config default verification
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigDefault:
    def test_require_candidate_approval_defaults_false(self):
        from src.config import get_settings
        s = get_settings()
        assert s.agents.require_candidate_approval_for_persistence is False

    def test_backward_compat_constant_defaults_false(self):
        from config.settings import AGENTS_REQUIRE_CANDIDATE_APPROVAL_FOR_PERSISTENCE
        assert AGENTS_REQUIRE_CANDIDATE_APPROVAL_FOR_PERSISTENCE is False
