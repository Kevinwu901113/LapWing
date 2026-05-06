"""Phase 4 tests: StateView progressive disclosure for capabilities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.state_view import (
    CapabilitySummary as StateViewCapabilitySummary,
    IdentityDocs,
    StateView,
    TrajectoryTurn,
    TrajectoryWindow,
)
from src.core.state_view_builder import StateViewBuilder


# ── Helpers ────────────────────────────────────────────────────────────────


def _identity_docs() -> IdentityDocs:
    return IdentityDocs(soul="test soul", constitution="test constitution", voice="test voice")


def _trajectory_window() -> TrajectoryWindow:
    return TrajectoryWindow(
        turns=(
            TrajectoryTurn(role="user", content="How do I fix this CI failure?"),
            TrajectoryTurn(role="assistant", content="Let me help you debug."),
        )
    )


def _empty_trajectory_window() -> TrajectoryWindow:
    return TrajectoryWindow(turns=())


def _mock_retriever(summaries=None):
    """Create a duck-typed retriever mock."""
    m = MagicMock()
    m.retrieve.return_value = summaries or []
    return m


def _make_retriever_summary(**overrides):
    """Create a mock CapabilitySummary matching the retriever's output format."""
    from src.capabilities.retriever import CapabilitySummary

    defaults = {
        "id": "repo_ci_debugger",
        "name": "CI Debugger",
        "description": "Diagnose and fix CI/test failures.",
        "type": "skill",
        "scope": "workspace",
        "maturity": "stable",
        "status": "active",
        "risk_level": "medium",
        "trust_required": "developer",
        "triggers": ("CI/test failure", "pytest failure"),
        "tags": ("ci", "testing"),
        "required_tools": ("delegate_to_coder", "shell"),
        "match_reason": "trigger, name",
        "score": 15.0,
    }
    defaults.update(overrides)
    return CapabilitySummary(**defaults)


# ── Feature flag matrix ────────────────────────────────────────────────────


class TestFeatureFlagMatrix:
    """StateView capability section only appears when both flags are enabled."""

    @pytest.fixture
    def builder_base(self):
        """Builder with no retriever wired (capabilities disabled)."""
        return StateViewBuilder(
            soul_path=Path("/nonexistent/soul.md"),
            constitution_path=Path("/nonexistent/constitution.md"),
            voice_prompt_name="lapwing_voice",
        )

    async def test_no_retriever_no_section(self, builder_base):
        """When no retriever is wired, capability_summaries is empty."""
        view = await builder_base.build_for_chat("test_chat")
        assert view.capability_summaries == ()

    async def test_retriever_wired_but_no_query_returns_empty(self, builder_base):
        """When retriever is wired but trajectory is empty, capability_summaries is empty."""
        builder_base._capability_retriever = _mock_retriever([])

        # Override to use empty trajectory
        with patch.object(builder_base, '_build_trajectory_for_chat',
                          return_value=_empty_trajectory_window()):
            view = await builder_base.build_for_chat("test_chat")
            assert view.capability_summaries == ()

    async def test_retriever_wired_with_results(self, builder_base):
        """When retriever is wired and candidates exist, summaries appear."""
        summaries = [_make_retriever_summary()]
        builder_base._capability_retriever = _mock_retriever(summaries)

        with patch.object(builder_base, '_load_identity_docs', return_value=_identity_docs()):
            with patch.object(builder_base, '_build_trajectory_for_chat',
                              return_value=_trajectory_window()):
                view = await builder_base.build_for_chat("test_chat")
                assert len(view.capability_summaries) >= 1

    async def test_retriever_error_returns_empty(self, builder_base):
        """When retriever raises, capability_summaries is empty (fail closed)."""
        bad_retriever = MagicMock()
        bad_retriever.retrieve.side_effect = RuntimeError("database error")
        builder_base._capability_retriever = bad_retriever

        with patch.object(builder_base, '_load_identity_docs', return_value=_identity_docs()):
            with patch.object(builder_base, '_build_trajectory_for_chat',
                              return_value=_trajectory_window()):
                view = await builder_base.build_for_chat("test_chat")
                assert view.capability_summaries == ()


# ── Content verification ───────────────────────────────────────────────────


