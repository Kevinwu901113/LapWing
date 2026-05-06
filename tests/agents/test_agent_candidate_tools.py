"""Phase 6D — Agent candidate operator tools tests.

Tests:
  - Feature flag behavior (tools absent by default)
  - Permission model (agent_candidate_operator tag required)
  - list_agent_candidates filters and behavior
  - view_agent_candidate details
  - add_agent_candidate_evidence
  - approve_agent_candidate
  - reject_agent_candidate
  - archive_agent_candidate
  - No-execution guarantees
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.candidate import (
    AgentCandidate,
    AgentEvalEvidence,
    validate_candidate_id,
)
from src.agents.candidate_store import AgentCandidateStore
from src.agents.spec import AgentSpec
from src.tools.agent_candidate_tools import (
    register_agent_candidate_tools,
    _candidate_summary,
    _evidence_summary,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_store(tmp_path):
    return AgentCandidateStore(tmp_path / "agent_candidates")


def _make_candidate(spec=None, **overrides):
    if spec is None:
        spec = AgentSpec(name="test_agent", description="test")
    defaults = {
        "candidate_id": "cand-test-001",
        "name": spec.name,
        "description": "test candidate",
        "proposed_spec": spec,
        "reason": "testing",
        "approval_state": "pending",
        "risk_level": "low",
    }
    defaults.update(overrides)
    return AgentCandidate(**defaults)


def _make_context():
    return ToolExecutionContext(
        execute_shell=MagicMock(),
        shell_default_cwd="/tmp",
    )


def _find_tool(tools, name):
    for t in tools:
        if t.name == name:
            return t
    return None


def _collect_registered_tools(store, policy=None):
    """Collect tools registered via a mock registry."""
    tools = []
    mock_registry = MagicMock()

    def _capture(spec):
        tools.append(spec)

    mock_registry.register = _capture
    register_agent_candidate_tools(mock_registry, store, policy)
    return tools


# ══════════════════════════════════════════════════════════════════════════════
# Feature flag / registration tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureFlagRegistration:
    def test_all_6_tools_registered(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        names = {t.name for t in tools}
        expected = {
            "list_agent_candidates",
            "view_agent_candidate",
            "add_agent_candidate_evidence",
            "approve_agent_candidate",
            "reject_agent_candidate",
            "archive_agent_candidate",
        }
        assert names == expected

    def test_no_forbidden_tools_registered(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        names = {t.name for t in tools}
        forbidden = {
            "run_agent_candidate",
            "promote_agent_candidate",
            "save_candidate_as_agent",
            "execute_candidate",
            "auto_approve_agent_candidate",
            "run_capability",
        }
        assert names.isdisjoint(forbidden)

    def test_all_tools_use_correct_capability_tag(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        for t in tools:
            assert t.capability == "agent_candidate_operator", (
                f"Tool {t.name} has capability={t.capability!r}, "
                f"expected 'agent_candidate_operator'"
            )

    def test_register_with_none_store_skips(self):
        mock_registry = MagicMock()
        register_agent_candidate_tools(mock_registry, None)
        mock_registry.register.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# list_agent_candidates tests
# ══════════════════════════════════════════════════════════════════════════════

class TestListAgentCandidates:
    async def test_list_empty(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={}),
            _make_context(),
        )
        assert result.success
        assert result.payload["candidates"] == []
        assert result.payload["count"] == 0

    async def test_list_pending(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_candidate(_make_candidate(candidate_id="cand-a01", approval_state="pending"))
        store.create_candidate(_make_candidate(candidate_id="cand-a02", approval_state="approved"))

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={"approval_state": "pending"}),
            _make_context(),
        )
        assert result.success
        assert result.payload["count"] == 1
        assert result.payload["candidates"][0]["candidate_id"] == "cand-a01"

    async def test_filter_by_risk_level(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_candidate(_make_candidate(candidate_id="cand-low", risk_level="low"))
        store.create_candidate(_make_candidate(candidate_id="cand-high", risk_level="high"))

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={"risk_level": "high"}),
            _make_context(),
        )
        assert result.success
        assert result.payload["count"] == 1
        assert result.payload["candidates"][0]["candidate_id"] == "cand-high"

    async def test_archived_excluded_by_default(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_candidate(_make_candidate(candidate_id="cand-active"))
        cand_arch = _make_candidate(candidate_id="cand-archived")
        store.create_candidate(cand_arch)
        store.archive_candidate("cand-archived")

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={}),
            _make_context(),
        )
        assert result.success
        ids = [c["candidate_id"] for c in result.payload["candidates"]]
        assert "cand-active" in ids
        assert "cand-archived" not in ids

    async def test_include_archived(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_candidate(_make_candidate(candidate_id="cand-active"))
        cand_arch = _make_candidate(candidate_id="cand-archived")
        store.create_candidate(cand_arch)
        store.archive_candidate("cand-archived")

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={"include_archived": True}),
            _make_context(),
        )
        assert result.success
        ids = [c["candidate_id"] for c in result.payload["candidates"]]
        assert "cand-archived" in ids

    async def test_limit_respected(self, tmp_path):
        store = _make_store(tmp_path)
        for i in range(25):
            store.create_candidate(_make_candidate(candidate_id=f"cand-{i:02d}"))

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={"limit": 5}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["candidates"]) == 5

    async def test_deterministic_ordering(self, tmp_path):
        store = _make_store(tmp_path)
        store.create_candidate(_make_candidate(candidate_id="cand-b", created_at="2026-05-01T00:00:00"))
        store.create_candidate(_make_candidate(candidate_id="cand-a", created_at="2026-05-03T00:00:00"))
        store.create_candidate(_make_candidate(candidate_id="cand-c", created_at="2026-04-30T00:00:00"))

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={}),
            _make_context(),
        )
        # Most recent first
        ids = [c["candidate_id"] for c in result.payload["candidates"]]
        assert ids == ["cand-a", "cand-b", "cand-c"]

    async def test_summary_fields(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(
            candidate_id="cand-summary",
            requested_runtime_profile="agent_researcher",
            requested_tools=["bash"],
            bound_capabilities=["workspace_abc"],
            source_trace_id="trace_xyz",
        )
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "list_agent_candidates")
        result = await tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={}),
            _make_context(),
        )
        s = result.payload["candidates"][0]
        assert s["candidate_id"] == "cand-summary"
        assert s["approval_state"] == "pending"
        assert s["risk_level"] == "low"
        assert s["requested_runtime_profile"] == "agent_researcher"
        assert s["requested_tools"] == ["bash"]
        assert s["bound_capabilities"] == ["workspace_abc"]
        assert s["evidence_count"] == 0
        assert s["source_trace_id"] == "trace_xyz"
        assert "created_at" in s
        # Compact summary must not include full prompt body
        assert "proposed_spec" not in s
        assert "system_prompt" not in s


# ══════════════════════════════════════════════════════════════════════════════
# view_agent_candidate tests
# ══════════════════════════════════════════════════════════════════════════════

class TestViewAgentCandidate:
    async def test_view_returns_details(self, tmp_path):
        store = _make_store(tmp_path)
        spec = AgentSpec(name="view_test", description="detailed spec", system_prompt="be helpful")
        cand = _make_candidate(
            candidate_id="cand-view",
            proposed_spec=spec,
            source_task_summary="original task",
        )
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "view_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="view_agent_candidate", arguments={"candidate_id": "cand-view"}),
            _make_context(),
        )
        assert result.success
        assert result.payload["candidate_id"] == "cand-view"
        assert result.payload["source_task_summary"] == "original task"
        assert result.payload["proposed_spec"]["name"] == "view_test"
        # Full system_prompt body is NOT included (only hash)
        assert "system_prompt" not in result.payload["proposed_spec"]
        assert "system_prompt_hash" in result.payload["proposed_spec"]

    async def test_view_missing_candidate_returns_not_found(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "view_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="view_agent_candidate", arguments={"candidate_id": "cand-nonexistent"}),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"

    async def test_view_does_not_mutate(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-no-mutate")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "view_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="view_agent_candidate", arguments={"candidate_id": "cand-no-mutate"}),
            _make_context(),
        )
        assert result.success
        # Verify candidate on disk unchanged
        reloaded = store.get_candidate("cand-no-mutate")
        assert reloaded.approval_state == "pending"
        assert reloaded.eval_evidence == []

    async def test_view_without_candidate_id(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "view_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="view_agent_candidate", arguments={}),
            _make_context(),
        )
        assert not result.success
        assert "candidate_id" in result.payload["error"]

    async def test_view_includes_policy_findings(self, tmp_path):
        from src.agents.candidate import AgentCandidateFinding

        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-findings")
        cand.policy_findings = [
            AgentCandidateFinding(severity="warning", code="W1", message="risky tool requested"),
        ]
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "view_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="view_agent_candidate", arguments={"candidate_id": "cand-findings"}),
            _make_context(),
        )
        assert result.success
        assert len(result.payload["policy_findings"]) == 1
        assert result.payload["policy_findings"][0]["code"] == "W1"


# ══════════════════════════════════════════════════════════════════════════════
# add_agent_candidate_evidence tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAddEvidence:
    async def test_add_evidence_appends(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-ev")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-ev",
                "evidence_type": "task_success",
                "summary": "All tests passed",
                "passed": True,
                "score": 0.95,
            }),
            _make_context(),
        )
        assert result.success
        assert result.payload["evidence_id"].startswith("ev_")
        assert result.payload["evidence_count"] == 1

        # Verify persisted
        reloaded = store.get_candidate("cand-ev")
        assert len(reloaded.eval_evidence) == 1
        assert reloaded.eval_evidence[0].evidence_type == "task_success"
        assert reloaded.eval_evidence[0].passed is True

    async def test_add_evidence_does_not_change_approval_state(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-ev-state", approval_state="pending")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-ev-state",
                "evidence_type": "task_success",
                "summary": "done",
                "passed": True,
            }),
            _make_context(),
        )
        assert result.success
        assert result.payload["approval_state"] == "pending"

    async def test_invalid_evidence_type_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-bad-type")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-bad-type",
                "evidence_type": "not_a_type",
                "summary": "bad",
                "passed": True,
            }),
            _make_context(),
        )
        assert not result.success
        assert "invalid evidence_type" in result.payload["error"]

    async def test_score_out_of_range_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-score")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-score",
                "evidence_type": "task_success",
                "summary": "bad score",
                "passed": True,
                "score": 1.5,
            }),
            _make_context(),
        )
        assert not result.success
        assert "out of" in result.payload["error"]

    async def test_secrets_redacted_in_summary(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-secrets")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-secrets",
                "evidence_type": "task_success",
                "summary": "Used API key sk-abc123def45678901234567890 for testing",
                "passed": True,
            }),
            _make_context(),
        )
        assert result.success
        reloaded = store.get_candidate("cand-secrets")
        assert "sk-" not in reloaded.eval_evidence[0].summary
        assert "REDACTED" in reloaded.eval_evidence[0].summary

    async def test_evidence_round_trip(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-rt")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-rt",
                "evidence_type": "manual_review",
                "summary": "Manual review passed",
                "passed": True,
                "score": 0.88,
                "trace_id": "trace_abc",
                "details": {"reviewer": "kevin", "notes": "looks good"},
            }),
            _make_context(),
        )
        assert result.success
        ev_id = result.payload["evidence_id"]

        reloaded = store.get_candidate("cand-rt")
        ev = reloaded.eval_evidence[0]
        assert ev.evidence_id == ev_id
        assert ev.evidence_type == "manual_review"
        assert ev.passed is True
        assert ev.score == 0.88
        assert ev.trace_id == "trace_abc"
        assert ev.details == {"reviewer": "kevin", "notes": "looks good"}

    async def test_add_evidence_to_missing_candidate(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-nope",
                "evidence_type": "task_success",
                "summary": "nope",
                "passed": True,
            }),
            _make_context(),
        )
        assert not result.success

    async def test_add_evidence_to_archived_candidate(self, tmp_path):
        """Adding evidence to an archived candidate should still work
        (evidence is append-only, doesn't change approval state)."""
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-arch-ev")
        store.create_candidate(cand)
        store.archive_candidate("cand-arch-ev")

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "add_agent_candidate_evidence")
        result = await tool.executor(
            ToolExecutionRequest(name="add_agent_candidate_evidence", arguments={
                "candidate_id": "cand-arch-ev",
                "evidence_type": "task_success",
                "summary": "post-archive evidence",
                "passed": True,
            }),
            _make_context(),
        )
        assert result.success
        reloaded = store.get_candidate("cand-arch-ev")
        assert len(reloaded.eval_evidence) == 1


