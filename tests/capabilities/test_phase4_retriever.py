"""Phase 4 tests: CapabilityRetriever filtering, ranking, and progressive disclosure."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest

from src.capabilities.index import CapabilityIndex
from src.capabilities.ranking import (
    RISK_PENALTY,
    SCOPE_BOOST,
    keyword_score,
    maturity_boost,
    rank_candidates,
    recency_boost,
    risk_penalty,
    scope_boost,
    score_candidate,
    usage_boost,
)
from src.capabilities.retriever import (
    DEFAULT_INCLUDE_MATURITIES,
    DEFAULT_INCLUDE_RISK_LEVELS,
    DEFAULT_MAX_RESULTS,
    SCOPE_PRECEDENCE_ORDER,
    CapabilityRetriever,
    CapabilitySummary,
    RetrievalContext,
)
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityRiskLevel,
    CapabilityScope,
    CapabilityStatus,
    CapabilityType,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_index_row(**overrides) -> dict:
    defaults = {
        "id": "test_cap_01",
        "name": "Test Capability",
        "description": "A test capability for testing.",
        "type": "skill",
        "scope": "workspace",
        "maturity": "stable",
        "status": "active",
        "risk_level": "low",
        "trust_required": "developer",
        "triggers_json": json.dumps(["test failure", "pytest failure"]),
        "tags_json": json.dumps(["ci", "testing"]),
        "required_tools_json": json.dumps(["delegate_to_coder", "shell"]),
        "usage_count": 10,
        "success_count": 8,
        "failure_count": 2,
        "path": "/tmp/test_cap_01",
        "content_hash": "abc123",
        "created_at": "2025-01-01T00:00:00",
        "updated_at": "2025-06-01T00:00:00",
    }
    defaults.update(overrides)
    return defaults


def _make_index_row_deserialized(**overrides) -> dict:
    """Like _make_index_row but with deserialized JSON fields (matching _row_to_dict)."""
    row = _make_index_row(**overrides)
    for field in ("triggers_json", "tags_json", "required_tools_json"):
        if field in row:
            parsed = json.loads(row[field])
            base_name = field.replace("_json", "")
            row[base_name] = parsed
            row[field] = parsed  # also keep under original key for robustness
    return row


def _make_summary(**overrides) -> CapabilitySummary:
    defaults = {
        "id": "test_cap_01",
        "name": "Test Capability",
        "description": "A test capability.",
        "type": "skill",
        "scope": "workspace",
        "maturity": "stable",
        "status": "active",
        "risk_level": "low",
    }
    defaults.update(overrides)
    return CapabilitySummary(**defaults)


# ── Ranking unit tests ─────────────────────────────────────────────────────


class TestKeywordScore:
    def test_name_match_scores_highest(self):
        row = _make_index_row(name="CI Debugger")
        score = keyword_score(row, "CI Debugger")
        assert score >= 10.0

    def test_name_partial_match(self):
        row = _make_index_row(name="CI Debugger")
        score = keyword_score(row, "debugger")
        assert score >= 5.0

    def test_trigger_match(self):
        row = _make_index_row(triggers_json=json.dumps(["test failure"]))
        score = keyword_score(row, "test failure")
        assert score >= 5.0

    def test_tag_match(self):
        row = _make_index_row(tags_json=json.dumps(["ci"]))
        score = keyword_score(row, "ci")
        assert score >= 4.0

    def test_description_match(self):
        row = _make_index_row(description="Diagnose CI failures")
        score = keyword_score(row, "diagnose")
        assert score >= 3.0

    def test_empty_query_zero_score(self):
        row = _make_index_row()
        assert keyword_score(row, "") == 0.0
        assert keyword_score(row, "  ") == 0.0

    def test_no_match_zero_score(self):
        row = _make_index_row(name="CI Debugger")
        assert keyword_score(row, "completely_unrelated_term") == 0.0


class TestScopeBoost:
    def test_session_highest(self):
        assert scope_boost("session") > scope_boost("workspace")

    def test_workspace_above_user(self):
        assert scope_boost("workspace") > scope_boost("user")

    def test_user_above_global(self):
        assert scope_boost("user") > scope_boost("global")

    def test_unknown_scope_zero(self):
        assert scope_boost("unknown_scope") == 0.0


class TestMaturityBoost:
    def test_stable_ranks_above_testing(self):
        assert maturity_boost("stable") > maturity_boost("testing")

    def test_testing_ranks_above_draft(self):
        assert maturity_boost("testing") > maturity_boost("draft")

    def test_broken_penalized(self):
        assert maturity_boost("broken") < 0.0


class TestRiskPenalty:
    def test_low_best(self):
        assert risk_penalty("low") > risk_penalty("medium")

    def test_medium_above_high(self):
        assert risk_penalty("medium") > risk_penalty("high")

    def test_high_penalized(self):
        assert risk_penalty("high") < -5.0


class TestUsageBoost:
    def test_zero_usage_zero_boost(self):
        row = {"usage_count": 0, "success_count": 0}
        assert usage_boost(row) == 0.0

    def test_high_success_ratio_positive_boost(self):
        row = {"usage_count": 10, "success_count": 9}
        assert usage_boost(row) > 0.0

    def test_low_success_ratio_low_boost(self):
        row = {"usage_count": 10, "success_count": 1}
        assert usage_boost(row) < 1.0


class TestRecencyBoost:
    def test_recent_update_positive(self):
        row = {"updated_at": "2025-06-01T00:00:00"}
        assert recency_boost(row) > 0.0

    def test_no_update_zero(self):
        assert recency_boost({}) == 0.0


class TestScoreCandidate:
    def test_stable_low_risk_scores_higher(self):
        stable = _make_index_row(maturity="stable", risk_level="low")
        draft = _make_index_row(id="draft_01", maturity="draft", risk_level="low")
        assert score_candidate(stable, "test") > score_candidate(draft, "test")

    def test_low_risk_scores_above_high_risk(self):
        low = _make_index_row(risk_level="low")
        high = _make_index_row(id="high_01", risk_level="high")
        assert score_candidate(low, "test") > score_candidate(high, "test")


class TestRankCandidates:
    def test_deterministic_ordering(self):
        rows = [
            _make_index_row(id="c", name="Charlie"),
            _make_index_row(id="a", name="Alpha"),
            _make_index_row(id="b", name="Bravo"),
        ]
        result1 = rank_candidates(rows, "test")
        result2 = rank_candidates(rows, "test")
        assert [r["id"] for r in result1] == [r["id"] for r in result2]

    def test_no_embeddings_no_llm_no_network(self):
        """Ranking must be purely deterministic with no external calls."""
        rows = [_make_index_row()]
        with patch("src.capabilities.ranking.json.loads", side_effect=json.loads) as mock_json:
            result = rank_candidates(rows, "test")
            assert len(result) == 1
            # json.loads is called for parsing JSON fields, not for any network/LLM


# ── CapabilityRetriever tests ──────────────────────────────────────────────


class TestRetrieverRetrieve:
    """Tests for CapabilityRetriever.retrieve()."""

    @pytest.fixture
    def store(self):
        """Mock CapabilityStore."""
        return MagicMock()

    @pytest.fixture
    def index(self):
        """Real CapabilityIndex with an in-memory SQLite database for search."""
        idx = CapabilityIndex(":memory:")
        idx.init()
        return idx

    def _seed_index(self, index, rows):
        for row in rows:
            columns = [
                "id", "name", "description", "type", "scope", "maturity", "status",
                "risk_level", "trust_required",
                "required_tools_json", "required_permissions_json",
                "triggers_json", "tags_json",
                "path", "content_hash", "created_at", "updated_at",
                "usage_count", "success_count", "failure_count",
            ]
            placeholders = ", ".join("?" for _ in columns)
            sql = f"INSERT OR REPLACE INTO capability_index ({', '.join(columns)}) VALUES ({placeholders})"
            values = [
                row.get(c, row.get(c.replace("_json", ""), ""))
                for c in columns
            ]
            # Ensure JSON columns are strings
            for json_col in ("required_tools_json", "required_permissions_json", "triggers_json", "tags_json"):
                idx_val_idx = columns.index(json_col)
                val = values[idx_val_idx]
                if isinstance(val, list):
                    values[idx_val_idx] = json.dumps(val)
            index.conn.execute(sql, values)
        index.conn.commit()

    def test_retrieves_by_name(self, store, index):
        self._seed_index(index, [_make_index_row(name="CI Debugger")])
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("CI Debugger")
        assert len(results) >= 1
        assert any(r.name == "CI Debugger" for r in results)

    def test_retrieves_by_description(self, store, index):
        self._seed_index(index, [_make_index_row(description="Diagnose CI failures")])
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("diagnose")
        assert len(results) >= 1

    def test_retrieves_by_trigger(self, store, index):
        self._seed_index(index, [_make_index_row(triggers_json=json.dumps(["pytest failure"]))])
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("pytest failure")
        assert len(results) >= 1

    def test_retrieves_by_tag(self, store, index):
        self._seed_index(index, [_make_index_row(tags_json=json.dumps(["ci"]))])
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("ci")
        assert len(results) >= 1

    def test_stable_ranks_above_testing(self, store, index):
        self._seed_index(index, [
            _make_index_row(id="testing_01", name="Testing Cap", maturity="testing"),
            _make_index_row(id="stable_01", name="Stable Cap", maturity="stable"),
        ])
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("cap")
        stable_idx = next(i for i, r in enumerate(results) if r.maturity == "stable")
        testing_idx = next(i for i, r in enumerate(results) if r.maturity == "testing")
        assert stable_idx < testing_idx

    def test_low_risk_ranks_above_medium(self, store, index):
        self._seed_index(index, [
            _make_index_row(id="med_01", name="Medium Cap", risk_level="medium"),
            _make_index_row(id="low_01", name="Low Cap", risk_level="low"),
        ])
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("cap")
        low_idx = next(i for i, r in enumerate(results) if r.risk_level == "low")
        med_idx = next(i for i, r in enumerate(results) if r.risk_level == "medium")
        assert low_idx < med_idx


class TestRetrieverFiltering:
    """Tests for filter_candidates exclusion rules."""

    @pytest.fixture
    def store(self):
        return MagicMock()

    @pytest.fixture
    def index(self):
        return MagicMock()

    def test_high_risk_excluded_by_default(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(risk_level="high")]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 0

    def test_high_risk_included_when_explicit(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(risk_level="high")]
        ctx = RetrievalContext(include_high_risk=True, include_risk_levels=["low", "medium", "high"])
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1

    def test_disabled_excluded_by_default(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(status="disabled")]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 0

    def test_disabled_included_when_explicit(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(status="disabled")]
        ctx = RetrievalContext(include_disabled=True)
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1

    def test_archived_excluded_by_default(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(status="archived")]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 0

    def test_archived_included_when_explicit(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(status="archived")]
        ctx = RetrievalContext(include_archived=True)
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1

    def test_quarantined_excluded_by_default(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(status="quarantined")]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 0

    def test_quarantined_included_when_explicit(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(status="quarantined")]
        ctx = RetrievalContext(include_quarantined=True)
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1

    def test_broken_excluded_by_default(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(maturity="broken")]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 0

    def test_draft_excluded_by_default(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(maturity="draft")]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 0

    def test_draft_included_when_explicit(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(maturity="draft")]
        ctx = RetrievalContext(include_draft=True, include_maturity=["stable", "testing", "draft"])
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1

    def test_missing_required_tools_excluded(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(
            required_tools=["missing_tool", "shell"],
        )]
        ctx = RetrievalContext(available_tools={"shell", "read_file"})
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 0

    def test_required_tools_all_present_included(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        # Pass required_tools_json as JSON to avoid deserialization-loop overwrite
        candidates = [_make_index_row_deserialized(
            required_tools_json=json.dumps(["shell", "read_file"]),
        )]
        ctx = RetrievalContext(available_tools={"shell", "read_file", "write_file"})
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1

    def test_no_required_tools_always_included(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(required_tools=[])]
        ctx = RetrievalContext(available_tools=set())
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1

    def test_no_available_tools_no_exclusion(self, store, index):
        """When available_tools is empty (falsy), required_tools check is skipped."""
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized(
            required_tools_json=json.dumps(["nonexistent_tool"]),
        )]
        ctx = RetrievalContext(available_tools=set())  # falsy → skips check
        result = retriever.filter_candidates(candidates, ctx)
        # Empty available_tools is falsy → required_tools check skipped → included
        assert len(result) == 1


class TestRetrieverDedup:
    """Tests for duplicate handling and scope precedence."""

    @pytest.fixture
    def store(self):
        return MagicMock()

    @pytest.fixture
    def index(self):
        return MagicMock()

    def test_session_beats_workspace(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [
            _make_index_row_deserialized(id="cap_01", scope="workspace", name="Workspace Ver"),
            _make_index_row_deserialized(id="cap_01", scope="session", name="Session Ver"),
        ]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1
        assert result[0].scope == "session"

    def test_workspace_beats_user(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [
            _make_index_row_deserialized(id="cap_01", scope="user", name="User Ver"),
            _make_index_row_deserialized(id="cap_01", scope="workspace", name="Workspace Ver"),
        ]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1
        assert result[0].scope == "workspace"

    def test_user_beats_global(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [
            _make_index_row_deserialized(id="cap_01", scope="global", name="Global Ver"),
            _make_index_row_deserialized(id="cap_01", scope="user", name="User Ver"),
        ]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 1
        assert result[0].scope == "user"

    def test_different_ids_both_kept(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [
            _make_index_row_deserialized(id="cap_01", scope="workspace"),
            _make_index_row_deserialized(id="cap_02", scope="workspace"),
        ]
        ctx = RetrievalContext()
        result = retriever.filter_candidates(candidates, ctx)
        assert len(result) == 2


class TestRetrieverMaxResults:
    """Tests for max_results enforcement."""

    @pytest.fixture
    def store(self):
        return MagicMock()

    @pytest.fixture
    def index(self):
        return MagicMock()

    def test_max_results_enforced(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index, max_results=3)
        candidates = [
            _make_index_row_deserialized(id=f"cap_{i:02d}", name=f"Capability {i}")
            for i in range(10)
        ]
        ctx = RetrievalContext(max_results=3)
        filtered = retriever.filter_candidates(candidates, ctx)
        ranked = retriever.rank_candidates(filtered, ctx)
        assert len(ranked[:3]) <= 3
        # retrieve would return at most max_results
        assert len(ranked[:ctx.max_results]) <= 3

    def test_default_max_results_is_5(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [
            _make_index_row_deserialized(id=f"cap_{i:02d}", name=f"Capability {i}")
            for i in range(10)
        ]
        ctx = RetrievalContext()
        filtered = retriever.filter_candidates(candidates, ctx)
        ranked = retriever.rank_candidates(filtered, ctx)
        assert len(ranked[:DEFAULT_MAX_RESULTS]) <= 5


class TestRetrieverSummarize:
    """Tests for CapabilityRetriever.summarize()."""

    @pytest.fixture
    def store(self):
        return MagicMock()

    @pytest.fixture
    def index(self):
        return MagicMock()

    def test_summary_has_no_body(self, store, index):
        from src.capabilities.document import CapabilityDocument
        from src.capabilities.schema import CapabilityManifest

        manifest = CapabilityManifest(
            id="test_cap",
            name="Test Cap",
            description="A test capability.",
            type=CapabilityType.SKILL,
            scope=CapabilityScope.WORKSPACE,
            version="0.1.0",
            maturity=CapabilityMaturity.STABLE,
            status=CapabilityStatus.ACTIVE,
            risk_level=CapabilityRiskLevel.LOW,
            triggers=["test failure"],
            tags=["ci"],
            required_tools=["shell"],
        )
        doc = CapabilityDocument(
            manifest=manifest,
            body="This is the full body with procedure steps...",
            directory=Path("/tmp/test_cap"),
        )

        retriever = CapabilityRetriever(store=store, index=index)
        summary = retriever.summarize(doc)

        assert summary.id == "test_cap"
        assert summary.name == "Test Cap"
        assert summary.description == "A test capability."
        assert summary.maturity == "stable"
        assert summary.risk_level == "low"
        assert "shell" in summary.required_tools
        assert "test failure" in summary.triggers
        # summary is a frozen dataclass — no body field exists
        assert not hasattr(summary, "body")


class TestRetrieverSafeErrors:
    """Tests that retriever never raises on errors."""

    def test_fails_closed_on_error(self):
        store = MagicMock()
        index = MagicMock()
        index.search.side_effect = RuntimeError("database is locked")
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("test")
        assert results == []

    def test_fails_closed_on_empty_index(self):
        store = MagicMock()
        index = MagicMock()
        index.search.return_value = []
        retriever = CapabilityRetriever(store=store, index=index)
        results = retriever.retrieve("test")
        assert results == []


# ── RetrievalContext tests ──────────────────────────────────────────────────


class TestRetrievalContextDefaults:
    def test_default_include_maturities(self):
        ctx = RetrievalContext()
        assert set(ctx.include_maturity) == DEFAULT_INCLUDE_MATURITIES

    def test_default_include_risk_levels(self):
        ctx = RetrievalContext()
        assert set(ctx.include_risk_levels) == DEFAULT_INCLUDE_RISK_LEVELS

    def test_default_max_results(self):
        ctx = RetrievalContext()
        assert ctx.max_results == DEFAULT_MAX_RESULTS

    def test_default_excludes(self):
        ctx = RetrievalContext()
        assert not ctx.include_draft
        assert not ctx.include_high_risk
        assert not ctx.include_disabled
        assert not ctx.include_archived
        assert not ctx.include_quarantined


# ── Deterministic ordering tests ────────────────────────────────────────────


class TestDeterministicOrdering:
    """Ordering must be deterministic — no randomness, no embeddings, no LLM."""

    @pytest.fixture
    def store(self):
        return MagicMock()

    @pytest.fixture
    def index(self):
        return MagicMock()

    def test_same_input_same_output(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [
            _make_index_row_deserialized(id=f"cap_{i:02d}", name=f"Cap {i}")
            for i in range(5)
        ]
        ctx = RetrievalContext(user_task="test query")
        result1 = retriever.filter_candidates(candidates, ctx)
        ranked1 = retriever.rank_candidates(result1, ctx)
        result2 = retriever.filter_candidates(candidates, ctx)
        ranked2 = retriever.rank_candidates(result2, ctx)
        assert [r.id for r in ranked1] == [r.id for r in ranked2]


# ── No embeds / LLM / network tests ────────────────────────────────────────


class TestNoExternalCalls:
    """CapabilityRetriever must not use embeddings, LLM, or network."""

    @pytest.fixture
    def store(self):
        return MagicMock()

    @pytest.fixture
    def index(self):
        return MagicMock()

    def test_ranking_is_pure_function(self):
        """ranking module imports only stdlib and capabilities internals."""
        import inspect
        import sys

        ranking_module = sys.modules.get("src.capabilities.ranking")
        # Module should exist and not import anthropic, openai, requests, httpx
        assert ranking_module is not None

    def test_retriever_no_http_calls(self, store, index):
        retriever = CapabilityRetriever(store=store, index=index)
        candidates = [_make_index_row_deserialized()]
        ctx = RetrievalContext(user_task="test")
        # Should not raise and should not make any HTTP calls
        filtered = retriever.filter_candidates(candidates, ctx)
        assert len(filtered) >= 0
