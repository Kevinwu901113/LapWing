"""tests/agents/test_agent_candidate.py — Phase 6B model tests."""

import json

import pytest

from src.agents.candidate import (
    AgentCandidate,
    AgentCandidateFinding,
    AgentEvalEvidence,
    redact_secrets_in_summary,
    validate_candidate_id,
    validate_evidence_id,
)
from src.agents.spec import AgentSpec


class TestAgentEvalEvidence:
    def test_defaults_safe(self):
        ev = AgentEvalEvidence()
        assert ev.evidence_id.startswith("ev_")
        assert ev.evidence_type == "task_success"
        assert ev.passed is True
        assert ev.score is None
        assert ev.trace_id is None
        assert ev.details == {}

    def test_valid_evidence_serializes(self):
        ev = AgentEvalEvidence(
            evidence_id="ev_abc123",
            evidence_type="task_success",
            summary="All checks passed",
            passed=True,
            score=0.95,
            trace_id="trace_xyz",
            details={"checks": 5},
        )
        d = ev.to_dict()
        assert d["evidence_id"] == "ev_abc123"
        assert d["evidence_type"] == "task_success"
        assert d["score"] == 0.95

    def test_valid_evidence_deserializes(self):
        d = {
            "evidence_id": "ev_abc123",
            "evidence_type": "task_success",
            "summary": "test",
            "passed": True,
            "score": 0.8,
            "trace_id": None,
            "details": {},
        }
        ev = AgentEvalEvidence.from_dict(d)
        assert ev.evidence_id == "ev_abc123"
        assert ev.score == 0.8

    def test_round_trip(self):
        ev = AgentEvalEvidence(
            evidence_id="ev_rt",
            evidence_type="manual_review",
            summary="round trip test",
            passed=False,
            score=0.3,
            trace_id="tr_1",
            details={"note": "ok"},
        )
        ev2 = AgentEvalEvidence.from_dict(ev.to_dict())
        assert ev2.evidence_id == ev.evidence_id
        assert ev2.evidence_type == ev.evidence_type
        assert ev2.passed == ev.passed
        assert ev2.score == ev.score
        assert ev2.trace_id == ev.trace_id
        assert ev2.details == ev.details

    def test_invalid_evidence_type_rejected(self):
        with pytest.raises(ValueError, match="evidence_type"):
            AgentEvalEvidence(evidence_type="not_a_real_type")

    def test_all_valid_evidence_types(self):
        for etype in (
            "task_success", "task_failure", "manual_review",
            "policy_lint", "dry_run", "regression_test",
        ):
            ev = AgentEvalEvidence(evidence_id=f"ev_{etype}", evidence_type=etype)
            assert ev.evidence_type == etype

    def test_evidence_id_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="path traversal"):
            AgentEvalEvidence(evidence_id="../../../etc/passwd")

        with pytest.raises(ValueError, match="path traversal"):
            AgentEvalEvidence(evidence_id="..\\windows\\secret")

    def test_score_bounds(self):
        for bad_score in (-0.1, 1.1, 2.0, -100):
            with pytest.raises(ValueError, match="score"):
                AgentEvalEvidence(score=bad_score)

    def test_score_none_allowed(self):
        ev = AgentEvalEvidence(score=None)
        assert ev.score is None

    def test_score_boundary_values(self):
        ev0 = AgentEvalEvidence(score=0.0)
        assert ev0.score == 0.0
        ev1 = AgentEvalEvidence(score=1.0)
        assert ev1.score == 1.0

    def test_passed_round_trip(self):
        ev = AgentEvalEvidence(passed=True)
        assert ev.to_dict()["passed"] is True
        ev2 = AgentEvalEvidence.from_dict({"passed": False})
        assert ev2.passed is False

    def test_details_round_trip(self):
        ev = AgentEvalEvidence(details={"a": 1, "b": [2, 3], "c": {"nested": True}})
        d = ev.to_dict()
        ev2 = AgentEvalEvidence.from_dict(d)
        assert ev2.details == {"a": 1, "b": [2, 3], "c": {"nested": True}}


class TestAgentCandidateFinding:
    def test_defaults_safe(self):
        f = AgentCandidateFinding()
        assert f.severity == "info"
        assert f.code == ""
        assert f.message == ""
        assert f.details == {}

    def test_valid_finding(self):
        f = AgentCandidateFinding(
            severity="warning",
            code="test_warn",
            message="something looks off",
            details={"line": 42},
        )
        assert f.severity == "warning"
        assert f.code == "test_warn"

    def test_round_trip(self):
        f = AgentCandidateFinding(
            severity="error",
            code="E001",
            message="bad thing",
            details={"hint": "fix it"},
        )
        f2 = AgentCandidateFinding.from_dict(f.to_dict())
        assert f2.severity == f.severity
        assert f2.code == f.code
        assert f2.message == f.message
        assert f2.details == f.details

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValueError, match="severity"):
            AgentCandidateFinding(severity="critical")

    def test_all_valid_severities(self):
        for sev in ("info", "warning", "error"):
            f = AgentCandidateFinding(severity=sev)
            assert f.severity == sev