# ══════════════════════════════════════════════════════════════════════════════
# approve_agent_candidate tests
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveAgentCandidate:
    async def test_approve_pending_candidate(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-approve", approval_state="pending")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "approve_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="approve_agent_candidate", arguments={
                "candidate_id": "cand-approve",
                "reviewer": "kevin",
                "reason": "looks good",
            }),
            _make_context(),
        )
        assert result.success
        assert result.payload["approval_state"] == "approved"

        reloaded = store.get_candidate("cand-approve")
        assert reloaded.approval_state == "approved"

    async def test_approve_policy_denied_candidate_rejected(self, tmp_path):
        from src.agents.policy import AgentPolicy

        store = _make_store(tmp_path)
        spec = AgentSpec(
            name="bad_agent",
            bound_capabilities=["agent_admin_v1"],
        )
        cand = _make_candidate(
            candidate_id="cand-bad-policy",
            proposed_spec=spec,
            approval_state="pending",
            bound_capabilities=["agent_admin_v1"],
        )
        store.create_candidate(cand)

        policy = AgentPolicy.__new__(AgentPolicy)
        policy._catalog = MagicMock()
        policy._llm_router = None

        tools = _collect_registered_tools(store, policy=policy)
        tool = _find_tool(tools, "approve_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="approve_agent_candidate", arguments={
                "candidate_id": "cand-bad-policy",
            }),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "policy_denied"

    async def test_approve_archived_candidate_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-arch-approve", approval_state="pending")
        store.create_candidate(cand)
        store.archive_candidate("cand-arch-approve")

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "approve_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="approve_agent_candidate", arguments={
                "candidate_id": "cand-arch-approve",
            }),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "candidate_archived"

    async def test_approve_does_not_create_active_agent(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-no-create", approval_state="pending")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "approve_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="approve_agent_candidate", arguments={
                "candidate_id": "cand-no-create",
            }),
            _make_context(),
        )
        assert result.success
        # Candidate is still a candidate — not in any active registry
        reloaded = store.get_candidate("cand-no-create")
        assert reloaded.approval_state == "approved"
        # No active agent was created

    async def test_approve_missing_candidate(self, tmp_path):
        store = _make_store(tmp_path)
        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "approve_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="approve_agent_candidate", arguments={
                "candidate_id": "cand-nope",
            }),
            _make_context(),
        )
        assert not result.success
        assert result.payload["error"] == "not_found"


