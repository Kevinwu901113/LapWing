"""T-03: StateView injects compact agent summary into the system prompt.

Per Blueprint §9: StateViewBuilder gathers a compact agent list from
AgentRegistry, populates StateView.agent_summary, and StateSerializer
renders it inside the runtime-state block (before commitments).
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.core.state_view import StateView


def _make_attention_context():
    from src.core.state_view import AttentionContext
    from datetime import datetime, timezone

    return AttentionContext(
        now=datetime.now(timezone.utc),
        channel="desktop",
        actor_id=None,
        actor_name=None,
        auth_level=3,
        group_id=None,
        current_conversation=None,
        mode="idle",
        offline_hours=None,
    )


def _make_state_view(*, agent_summary: str | None = None) -> StateView:
    from src.core.state_view import (
        IdentityDocs, TrajectoryWindow, MemorySnippets,
    )

    return StateView(
        identity_docs=IdentityDocs(soul="", constitution="", voice=""),
        attention_context=_make_attention_context(),
        trajectory_window=TrajectoryWindow(turns=()),
        memory_snippets=MemorySnippets(snippets=()),
        commitments_active=(),
        agent_summary=agent_summary,
    )


def test_stateview_field_default_none():
    sv = _make_state_view()
    assert sv.agent_summary is None


def test_stateview_field_populated_when_set():
    sv = _make_state_view(agent_summary="可用 Agent:\n- researcher: builtin")
    assert sv.agent_summary == "可用 Agent:\n- researcher: builtin"


def test_serializer_renders_agent_summary_in_runtime_state_block():
    """T-03: serialized prompt contains the agent summary text."""
    from src.core.state_serializer import _render_runtime_state

    sv = _make_state_view(
        agent_summary=(
            "可用 Agent:\n"
            "- researcher: builtin, 搜索/浏览网页, 适合信息查找\n"
            "- coder: builtin, 文件读写/代码执行, 适合实现和调试"
        )
    )
    rendered = _render_runtime_state(sv)
    assert "可用 Agent:" in rendered
    assert "researcher" in rendered
    assert "coder" in rendered


def test_serializer_skips_block_when_summary_is_none():
    from src.core.state_serializer import _render_runtime_state

    sv = _make_state_view(agent_summary=None)
    rendered = _render_runtime_state(sv)
    assert "可用 Agent:" not in rendered


def test_builder_skips_when_no_registry_supplied():
    """StateViewBuilder.__init__ default keeps agent_summary as None."""
    from src.core.state_view_builder import StateViewBuilder

    builder = StateViewBuilder()
    assert builder._build_agent_summary() is None


def test_builder_calls_registry_render_method():
    """StateViewBuilder.__init__ accepts agent_registry; _build_agent_summary
    delegates to render_agent_summary_for_stateview()."""
    from src.core.state_view_builder import StateViewBuilder

    fake_registry = MagicMock()
    fake_registry.render_agent_summary_for_stateview.return_value = (
        "可用 Agent:\n- researcher: builtin, x"
    )
    builder = StateViewBuilder(agent_registry=fake_registry)
    summary = builder._build_agent_summary()
    assert summary is not None
    assert "可用 Agent:" in summary
    fake_registry.render_agent_summary_for_stateview.assert_called_once()


def test_builder_swallows_registry_exceptions():
    """If the registry raises (defensive), builder returns None — never breaks
    the StateView pipeline."""
    from src.core.state_view_builder import StateViewBuilder

    fake_registry = MagicMock()
    fake_registry.render_agent_summary_for_stateview.side_effect = RuntimeError("boom")
    builder = StateViewBuilder(agent_registry=fake_registry)
    assert builder._build_agent_summary() is None