class TestAgentCandidate:
    def test_defaults_safe(self):
        cand = AgentCandidate()
        assert cand.candidate_id.startswith("cand_")
        assert cand.approval_state == "pending"
        assert cand.risk_level == "low"
        assert cand.reason == ""
        assert cand.eval_evidence == []
        assert cand.policy_findings == []
        assert cand.bound_capabilities == []
        assert cand.requested_tools == []
        assert cand.metadata == {}
        assert isinstance(cand.proposed_spec, AgentSpec)

    def test_valid_candidate_serializes(self):
        spec = AgentSpec(name="test_agent", description="test desc")
        cand = AgentCandidate(
            candidate_id="cand_model_test",
            name="test",
            description="test candidate",
            proposed_spec=spec,
            reason="testing serialization",
            risk_level="low",
            approval_state="pending",
        )
        d = cand.to_dict()
        assert d["candidate_id"] == "cand_model_test"
        assert d["proposed_spec"]["name"] == "test_agent"
        assert d["approval_state"] == "pending"

    def test_valid_candidate_deserializes(self):
        spec = AgentSpec(name="test_agent", description="test")
        cand = AgentCandidate(
            candidate_id="cand_deser",
            proposed_spec=spec,
            reason="testing",
        )
        json_str = cand.to_json()
        cand2 = AgentCandidate.from_json(json_str)
        assert cand2.candidate_id == "cand_deser"
        assert cand2.proposed_spec.name == "test_agent"
        assert cand2.approval_state == "pending"

    def test_round_trip_full(self):
        spec = AgentSpec(
            name="full_test",
            description="full round-trip",
            system_prompt="be helpful",
            runtime_profile="agent_researcher",
        )
        ev = AgentEvalEvidence(
            evidence_id="ev_full",
            evidence_type="task_success",
            summary="worked",
        )
        finding = AgentCandidateFinding(
            severity="info",
            code="OK",
            message="all good",
        )
        cand = AgentCandidate(
            candidate_id="cand_full_rt",
            name="full",
            description="full test candidate",
            proposed_spec=spec,
            created_by="testbot",
            source_trace_id="trace_abc",
            source_task_summary="did a thing",
            reason="for testing round-trip",
            approval_state="pending",
            risk_level="medium",
            requested_runtime_profile="agent_researcher",
            requested_tools=["bash", "read"],
            bound_capabilities=["workspace_a1b2c3d4"],
            eval_evidence=[ev],
            policy_findings=[finding],
            version="1",
            metadata={"source": "test"},
        )
        cand2 = AgentCandidate.from_json(cand.to_json())
        assert cand2.candidate_id == cand.candidate_id
        assert cand2.name == cand.name
        assert cand2.description == cand.description
        assert cand2.proposed_spec.name == "full_test"
        assert cand2.proposed_spec.system_prompt == "be helpful"
        assert cand2.created_by == "testbot"
        assert cand2.source_trace_id == "trace_abc"
        assert cand2.source_task_summary == "did a thing"
        assert cand2.reason == cand.reason
        assert cand2.approval_state == "pending"
        assert cand2.risk_level == "medium"
        assert cand2.requested_runtime_profile == "agent_researcher"
        assert cand2.requested_tools == ["bash", "read"]
        assert cand2.bound_capabilities == ["workspace_a1b2c3d4"]
        assert len(cand2.eval_evidence) == 1
        assert cand2.eval_evidence[0].evidence_id == "ev_full"
        assert cand2.eval_evidence[0].summary == "worked"
        assert len(cand2.policy_findings) == 1
        assert cand2.policy_findings[0].code == "OK"
        assert cand2.version == "1"
        assert cand2.metadata == {"source": "test"}

    def test_invalid_candidate_id_rejected(self):
        with pytest.raises(ValueError, match="candidate_id"):
            AgentCandidate(candidate_id="BAD!!!")

    def test_candidate_id_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="path traversal"):
            AgentCandidate(candidate_id="../etc/passwd")

    def test_invalid_approval_state_rejected(self):
        with pytest.raises(ValueError, match="approval_state"):
            AgentCandidate(approval_state="invalid_state")

    def test_all_valid_approval_states(self):
        for state in ("not_required", "pending", "approved", "rejected"):
            cand = AgentCandidate(candidate_id=f"cand_{state}", approval_state=state)
            assert cand.approval_state == state

    def test_invalid_risk_level_rejected(self):
        with pytest.raises(ValueError, match="risk_level"):
            AgentCandidate(risk_level="extreme")

    def test_all_valid_risk_levels(self):
        for level in ("low", "medium", "high"):
            cand = AgentCandidate(candidate_id=f"cand_{level}", risk_level=level)
            assert cand.risk_level == level

    def test_proposed_spec_round_trip(self):
        spec = AgentSpec(
            name="spec_test",
            description="spec",
            system_prompt="be precise",
            model_slot="agent_coder",
            runtime_profile="agent_coder",
            risk_level="medium",
        )
        cand = AgentCandidate(
            candidate_id="cand_spec_rt",
            proposed_spec=spec,
        )
        cand2 = AgentCandidate.from_dict(cand.to_dict())
        assert cand2.proposed_spec.name == "spec_test"
        assert cand2.proposed_spec.system_prompt == "be precise"
        assert cand2.proposed_spec.model_slot == "agent_coder"
        assert cand2.proposed_spec.risk_level == "medium"

    def test_bound_capabilities_round_trip(self):
        spec = AgentSpec()
        cand = AgentCandidate(
            candidate_id="cand_bc_rt",
            proposed_spec=spec,
            bound_capabilities=["workspace_a1b2c3d4", "global_e5f6g7h8"],
        )
        cand2 = AgentCandidate.from_dict(cand.to_dict())
        assert cand2.bound_capabilities == ["workspace_a1b2c3d4", "global_e5f6g7h8"]

    def test_eval_evidence_round_trip(self):
        spec = AgentSpec()
        ev1 = AgentEvalEvidence(evidence_id="ev_1", evidence_type="task_success")
        ev2 = AgentEvalEvidence(evidence_id="ev_2", evidence_type="policy_lint")
        cand = AgentCandidate(
            candidate_id="cand_ev_rt",
            proposed_spec=spec,
            eval_evidence=[ev1, ev2],
        )
        cand2 = AgentCandidate.from_dict(cand.to_dict())
        assert len(cand2.eval_evidence) == 2
        assert cand2.eval_evidence[0].evidence_id == "ev_1"
        assert cand2.eval_evidence[1].evidence_id == "ev_2"

    def test_policy_findings_round_trip(self):
        spec = AgentSpec()
        f1 = AgentCandidateFinding(severity="info", code="I1", message="info")
        f2 = AgentCandidateFinding(severity="warning", code="W1", message="warn")
        cand = AgentCandidate(
            candidate_id="cand_pf_rt",
            proposed_spec=spec,
            policy_findings=[f1, f2],
        )
        cand2 = AgentCandidate.from_dict(cand.to_dict())
        assert len(cand2.policy_findings) == 2
        assert cand2.policy_findings[0].code == "I1"
        assert cand2.policy_findings[1].code == "W1"

    def test_metadata_round_trip(self):
        spec = AgentSpec()
        cand = AgentCandidate(
            candidate_id="cand_meta_rt",
            proposed_spec=spec,
            metadata={"key": "value", "list": [1, 2, 3]},
        )
        cand2 = AgentCandidate.from_dict(cand.to_dict())
        assert cand2.metadata == {"key": "value", "list": [1, 2, 3]}

    def test_no_mutation_of_proposed_spec_during_serialization(self):
        spec = AgentSpec(name="original")
        spec_name_before = spec.name
        spec_prompt_before = spec.system_prompt
        cand = AgentCandidate(
            candidate_id="cand_no_mut",
            proposed_spec=spec,
        )
        _ = cand.to_json()
        assert spec.name == spec_name_before
        assert spec.system_prompt == spec_prompt_before

    def test_legacy_json_without_new_fields(self):
        """JSON without Phase 6B-specific fields should deserialize with defaults."""
        minimal = {
            "candidate_id": "cand_minimal",
            "name": "min",
            "description": "",
            "proposed_spec": {
                "id": "agent_test",
                "name": "test",
                "display_name": "test",
                "description": "",
                "kind": "dynamic",
                "version": 1,
                "status": "active",
                "system_prompt": "",
                "model_slot": "agent_researcher",
                "runtime_profile": "",
                "tool_denylist": [],
                "lifecycle": {"mode": "ephemeral", "ttl_seconds": 3600, "max_runs": 1, "reusable": False},
                "resource_limits": {"max_tool_calls": 20, "max_llm_calls": 8, "max_tokens": 30000, "max_wall_time_seconds": 180, "max_child_agents": 0},
                "created_by": "brain",
                "created_reason": "",
                "created_at": "2026-05-02T00:00:00+08:00",
                "updated_at": "2026-05-02T00:00:00+08:00",
                "bound_capabilities": [],
                "memory_scope": None,
                "risk_level": "low",
                "eval_tasks": [],
                "success_count": 0,
                "failure_count": 0,
                "approval_state": "not_required",
                "allowed_delegation_depth": 0,
                "capability_binding_mode": "metadata_only",
            },
            "created_at": "2026-05-02T00:00:00+08:00",
            "reason": "",
            "approval_state": "pending",
            "risk_level": "low",
            "requested_runtime_profile": None,
            "requested_tools": [],
            "bound_capabilities": [],
            "eval_evidence": [],
            "policy_findings": [],
            "version": "1",
            "metadata": {},
        }
        cand = AgentCandidate.from_dict(minimal)
        assert cand.candidate_id == "cand_minimal"
        assert cand.proposed_spec.name == "test"
        assert cand.approval_state == "pending"
        assert cand.risk_level == "low"
        assert cand.bound_capabilities == []

    def test_tolerates_unknown_extra_keys_in_dict(self):
        """Extra keys in from_dict should be ignored (tolerated)."""
        data = {
            "candidate_id": "cand_extra",
            "name": "extra_test",
            "description": "",
            "proposed_spec": {
                "id": "agent_extra",
                "name": "extra",
                "display_name": "extra",
                "description": "",
                "kind": "dynamic",
                "version": 1,
                "status": "active",
                "system_prompt": "",
                "model_slot": "agent_researcher",
                "runtime_profile": "",
                "tool_denylist": [],
                "lifecycle": {"mode": "ephemeral", "ttl_seconds": 3600, "max_runs": 1, "reusable": False},
                "resource_limits": {"max_tool_calls": 20, "max_llm_calls": 8, "max_tokens": 30000, "max_wall_time_seconds": 180, "max_child_agents": 0},
                "created_by": "brain",
                "created_reason": "",
                "created_at": "2026-05-02T00:00:00+08:00",
                "updated_at": "2026-05-02T00:00:00+08:00",
                "bound_capabilities": [],
                "memory_scope": None,
                "risk_level": "low",
                "eval_tasks": [],
                "success_count": 0,
                "failure_count": 0,
                "approval_state": "not_required",
                "allowed_delegation_depth": 0,
                "capability_binding_mode": "metadata_only",
            },
            "created_at": "2026-05-02T00:00:00+08:00",
            "reason": "",
            "approval_state": "pending",
            "risk_level": "low",
            "requested_tools": [],
            "bound_capabilities": [],
            "eval_evidence": [],
            "policy_findings": [],
            "version": "1",
            "metadata": {},
            "UNKNOWN_FUTURE_FIELD": "should be tolerated",
        }
        cand = AgentCandidate.from_dict(data)
        assert cand.candidate_id == "cand_extra"

    def test_candidate_to_json_produces_valid_json(self):
        cand = AgentCandidate(
            candidate_id="cand_json_test",
            proposed_spec=AgentSpec(name="test"),
        )
        raw = cand.to_json()
        parsed = json.loads(raw)
        assert parsed["candidate_id"] == "cand_json_test"