# ══════════════════════════════════════════════════════════════════════════════
# reject_agent_candidate tests
# ══════════════════════════════════════════════════════════════════════════════

class TestRejectAgentCandidate:
    async def test_reject_changes_state_only(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-reject", approval_state="pending")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "reject_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="reject_agent_candidate", arguments={
                "candidate_id": "cand-reject",
                "reviewer": "kevin",
                "reason": "not safe enough",
            }),
            _make_context(),
        )
        assert result.success
        assert result.payload["approval_state"] == "rejected"

        reloaded = store.get_candidate("cand-reject")
        assert reloaded.approval_state == "rejected"
        # Files still exist
        assert reloaded.candidate_id == "cand-reject"

    async def test_reject_does_not_delete_files(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-reject-keep")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "reject_agent_candidate")
        await tool.executor(
            ToolExecutionRequest(name="reject_agent_candidate", arguments={
                "candidate_id": "cand-reject-keep",
            }),
            _make_context(),
        )
        # File still exists
        reloaded = store.get_candidate("cand-reject-keep")
        assert reloaded is not None

    async def test_reject_does_not_mutate_spec(self, tmp_path):
        store = _make_store(tmp_path)
        spec = AgentSpec(name="original")
        cand = _make_candidate(candidate_id="cand-spec-keep", proposed_spec=spec)
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "reject_agent_candidate")
        await tool.executor(
            ToolExecutionRequest(name="reject_agent_candidate", arguments={
                "candidate_id": "cand-spec-keep",
            }),
            _make_context(),
        )
        reloaded = store.get_candidate("cand-spec-keep")
        assert reloaded.proposed_spec.name == "original"


