"""Phase 6D — Save gate hardening tests.

Tests:
  - Archived candidate blocked by save gate
  - Approved + archived candidate denied
  - Denied save is atomic (no mutation)
  - Non-archived approved candidate still works
  - Evidence freshness (if implemented)
  - Fresh evidence passes
  - Stale evidence denied
  - Missing created_at handled conservatively
  - Ordinary agents unaffected
  - Flag false unchanged
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.candidate import AgentCandidate, AgentEvalEvidence
from src.agents.candidate_store import AgentCandidateStore
from src.agents.policy import AgentPolicy, SaveGateResult
from src.agents.spec import AgentSpec, is_capability_backed_agent


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_policy(evidence_max_age_days=90):
    policy = AgentPolicy.__new__(AgentPolicy)
    policy._catalog = MagicMock()
    policy._catalog.count.return_value = 0
    policy._catalog.get_by_name.return_value = None
    policy._llm_router = None
    policy.evidence_max_age_days = evidence_max_age_days
    return policy


def _capability_backed_spec(**overrides):
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
# Archived candidate blocked by save gate
# ══════════════════════════════════════════════════════════════════════════════

class TestArchivedCandidateSaveGate:
    def test_approved_archived_candidate_denied(self, tmp_path):
        """Phase 6D fix: approved-but-archived candidates are denied by save gate."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)
        store.archive_candidate(candidate.candidate_id, reason="test")

        retrieved = store.get_candidate(candidate.candidate_id)
        # Candidate is approved AND archived — should be denied
        assert retrieved.approval_state == "approved"
        assert retrieved.metadata.get("archived") is True

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("archived" in d for d in result.denials)

    def test_non_archived_approved_candidate_still_works(self, tmp_path):
        """Non-archived approved candidate still passes the save gate."""
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

    def test_denied_save_is_atomic(self, tmp_path):
        """Denied save gate does not mutate spec or candidate."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)
        store.archive_candidate(candidate.candidate_id)
        retrieved = store.get_candidate(candidate.candidate_id)

        original_spec_hash = spec.spec_hash()
        original_state = retrieved.approval_state

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False

        # No mutation
        assert spec.spec_hash() == original_spec_hash
        assert retrieved.approval_state == original_state

    def test_pending_candidate_still_denied(self, tmp_path):
        """Pending candidates are still denied (existing behavior unchanged)."""
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

    def test_rejected_candidate_still_denied(self, tmp_path):
        """Rejected candidates are still denied (existing behavior unchanged)."""
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


# ══════════════════════════════════════════════════════════════════════════════
# Evidence freshness tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceFreshness:
    def test_fresh_evidence_passes(self, tmp_path):
        """Evidence created recently passes freshness check."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")

        # Create fresh evidence (today)
        fresh_date = datetime.now(timezone.utc).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = fresh_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=90,
        )
        assert result.allowed is True

    def test_stale_evidence_denied(self, tmp_path):
        """Evidence older than threshold is denied."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")

        # Create stale evidence (100 days ago)
        stale_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=90,
        )
        assert result.allowed is False
        assert any("stale_evidence" in d for d in result.denials)

    def test_missing_created_at_handled_conservatively(self, tmp_path):
        """Evidence with no created_at is treated as stale (conservative)."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = ""
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=90,
        )
        assert result.allowed is False
        assert any("stale_evidence" in d for d in result.denials)

    def test_low_risk_skips_freshness(self, tmp_path):
        """Low risk candidates skip evidence freshness check."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="low")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="low")

        # Even stale evidence doesn't block low-risk
        stale_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=90,
        )
        assert result.allowed is True

    def test_freshness_not_enforced_when_none(self, tmp_path):
        """When evidence_max_age_days is None, freshness is not enforced."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")

        stale_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=None,
        )
        assert result.allowed is True

    def test_high_risk_stale_evidence_denied(self, tmp_path):
        """High risk with stale evidence is denied even if evidence types are correct."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="high", approval_state="approved")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="high")

        stale_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        candidate.eval_evidence = [
            AgentEvalEvidence(
                evidence_type="manual_review", summary="review", passed=True,
            ),
            AgentEvalEvidence(
                evidence_type="policy_lint", summary="lint", passed=True,
            ),
        ]
        candidate.eval_evidence[0].created_at = stale_date
        candidate.eval_evidence[1].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=90,
        )
        assert result.allowed is False
        assert any("stale_evidence" in d for d in result.denials)


# ══════════════════════════════════════════════════════════════════════════════
# Ordinary agents unaffected
# ══════════════════════════════════════════════════════════════════════════════

class TestOrdinaryAgentsUnaffected:
    def test_ordinary_agent_passes_with_flag_true(self):
        """Non-capability-backed agents always pass regardless of flag."""
        policy = _make_policy()
        spec = AgentSpec(name="ordinary", description="ordinary agent")
        result = policy.validate_persistent_save_gate(
            spec, require_candidate_approval=True,
            evidence_max_age_days=90,
        )
        assert result.allowed is True

    def test_ordinary_agent_passes_with_flag_false(self):
        policy = _make_policy()
        spec = AgentSpec(name="ordinary", description="ordinary agent")
        result = policy.validate_persistent_save_gate(
            spec, require_candidate_approval=False,
        )
        assert result.allowed is True

    def test_flag_false_archived_candidate_not_checked(self, tmp_path):
        """When flag is false, the gate is not run at all — archived candidate
        is not even looked up."""
        policy = _make_policy()
        store = _make_store(tmp_path)
        spec = _capability_backed_spec()
        candidate = _make_candidate(spec=spec, approval_state="approved")
        store.create_candidate(candidate)
        store.archive_candidate(candidate.candidate_id)

        # Flag false: gate disabled, passes without candidate lookup
        result = policy.validate_persistent_save_gate(
            spec, require_candidate_approval=False,
        )
        assert result.allowed is True


# ══════════════════════════════════════════════════════════════════════════════
# Config defaults for evidence freshness
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceFreshnessConfig:
    def test_max_age_days_default_in_config(self):
        from src.config import get_settings
        s = get_settings()
        assert s.agents.candidate_evidence_max_age_days == 90

    def test_max_age_days_constant(self):
        from config.settings import AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS
        assert AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS == 90

    def test_pydantic_default_is_90(self):
        """AgentsConfig() constructed with no args defaults to 90."""
        from src.config.settings import AgentsConfig
        c = AgentsConfig()
        assert c.candidate_evidence_max_age_days == 90

    def test_pydantic_explicit_none_disables(self):
        """Explicit None is supported to disable freshness checks."""
        from src.config.settings import AgentsConfig
        c = AgentsConfig(candidate_evidence_max_age_days=None)
        assert c.candidate_evidence_max_age_days is None

    def test_pydantic_explicit_zero(self):
        """Explicit 0 is supported (treated as disabled by save gate)."""
        from src.config.settings import AgentsConfig
        c = AgentsConfig(candidate_evidence_max_age_days=0)
        assert c.candidate_evidence_max_age_days == 0

    def test_missing_agents_section_falls_back_to_90(self):
        """When TOML has no [agents] section, Pydantic default 90 applies."""
        from src.config.settings import AgentsConfig
        # Simulate a TOML with no [agents] — AgentsConfig receives no data
        c = AgentsConfig.model_validate({})
        assert c.candidate_evidence_max_age_days == 90

    def test_partial_agents_section_falls_back_to_90(self):
        """When [agents] exists but lacks candidate_evidence_max_age_days, default 90 applies."""
        from src.config.settings import AgentsConfig
        c = AgentsConfig.model_validate({"require_candidate_approval_for_persistence": True})
        assert c.candidate_evidence_max_age_days == 90

    def test_env_var_override(self, monkeypatch):
        """AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS env var overrides default."""
        monkeypatch.setenv("AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS", "30")
        from src.config import reload_settings
        s = reload_settings()
        assert s.agents.candidate_evidence_max_age_days == 30
        # monkeypatch auto-reverts env var after test; reload to restore cache
        monkeypatch.undo()
        reload_settings()

    def test_compat_shim_uses_effective_value(self):
        """config/settings.py exports the effective value from get_settings()."""
        from config.settings import AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS
        from src.config import get_settings
        s = get_settings()
        assert AGENTS_CANDIDATE_EVIDENCE_MAX_AGE_DAYS == s.agents.candidate_evidence_max_age_days


# ══════════════════════════════════════════════════════════════════════════════
# Policy sentinel default — validate_persistent_save_gate uses policy default
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceMaxAgeDaysSentinelDefault:
    """When evidence_max_age_days is not passed, the policy's stored value is used."""

    def test_sentinel_uses_policy_default_of_90(self, tmp_path):
        """Not passing evidence_max_age_days uses policy.evidence_max_age_days (=90)."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy(evidence_max_age_days=90)
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")

        # Evidence is 100 days old — should be stale with default 90-day limit
        stale_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        # Do NOT pass evidence_max_age_days — sentinel triggers fallback to policy default
        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is False
        assert any("stale_evidence" in d for d in result.denials)

    def test_sentinel_disabled_when_policy_has_none(self, tmp_path):
        """When policy.evidence_max_age_days=None, freshness is not enforced."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy(evidence_max_age_days=None)
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")

        # Evidence is 100 days old — would be stale if enforced
        stale_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
        )
        assert result.allowed is True

    def test_per_call_override_still_works(self, tmp_path):
        """Explicit parameter overrides the policy default."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy(evidence_max_age_days=90)
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")

        # Evidence is 100 days old — stale with default 90, but we override to 200
        stale_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        # Override with 200 days — 100-day evidence should pass
        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=200,
        )
        assert result.allowed is True

    def test_per_call_override_to_none_disables(self, tmp_path):
        """Per-call None override disables freshness even when policy default is 90."""
        from datetime import datetime, timezone, timedelta

        policy = _make_policy(evidence_max_age_days=90)
        store = _make_store(tmp_path)
        spec = _capability_backed_spec(risk_level="medium")
        candidate = _make_candidate(spec=spec, approval_state="approved", risk_level="medium")

        stale_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        candidate.eval_evidence = [
            _make_evidence(evidence_type="task_success", passed=True)
        ]
        candidate.eval_evidence[0].created_at = stale_date
        store.create_candidate(candidate)
        retrieved = store.get_candidate(candidate.candidate_id)

        result = policy.validate_persistent_save_gate(
            spec, candidate=retrieved, require_candidate_approval=True,
            evidence_max_age_days=None,
        )
        assert result.allowed is True


class TestAgentPolicyDefaultWiring:
    """AgentPolicy constructor and integration with config."""

    def test_constructor_default_is_90(self):
        """AgentPolicy() defaults evidence_max_age_days to 90."""
        policy = AgentPolicy(catalog=MagicMock())
        assert policy.evidence_max_age_days == 90

    def test_constructor_accepts_explicit_value(self):
        """AgentPolicy(evidence_max_age_days=7) stores it."""
        policy = AgentPolicy(catalog=MagicMock(), evidence_max_age_days=7)
        assert policy.evidence_max_age_days == 7

    def test_constructor_accepts_none(self):
        """AgentPolicy(evidence_max_age_days=None) disables freshness."""
        policy = AgentPolicy(catalog=MagicMock(), evidence_max_age_days=None)
        assert policy.evidence_max_age_days is None