class TestValidateCandidateId:
    def test_valid_id(self):
        assert validate_candidate_id("cand_abc123") == "cand_abc123"
        assert validate_candidate_id("cand-test") == "cand-test"
        assert validate_candidate_id("a_b-c") == "a_b-c"

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            validate_candidate_id("")

    def test_short_rejected(self):
        with pytest.raises(ValueError, match="must match"):
            validate_candidate_id("ab")

    def test_path_traversal_rejected(self):
        for bad in ("../etc/passwd", "cand/../../root", "cand\\windows"):
            with pytest.raises(ValueError, match="path traversal"):
                validate_candidate_id(bad)

    def test_dot_dot_in_middle_rejected(self):
        with pytest.raises(ValueError, match="path traversal"):
            validate_candidate_id("cand..hidden")


class TestValidateEvidenceId:
    def test_valid_ids(self):
        assert validate_evidence_id("ev_abc123") == "ev_abc123"
        assert validate_evidence_id("ev-test-001") == "ev-test-001"
        assert validate_evidence_id("a") == "a"

    def test_path_traversal_rejected(self):
        for bad in ("../password", "ev/../../secret", "ev\\windows"):
            with pytest.raises(ValueError, match="path traversal"):
                validate_evidence_id(bad)

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            validate_evidence_id("x" * 129)


class TestRedactSecrets:
    def test_api_key_redacted(self):
        result = redact_secrets_in_summary("Used sk-abc123def45678901234567890 for auth")
        assert "sk-" not in result
        assert "REDACTED" in result

    def test_bearer_token_redacted(self):
        result = redact_secrets_in_summary("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
        assert "Bearer [REDACTED" in result

    def test_password_assignment_redacted(self):
        result = redact_secrets_in_summary("Set password=hunter2 for the account")
        assert "hunter2" not in result
        assert "REDACTED" in result

    def test_none_returns_none(self):
        assert redact_secrets_in_summary(None) is None

    def test_clean_text_unchanged(self):
        clean = "The task completed successfully with no errors."
        assert redact_secrets_in_summary(clean) == clean