class TestContentVerification:
    """StateView capability summaries must never contain full content."""

    @pytest.fixture
    def builder(self):
        b = StateViewBuilder(
            soul_path=Path("/nonexistent/soul.md"),
            constitution_path=Path("/nonexistent/constitution.md"),
            voice_prompt_name="lapwing_voice",
        )
        b._capability_retriever = _mock_retriever([_make_retriever_summary()])
        return b

    async def test_summary_has_id_scope_maturity_risk(self, builder):
        with patch.object(builder, '_load_identity_docs', return_value=_identity_docs()):
            with patch.object(builder, '_build_trajectory_for_chat',
                              return_value=_trajectory_window()):
                view = await builder.build_for_chat("test_chat")
                assert len(view.capability_summaries) >= 1
                s = view.capability_summaries[0]
                assert s.id == "repo_ci_debugger"
                assert s.scope == "workspace"
                assert s.maturity == "stable"
                assert s.risk_level == "medium"

    async def test_summary_has_no_body_field(self, builder):
        """StateView CapabilitySummary dataclass has no body/procedure/scripts field."""
        with patch.object(builder, '_load_identity_docs', return_value=_identity_docs()):
            with patch.object(builder, '_build_trajectory_for_chat',
                              return_value=_trajectory_window()):
                view = await builder.build_for_chat("test_chat")
                assert len(view.capability_summaries) >= 1
                s = view.capability_summaries[0]
                assert not hasattr(s, "body")
                assert not hasattr(s, "procedure")
                assert not hasattr(s, "scripts")
                assert not hasattr(s, "traces")
                assert not hasattr(s, "evals")
                assert not hasattr(s, "version_contents")

    async def test_summary_has_triggers_and_required_tools(self, builder):
        with patch.object(builder, '_load_identity_docs', return_value=_identity_docs()):
            with patch.object(builder, '_build_trajectory_for_chat',
                              return_value=_trajectory_window()):
                view = await builder.build_for_chat("test_chat")
                s = view.capability_summaries[0]
                assert "CI/test failure" in s.triggers
                assert "shell" in s.required_tools

    async def test_summary_has_match_reason(self, builder):
        with patch.object(builder, '_load_identity_docs', return_value=_identity_docs()):
            with patch.object(builder, '_build_trajectory_for_chat',
                              return_value=_trajectory_window()):
                view = await builder.build_for_chat("test_chat")
                s = view.capability_summaries[0]
                assert s.match_reason


# ── StateView existing fields unchanged ────────────────────────────────────


class TestExistingFieldsUnchanged:
    """Adding capability_summaries must not break existing StateView fields."""

    async def test_stateview_construction_with_capabilities(self):
        """StateView can be constructed with capability_summaries."""
        view = StateView(
            identity_docs=_identity_docs(),
            attention_context=MagicMock(),
            trajectory_window=_trajectory_window(),
            memory_snippets=MagicMock(),
            commitments_active=(),
            capability_summaries=(
                StateViewCapabilitySummary(
                    id="test_cap",
                    name="Test Cap",
                    description="A test capability.",
                    type="skill",
                    scope="workspace",
                    maturity="stable",
                    risk_level="low",
                    triggers=("test",),
                    required_tools=("shell",),
                    match_reason="name",
                ),
            ),
        )
        assert len(view.capability_summaries) == 1
        assert view.capability_summaries[0].id == "test_cap"

    async def test_stateview_construction_without_capabilities(self):
        """StateView can be constructed without capability_summaries (default empty)."""
        view = StateView(
            identity_docs=_identity_docs(),
            attention_context=MagicMock(),
            trajectory_window=_trajectory_window(),
            memory_snippets=MagicMock(),
            commitments_active=(),
        )
        assert view.capability_summaries == ()

    async def test_existing_fields_preserved(self):
        """All existing StateView fields work as before."""
        attn = MagicMock()
        traj = _trajectory_window()
        mem = MagicMock()
        view = StateView(
            identity_docs=_identity_docs(),
            attention_context=attn,
            trajectory_window=traj,
            memory_snippets=mem,
            commitments_active=(),
            skill_summary=None,
            time_context=None,
            ambient_entries=(),
            focus_context=None,
            agent_summary=None,
            capability_summaries=(),
        )
        assert view.identity_docs.soul == "test soul"
        assert view.attention_context is attn
        assert view.trajectory_window is traj
        assert view.memory_snippets is mem


# ── Build for inner tests ──────────────────────────────────────────────────


class TestBuildForInner:
    """Progressive disclosure also works for the inner loop."""

    async def test_inner_loop_retrieves_capabilities(self):
        builder = StateViewBuilder(
            soul_path=Path("/nonexistent/soul.md"),
            constitution_path=Path("/nonexistent/constitution.md"),
            voice_prompt_name="lapwing_voice",
        )
        builder._capability_retriever = _mock_retriever([_make_retriever_summary()])

        with patch.object(builder, '_load_identity_docs', return_value=_identity_docs()):
            with patch.object(builder, '_build_trajectory_for_inner',
                              return_value=_trajectory_window()):
                view = await builder.build_for_inner()
                assert len(view.capability_summaries) >= 1


# ── CapabilitySummary dataclass tests ──────────────────────────────────────


class TestCapabilitySummaryDataclass:
    def test_is_frozen(self):
        s = StateViewCapabilitySummary(
            id="test", name="Test", description="Desc",
            type="skill", scope="workspace", maturity="stable", risk_level="low",
        )
        with pytest.raises(Exception):
            s.id = "changed"

    def test_defaults(self):
        s = StateViewCapabilitySummary(
            id="test", name="Test", description="Desc",
            type="skill", scope="workspace", maturity="stable", risk_level="low",
        )
        assert s.triggers == ()
        assert s.required_tools == ()
        assert s.match_reason == ""