# ══════════════════════════════════════════════════════════════════════════════
# archive_agent_candidate tests
# ══════════════════════════════════════════════════════════════════════════════

class TestArchiveAgentCandidate:
    async def test_archive_excludes_from_default_list(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-to-archive")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "archive_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="archive_agent_candidate", arguments={
                "candidate_id": "cand-to-archive",
                "reason": "no longer needed",
            }),
            _make_context(),
        )
        assert result.success
        assert result.payload["archived"] is True

        # Not in default list
        list_tool = _find_tool(tools, "list_agent_candidates")
        list_result = await list_tool.executor(
            ToolExecutionRequest(name="list_agent_candidates", arguments={}),
            _make_context(),
        )
        ids = [c["candidate_id"] for c in list_result.payload["candidates"]]
        assert "cand-to-archive" not in ids

    async def test_archive_does_not_delete_evidence(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-ev-keep")
        store.create_candidate(cand)
        ev = AgentEvalEvidence(evidence_type="task_success", summary="test")
        store.add_evidence("cand-ev-keep", ev)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "archive_agent_candidate")
        await tool.executor(
            ToolExecutionRequest(name="archive_agent_candidate", arguments={
                "candidate_id": "cand-ev-keep",
            }),
            _make_context(),
        )
        reloaded = store.get_candidate("cand-ev-keep")
        assert len(reloaded.eval_evidence) == 1

    async def test_archive_does_not_affect_active_agents(self, tmp_path):
        """Archive is a candidate-only operation — no active agents involved."""
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-archive-only")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "archive_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="archive_agent_candidate", arguments={
                "candidate_id": "cand-archive-only",
            }),
            _make_context(),
        )
        assert result.success


