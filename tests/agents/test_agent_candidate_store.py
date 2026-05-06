"""tests/agents/test_agent_candidate_store.py — Phase 6B store tests."""

import json
import os
from pathlib import Path

import pytest

from src.agents.candidate import (
    AgentCandidate,
    AgentCandidateFinding,
    AgentEvalEvidence,
)
from src.agents.candidate_store import (
    AgentCandidateStore,
    CandidateStoreError,
)
from src.agents.spec import AgentSpec


@pytest.fixture
def store(tmp_path):
    return AgentCandidateStore(tmp_path / "agent_candidates")


@pytest.fixture
def minimal_candidate():
    spec = AgentSpec(name="test_agent", description="test")
    return AgentCandidate(
        candidate_id="cand_store_min",
        name="test",
        description="test candidate",
        proposed_spec=spec,
        reason="store testing",
    )


class TestCreateCandidate:
    def test_create_writes_candidate_json(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        candidate_file = (
            store._base_dir / "cand_store_min" / "candidate.json"
        )
        assert candidate_file.exists()
        raw = candidate_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["candidate_id"] == "cand_store_min"

    def test_create_creates_evidence_dir(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        evidence_dir = store._base_dir / "cand_store_min" / "evidence"
        assert evidence_dir.is_dir()

    def test_duplicate_candidate_id_rejected(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        dup = AgentCandidate(
            candidate_id="cand_store_min",
            name="dup",
            proposed_spec=AgentSpec(),
        )
        with pytest.raises(CandidateStoreError, match="already exists"):
            store.create_candidate(dup)


class TestGetCandidate:
    def test_get_returns_candidate(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        cand = store.get_candidate("cand_store_min")
        assert cand.candidate_id == "cand_store_min"
        assert cand.name == "test"
        assert cand.proposed_spec.name == "test_agent"

    def test_get_nonexistent_raises(self, store):
        with pytest.raises(CandidateStoreError, match="not found"):
            store.get_candidate("cand_nonexist")

    def test_get_or_none_returns_none(self, store):
        assert store.get_candidate_or_none("cand_nonexist") is None

    def test_get_or_none_returns_candidate(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        cand = store.get_candidate_or_none("cand_store_min")
        assert cand is not None
        assert cand.candidate_id == "cand_store_min"


class TestListCandidates:
    def test_empty_store_returns_empty(self, store):
        assert store.list_candidates() == []

    def test_lists_all_candidates(self, store):
        for i in range(3):
            spec = AgentSpec(name=f"agent_{i}")
            cand = AgentCandidate(
                candidate_id=f"cand_list_{i}",
                name=f"test_{i}",
                proposed_spec=spec,
            )
            store.create_candidate(cand)
        all_cands = store.list_candidates()
        assert len(all_cands) == 3

    def test_filter_by_approval_state(self, store):
        spec = AgentSpec()
        approved = AgentCandidate(
            candidate_id="cand_approved",
            proposed_spec=spec,
            approval_state="approved",
        )
        pending = AgentCandidate(
            candidate_id="cand_pending",
            proposed_spec=spec,
            approval_state="pending",
        )
        store.create_candidate(approved)
        store.create_candidate(pending)

        result = store.list_candidates(approval_state="approved")
        assert len(result) == 1
        assert result[0].candidate_id == "cand_approved"

        result2 = store.list_candidates(approval_state="rejected")
        assert len(result2) == 0

    def test_filter_by_risk_level(self, store):
        spec = AgentSpec()
        low = AgentCandidate(
            candidate_id="cand_low_r",
            proposed_spec=spec,
            risk_level="low",
        )
        high = AgentCandidate(
            candidate_id="cand_high_r",
            proposed_spec=spec,
            risk_level="high",
        )
        store.create_candidate(low)
        store.create_candidate(high)

        result = store.list_candidates(risk_level="high")
        assert len(result) == 1
        assert result[0].candidate_id == "cand_high_r"

    def test_skips_directories_without_candidate_json(self, store, tmp_path):
        spec = AgentSpec()
        cand = AgentCandidate(candidate_id="cand_valid", proposed_spec=spec)
        store.create_candidate(cand)
        # Create a directory without candidate.json
        (store._base_dir / "cand_empty").mkdir(parents=True, exist_ok=True)
        result = store.list_candidates()
        assert len(result) == 1
        assert result[0].candidate_id == "cand_valid"

    def test_skips_corrupt_candidate_file(self, store, tmp_path):
        spec = AgentSpec()
        cand = AgentCandidate(candidate_id="cand_good", proposed_spec=spec)
        store.create_candidate(cand)
        # Corrupt another candidate
        bad_dir = store._base_dir / "cand_bad"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "candidate.json").write_text("not valid json {{{", encoding="utf-8")
        result = store.list_candidates()
        assert len(result) == 1
        assert result[0].candidate_id == "cand_good"


class TestAddEvidence:
    def test_adds_evidence_and_writes_file(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        ev = AgentEvalEvidence(
            evidence_id="ev_store_1",
            evidence_type="task_success",
            summary="passed",
        )
        updated = store.add_evidence("cand_store_min", ev)
        assert len(updated.eval_evidence) == 1
        assert updated.eval_evidence[0].evidence_id == "ev_store_1"

        ev_file = (
            store._base_dir / "cand_store_min" / "evidence" / "ev_store_1.json"
        )
        assert ev_file.exists()

    def test_multiple_evidence_appended(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        ev1 = AgentEvalEvidence(evidence_id="ev_1", evidence_type="task_success")
        ev2 = AgentEvalEvidence(evidence_id="ev_2", evidence_type="policy_lint")
        store.add_evidence("cand_store_min", ev1)
        updated = store.add_evidence("cand_store_min", ev2)
        assert len(updated.eval_evidence) == 2

    def test_add_evidence_to_nonexistent_candidate(self, store):
        ev = AgentEvalEvidence()
        with pytest.raises(CandidateStoreError, match="not found"):
            store.add_evidence("cand_nonexist", ev)


class TestUpdateApproval:
    def test_updates_approval_state(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        updated = store.update_approval(
            "cand_store_min",
            "approved",
            reviewer="testbot",
            reason="looks good",
        )
        assert updated.approval_state == "approved"
        assert updated.metadata["reviewer"] == "testbot"
        assert updated.metadata["approval_reason"] == "looks good"

    def test_invalid_approval_state_rejected(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        with pytest.raises(CandidateStoreError, match="invalid approval_state"):
            store.update_approval("cand_store_min", "bogus")

    def test_persistence_survives_reread(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        store.update_approval("cand_store_min", "approved")
        cand = store.get_candidate("cand_store_min")
        assert cand.approval_state == "approved"


class TestArchiveCandidate:
    def test_archive_does_not_delete_files(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        candidate_file = store._base_dir / "cand_store_min" / "candidate.json"
        assert candidate_file.exists()
        store.archive_candidate("cand_store_min", reason="done")
        assert candidate_file.exists()  # Still exists

    def test_archive_sets_metadata(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        archived = store.archive_candidate("cand_store_min", reason="done")
        assert archived.metadata["archived"] is True
        assert archived.metadata["archive_reason"] == "done"
        assert "archived_at" in archived.metadata

    def test_archive_persists(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        store.archive_candidate("cand_store_min", reason="done")
        cand = store.get_candidate("cand_store_min")
        assert cand.metadata["archived"] is True


class TestUpdateCandidate:
    def test_update_overwrites(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        minimal_candidate.reason = "updated reason"
        store.update_candidate(minimal_candidate)
        cand = store.get_candidate("cand_store_min")
        assert cand.reason == "updated reason"

    def test_update_nonexistent_raises(self, store):
        cand = AgentCandidate(candidate_id="cand_noexist", proposed_spec=AgentSpec())
        with pytest.raises(CandidateStoreError, match="does not exist"):
            store.update_candidate(cand)


class TestPathSafety:
    def test_candidate_path_traversal_rejected_on_create(self, store):
        spec = AgentSpec()
        cand = AgentCandidate(
            candidate_id="cand-ok",  # valid id to pass constructor
            proposed_spec=spec,
        )
        # Force a bad id after construction
        cand.candidate_id = "../etc/passwd"
        with pytest.raises(ValueError, match="path traversal"):
            store.create_candidate(cand)

    def test_evidence_path_traversal_rejected(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        ev = AgentEvalEvidence(evidence_id="ev_ok")
        ev.evidence_id = "../../../etc/passwd"
        with pytest.raises(ValueError, match="path traversal"):
            store.add_evidence("cand_store_min", ev)


class TestCandidateStoreDoesNotCreateActiveAgentFiles:
    def test_no_active_agent_files(self, store, minimal_candidate, tmp_path):
        store.create_candidate(minimal_candidate)
        # AgentCatalog uses SQLite at a specific path — candidate store
        # should not touch it.
        db_path = tmp_path / "lapwing.db"
        assert not db_path.exists()

    def test_store_uses_base_dir_not_catalog_dir(self, store, minimal_candidate):
        store.create_candidate(minimal_candidate)
        # All files should be under the store's base_dir
        base = str(store._base_dir.resolve())
        for root, _dirs, files in os.walk(str(store._base_dir)):
            for f in files:
                full = os.path.join(root, f)
                assert full.startswith(base), f"File {full} outside base dir"


class TestCorruptFileHandling:
    def test_corrupt_json_raises_on_read(self, store, tmp_path):
        cand_dir = store._base_dir / "cand_corrupt"
        cand_dir.mkdir(parents=True)
        (cand_dir / "candidate.json").write_text("not json", encoding="utf-8")
        with pytest.raises(CandidateStoreError, match="corrupt JSON"):
            store.get_candidate("cand_corrupt")

    def test_valid_json_bad_schema_raises(self, store, tmp_path):
        cand_dir = store._base_dir / "cand_bad_schema"
        cand_dir.mkdir(parents=True)
        (cand_dir / "candidate.json").write_text(
            '{"candidate_id": "cand_bad_schema", "proposed_spec": "not_a_dict"}',
            encoding="utf-8",
        )
        with pytest.raises(CandidateStoreError, match="failed deserialization"):
            store.get_candidate("cand_bad_schema")


class TestStoreRoundTrip:
    def test_full_round_trip(self, store):
        spec = AgentSpec(
            name="round_trip_agent",
            description="full round trip test",
            system_prompt="be helpful",
            runtime_profile="agent_researcher",
        )
        cand = AgentCandidate(
            candidate_id="cand_rt_full",
            name="round_trip",
            description="testing full round-trip",
            proposed_spec=spec,
            created_by="testbot",
            reason="integration test",
            risk_level="medium",
            approval_state="pending",
            requested_runtime_profile="agent_coder",
            requested_tools=["bash", "read"],
            bound_capabilities=["workspace_abc123"],
            metadata={"origin": "test"},
        )
        store.create_candidate(cand)

        # Add evidence
        ev = AgentEvalEvidence(
            evidence_id="ev_rt_1",
            evidence_type="task_success",
            summary="passed",
            score=0.95,
        )
        store.add_evidence("cand_rt_full", ev)

        # Update approval
        store.update_approval("cand_rt_full", "approved", reviewer="auto")

        # Read back
        cand2 = store.get_candidate("cand_rt_full")
        assert cand2.candidate_id == "cand_rt_full"
        assert cand2.name == "round_trip"
        assert cand2.proposed_spec.name == "round_trip_agent"
        assert cand2.proposed_spec.system_prompt == "be helpful"
        assert cand2.created_by == "testbot"
        assert cand2.risk_level == "medium"
        assert cand2.approval_state == "approved"
        assert cand2.requested_runtime_profile == "agent_coder"
        assert cand2.requested_tools == ["bash", "read"]
        assert cand2.bound_capabilities == ["workspace_abc123"]
        assert len(cand2.eval_evidence) == 1
        assert cand2.eval_evidence[0].evidence_id == "ev_rt_1"
        assert cand2.eval_evidence[0].score == 0.95
        assert cand2.metadata["origin"] == "test"
        assert cand2.metadata["reviewer"] == "auto"