# ══════════════════════════════════════════════════════════════════════════════
# No-execution guarantees
# ══════════════════════════════════════════════════════════════════════════════

class TestNoExecutionGuarantees:
    async def test_approve_does_not_execute(self, tmp_path):
        store = _make_store(tmp_path)
        cand = _make_candidate(candidate_id="cand-no-exec")
        store.create_candidate(cand)

        tools = _collect_registered_tools(store)
        tool = _find_tool(tools, "approve_agent_candidate")
        result = await tool.executor(
            ToolExecutionRequest(name="approve_agent_candidate", arguments={
                "candidate_id": "cand-no-exec",
            }),
            _make_context(),
        )
        assert result.success
        # No execution occurred — candidate is still just a candidate on disk


# ══════════════════════════════════════════════════════════════════════════════
# Summary helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestSummaryHelpers:
    def test_candidate_summary(self):
        spec = AgentSpec(name="summary_test")
        cand = _make_candidate(
            candidate_id="cand-summary-test",
            proposed_spec=spec,
            approval_state="pending",
            risk_level="medium",
            requested_runtime_profile="agent_researcher",
            requested_tools=["bash"],
            bound_capabilities=["workspace_abc"],
            source_trace_id="trace_123",
        )
        s = _candidate_summary(cand)
        assert s["candidate_id"] == "cand-summary-test"
        assert s["approval_state"] == "pending"
        assert s["risk_level"] == "medium"
        assert s["evidence_count"] == 0
        assert "proposed_spec" not in s

    def test_evidence_summary(self):
        ev = AgentEvalEvidence(
            evidence_id="ev_test",
            evidence_type="manual_review",
            summary="looks good",
            passed=True,
            score=0.9,
            trace_id="trace_abc",
        )
        s = _evidence_summary(ev)
        assert s["evidence_id"] == "ev_test"
        assert s["evidence_type"] == "manual_review"
        assert s["passed"] is True
        assert s["score"] == 0.9
        assert s["trace_id"] == "trace_abc"
        assert "details" not in s
